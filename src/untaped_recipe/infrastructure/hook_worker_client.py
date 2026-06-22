"""Client for uv hook worker processes."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Condition, Lock, Thread
from typing import Protocol, TextIO

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

from untaped_recipe import hook_worker
from untaped_recipe import worker_protocol as protocol
from untaped_recipe.infrastructure.hook_resolver import UvHookRef


class HookWorkerResponse(BaseModel):
    """Engine-side validation for one worker protocol response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: StrictStr
    ok: StrictBool
    result: object | None = None
    error: StrictStr | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> HookWorkerResponse:
        if self.ok and self.error is not None:
            raise ValueError("successful hook response cannot include error")
        if not self.ok and not self.error:
            raise ValueError("failed hook response must include error")
        if not self.ok and self.result is not None:
            raise ValueError("failed hook response cannot include result")
        return self


class FatalHookWorkerError(ValueError):
    """Raised when a worker process cannot safely be reused."""


@dataclass(frozen=True)
class HookWorkerCallResult:
    """Result plus diagnostics captured from one worker request."""

    result: object
    diagnostics: str


class HookWorkerClient(Protocol):
    """Request/response transport for external hook execution."""

    def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
        """Send one hook request and return the validated result."""


class UvHookWorkerPool:
    """Pool uv worker processes by hook project."""

    def __init__(
        self,
        *,
        max_workers_per_project: int = 1,
        hook_timeout_seconds: float = 60,
    ) -> None:
        self._max_workers_per_project = max(max_workers_per_project, 1)
        if hook_timeout_seconds < 0:
            raise ValueError("hook timeout must be greater than or equal to 0")
        self._hook_timeout_seconds = hook_timeout_seconds
        self._groups: dict[Path, _UvHookWorkerGroup] = {}
        self._lock = Lock()

    def __enter__(self) -> UvHookWorkerPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
        """Send one serialized request to the hook project's worker."""
        group = self._group_for(ref.project_root)
        return group.request({**payload, protocol.MODULE: ref.module})

    def request_with_diagnostics(
        self, ref: UvHookRef, payload: dict[str, object]
    ) -> HookWorkerCallResult:
        """Send one serialized request and return successful worker diagnostics."""
        group = self._group_for(ref.project_root)
        return group.request_with_diagnostics({**payload, protocol.MODULE: ref.module})

    def close(self) -> None:
        """Stop all worker processes."""
        with self._lock:
            groups = tuple(self._groups.values())
            self._groups.clear()
        for group in groups:
            group.close()

    def _group_for(self, project_root: Path) -> _UvHookWorkerGroup:
        resolved = project_root.resolve()
        with self._lock:
            group = self._groups.get(resolved)
            if group is None:
                group = _UvHookWorkerGroup(
                    resolved,
                    self._max_workers_per_project,
                    self._hook_timeout_seconds,
                )
                self._groups[resolved] = group
            return group


class _UvHookWorkerGroup:
    """Lazy bounded worker group for one hook project."""

    def __init__(self, project_root: Path, max_workers: int, hook_timeout_seconds: float) -> None:
        self._project_root = project_root
        self._max_workers = max(max_workers, 1)
        self._hook_timeout_seconds = hook_timeout_seconds
        self._workers: list[UvHookWorker] = []
        self._idle: list[UvHookWorker] = []
        self._condition = Condition()
        self._closed = False

    def request(self, payload: dict[str, object]) -> object:
        """Lease one serialized worker for a request."""
        return self._request(payload, capture_diagnostics=False).result

    def request_with_diagnostics(self, payload: dict[str, object]) -> HookWorkerCallResult:
        """Lease one serialized worker and return successful diagnostics."""
        return self._request(payload, capture_diagnostics=True)

    def _request(
        self,
        payload: dict[str, object],
        *,
        capture_diagnostics: bool,
    ) -> HookWorkerCallResult:
        worker = self._lease()
        try:
            if capture_diagnostics:
                return worker.request_with_diagnostics(payload)
            return HookWorkerCallResult(result=worker.request(payload), diagnostics="")
        except FatalHookWorkerError:
            self._retire(worker)
            raise
        finally:
            self._release(worker)

    def close(self) -> None:
        """Close every worker in the group."""
        with self._condition:
            self._closed = True
            workers = tuple(self._workers)
            self._workers.clear()
            self._idle.clear()
            self._condition.notify_all()
        for worker in workers:
            worker.close()

    def _retire(self, worker: UvHookWorker) -> None:
        with self._condition:
            if worker in self._workers:
                self._workers.remove(worker)
            if worker in self._idle:
                self._idle.remove(worker)
            self._condition.notify_all()
        worker.close()

    def _lease(self) -> UvHookWorker:
        with self._condition:
            while not self._closed:
                if self._idle:
                    return self._idle.pop()
                if len(self._workers) < self._max_workers:
                    break
                self._condition.wait()
            if self._closed:
                raise FatalHookWorkerError("hook worker pool is closed")
            try:
                worker = UvHookWorker(
                    self._project_root,
                    hook_timeout_seconds=self._hook_timeout_seconds,
                )
            except Exception:
                self._condition.notify_all()
                raise
            self._workers.append(worker)
            return worker

    def _release(self, worker: UvHookWorker) -> None:
        with self._condition:
            if worker in self._workers and not self._closed:
                self._idle.append(worker)
            self._condition.notify()


class UvHookWorker:
    """Long-lived worker process for one uv hook project."""

    def __init__(self, project_root: Path, *, hook_timeout_seconds: float = 60) -> None:
        if hook_timeout_seconds < 0:
            raise ValueError("hook timeout must be greater than or equal to 0")
        self._project_root = project_root
        self._hook_timeout_seconds = hook_timeout_seconds
        self._lock = Lock()
        self._next_id = 0
        self._stderr: Queue[str] = Queue()
        self._stdout: Queue[str | None] = Queue()
        self._process = self._start()
        if self._process.stdout is not None:
            self._stdout_thread = Thread(
                target=_drain_stdout,
                args=(self._process.stdout, self._stdout),
                daemon=True,
            )
            self._stdout_thread.start()
        if self._process.stderr is not None:
            self._stderr_thread = Thread(
                target=_drain_stderr,
                args=(self._process.stderr, self._stderr),
                daemon=True,
            )
            self._stderr_thread.start()

    def request(self, payload: dict[str, object]) -> object:
        """Send one request over NDJSON and return its result."""
        return self._request(payload, capture_diagnostics=False).result

    def request_with_diagnostics(self, payload: dict[str, object]) -> HookWorkerCallResult:
        """Send one request over NDJSON and return its result plus diagnostics."""
        return self._request(payload, capture_diagnostics=True)

    def _request(
        self,
        payload: dict[str, object],
        *,
        capture_diagnostics: bool,
    ) -> HookWorkerCallResult:
        with self._lock:
            diagnostic_limit = None if capture_diagnostics else 4000
            self._next_id += 1
            request_id = str(self._next_id)
            request = {protocol.ID: request_id, **payload}
            try:
                line = json.dumps(request, default=str) + "\n"
            except TypeError as exc:
                raise ValueError(f"hook request is not JSON serializable: {exc}") from exc
            stdin = self._process.stdin
            stdout = self._process.stdout
            if stdin is None or stdout is None:
                raise FatalHookWorkerError("hook worker was not started with pipes")
            try:
                stdin.write(line)
                stdin.flush()
            except BrokenPipeError as exc:
                message = self._failure_message(
                    "hook worker exited before request",
                    diagnostic_limit=diagnostic_limit,
                )
                raise FatalHookWorkerError(message) from exc
            response_line = self._read_response_line(diagnostic_limit=diagnostic_limit)
            if not response_line:
                raise FatalHookWorkerError(
                    self._failure_message(
                        "hook worker exited before response",
                        diagnostic_limit=diagnostic_limit,
                    )
                )
            try:
                response = HookWorkerResponse.model_validate_json(response_line)
            except ValueError as exc:
                raise FatalHookWorkerError(
                    self._failure_message(
                        f"malformed hook worker response: {response_line!r}",
                        diagnostic_limit=diagnostic_limit,
                    )
                ) from exc
            if response.id != request_id:
                raise FatalHookWorkerError(
                    self._failure_message(
                        "hook worker response id mismatch: "
                        f"expected {request_id}, got {response.id}",
                        diagnostic_limit=diagnostic_limit,
                    )
                )
            if not response.ok:
                raise ValueError(
                    self._failure_message(
                        response.error or "hook worker failed",
                        diagnostic_limit=diagnostic_limit,
                    )
                )
            diagnostics = self._drain_diagnostics(
                limit=diagnostic_limit,
                settle_seconds=0.05 if capture_diagnostics else 0,
            )
            return HookWorkerCallResult(
                result=response.result,
                diagnostics=diagnostics if capture_diagnostics else "",
            )

    def _read_response_line(self, *, diagnostic_limit: int | None) -> str | None:
        try:
            if self._hook_timeout_seconds == 0:
                return self._stdout.get()
            return self._stdout.get(timeout=self._hook_timeout_seconds)
        except Empty as exc:
            message = self._failure_message(
                f"hook worker timed out after {self._hook_timeout_seconds:g}s",
                diagnostic_limit=diagnostic_limit,
            )
            self.close()
            raise FatalHookWorkerError(message) from exc

    def close(self) -> None:
        """Terminate the worker process."""
        if self._process.stdin is not None:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)

    def _start(self) -> subprocess.Popen[str]:
        worker_path = Path(hook_worker.__file__).resolve()
        env = os.environ.copy()
        project_src = str(self._project_root / "src")
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            project_src
            if not existing_pythonpath
            else os.pathsep.join([project_src, existing_pythonpath])
        )
        try:
            return subprocess.Popen(
                [
                    "uv",
                    "run",
                    "--project",
                    str(self._project_root),
                    "--locked",
                    "python",
                    str(worker_path),
                ],
                cwd=self._project_root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ValueError("uv executable not found for hook project execution") from exc

    def _failure_message(self, message: str, *, diagnostic_limit: int | None = 4000) -> str:
        diagnostics = self._drain_diagnostics(
            limit=diagnostic_limit,
            settle_seconds=0.05 if diagnostic_limit is None else 0,
        )
        if diagnostics:
            return f"{message}\n{diagnostics}"
        return message

    def _discard_diagnostics(self) -> None:
        self._drain_diagnostics(limit=4000)

    def _drain_diagnostics(
        self,
        *,
        limit: int | None = 4000,
        settle_seconds: float = 0,
    ) -> str:
        lines: list[str] = []
        size = 0
        deadline = time.monotonic() + max(settle_seconds, 0)
        while True:
            try:
                line = self._stderr.get_nowait()
            except Empty:
                if time.monotonic() >= deadline:
                    break
                try:
                    line = self._stderr.get(timeout=min(0.01, deadline - time.monotonic()))
                except Empty:
                    continue
            lines.append(line)
            size += len(line)
            while limit is not None and size > limit and lines:
                size -= len(lines.pop(0))
        return "".join(lines).strip()


def _drain_stderr(stream: TextIO, queue: Queue[str]) -> None:
    for line in stream:
        queue.put(str(line))


def _drain_stdout(stream: TextIO, queue: Queue[str | None]) -> None:
    while True:
        line = stream.readline()
        if line == "":
            queue.put(None)
            return
        queue.put(str(line))
