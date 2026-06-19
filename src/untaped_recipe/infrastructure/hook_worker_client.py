"""Client for uv hook worker processes."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Protocol, TextIO

from pydantic import BaseModel, ConfigDict, model_validator

from untaped_recipe import hook_worker
from untaped_recipe.infrastructure.hook_resolver import UvHookRef


class HookWorkerResponse(BaseModel):
    """Engine-side validation for one worker protocol response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    ok: bool
    result: object | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> HookWorkerResponse:
        if self.ok and self.error is not None:
            raise ValueError("successful hook response cannot include error")
        if not self.ok and not self.error:
            raise ValueError("failed hook response must include error")
        return self


class HookWorkerClient(Protocol):
    """Request/response transport for external hook execution."""

    def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
        """Send one hook request and return the validated result."""


class UvHookWorkerPool:
    """One uv worker process per hook project."""

    def __init__(self) -> None:
        self._workers: dict[Path, UvHookWorker] = {}
        self._lock = Lock()

    def __enter__(self) -> UvHookWorkerPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
        """Send one serialized request to the hook project's worker."""
        worker = self._worker_for(ref.project_root)
        return worker.request({**payload, "module": ref.module})

    def close(self) -> None:
        """Stop all worker processes."""
        with self._lock:
            workers = tuple(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.close()

    def _worker_for(self, project_root: Path) -> UvHookWorker:
        resolved = project_root.resolve()
        with self._lock:
            worker = self._workers.get(resolved)
            if worker is None:
                worker = UvHookWorker(resolved)
                self._workers[resolved] = worker
            return worker


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
            request = {"id": request_id, **payload}
            try:
                line = json.dumps(request) + "\n"
            except TypeError as exc:
                raise ValueError(f"hook request is not JSON serializable: {exc}") from exc
            stdin = self._process.stdin
            stdout = self._process.stdout
            if stdin is None or stdout is None:
                raise ValueError("hook worker was not started with pipes")
            try:
                stdin.write(line)
                stdin.flush()
            except BrokenPipeError as exc:
                message = self._failure_message("hook worker exited before request")
                raise ValueError(message) from exc
            response_line = stdout.readline()
            if not response_line:
                raise ValueError(self._failure_message("hook worker exited before response"))
            try:
                response = HookWorkerResponse.model_validate_json(response_line)
            except ValueError as exc:
                raise ValueError(
                    self._failure_message(f"malformed hook worker response: {response_line!r}")
                ) from exc
            if response.id != request_id:
                raise ValueError(
                    self._failure_message(
                        "hook worker response id mismatch: "
                        f"expected {request_id}, got {response.id}"
                    )
                )
            if not response.ok:
                raise ValueError(self._failure_message(response.error or "hook worker failed"))
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

    def _drain_diagnostics(self) -> str:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._stderr.get_nowait())
            except Empty:
                break
        return "".join(lines).strip()


def _drain_stderr(stream: TextIO, queue: Queue[str]) -> None:
    for line in stream:
        queue.put(str(line))
