"""Client for uv hook worker processes."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
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


class HookWorkerClient(Protocol):
    """Request/response transport for external hook execution."""

    def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
        """Send one hook request and return the validated result."""


class UvHookWorkerPool:
    """Pool uv worker processes by hook project."""

    def __init__(self, *, max_workers_per_project: int = 1) -> None:
        self._max_workers_per_project = max(max_workers_per_project, 1)
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
                group = _UvHookWorkerGroup(resolved, self._max_workers_per_project)
                self._groups[resolved] = group
            return group


class _UvHookWorkerGroup:
    """Lazy bounded worker group for one hook project."""

    def __init__(self, project_root: Path, max_workers: int) -> None:
        self._project_root = project_root
        self._max_workers = max(max_workers, 1)
        self._workers: list[UvHookWorker] = []
        self._idle: Queue[UvHookWorker] = Queue()
        self._lock = Lock()

    def request(self, payload: dict[str, object]) -> object:
        """Lease one serialized worker for a request."""
        worker = self._lease()
        try:
            return worker.request(payload)
        except FatalHookWorkerError:
            self._retire(worker)
            raise
        finally:
            if worker in self._workers:
                self._idle.put(worker)

    def close(self) -> None:
        """Close every worker in the group."""
        with self._lock:
            workers = tuple(self._workers)
            self._workers.clear()
            while True:
                try:
                    self._idle.get_nowait()
                except Empty:
                    break
        for worker in workers:
            worker.close()

    def _retire(self, worker: UvHookWorker) -> None:
        with self._lock:
            if worker in self._workers:
                self._workers.remove(worker)
        worker.close()

    def _lease(self) -> UvHookWorker:
        try:
            return self._idle.get_nowait()
        except Empty:
            pass
        with self._lock:
            if len(self._workers) < self._max_workers:
                worker = UvHookWorker(self._project_root)
                self._workers.append(worker)
                return worker
        return self._idle.get()


class UvHookWorker:
    """Long-lived worker process for one uv hook project."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._lock = Lock()
        self._next_id = 0
        self._stderr: Queue[str] = Queue()
        self._process = self._start()
        if self._process.stderr is not None:
            self._stderr_thread = Thread(
                target=_drain_stderr,
                args=(self._process.stderr, self._stderr),
                daemon=True,
            )
            self._stderr_thread.start()

    def request(self, payload: dict[str, object]) -> object:
        """Send one request over NDJSON and return its result."""
        with self._lock:
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
                message = self._failure_message("hook worker exited before request")
                raise FatalHookWorkerError(message) from exc
            response_line = stdout.readline()
            if not response_line:
                raise FatalHookWorkerError(
                    self._failure_message("hook worker exited before response")
                )
            try:
                response = HookWorkerResponse.model_validate_json(response_line)
            except ValueError as exc:
                raise FatalHookWorkerError(
                    self._failure_message(f"malformed hook worker response: {response_line!r}")
                ) from exc
            if response.id != request_id:
                raise FatalHookWorkerError(
                    self._failure_message(
                        "hook worker response id mismatch: "
                        f"expected {request_id}, got {response.id}"
                    )
                )
            if not response.ok:
                raise ValueError(self._failure_message(response.error or "hook worker failed"))
            self._discard_diagnostics()
            return response.result

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

    def _failure_message(self, message: str) -> str:
        diagnostics = self._drain_diagnostics()
        if diagnostics:
            return f"{message}\n{diagnostics}"
        return message

    def _discard_diagnostics(self) -> None:
        self._drain_diagnostics()

    def _drain_diagnostics(self) -> str:
        lines: list[str] = []
        size = 0
        while True:
            try:
                line = self._stderr.get_nowait()
                lines.append(line)
                size += len(line)
                while size > 4000 and lines:
                    size -= len(lines.pop(0))
            except Empty:
                break
        return "".join(lines).strip()


def _drain_stderr(stream: TextIO, queue: Queue[str]) -> None:
    for line in stream:
        queue.put(str(line))
