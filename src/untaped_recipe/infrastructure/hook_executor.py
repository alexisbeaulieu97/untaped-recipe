"""Execute resolved hooks in-process for built-ins or through uv workers."""

from __future__ import annotations

import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from untaped_recipe import worker_protocol as protocol
from untaped_recipe.application.ports import HookHelpersPort
from untaped_recipe.domain.hook_project import HookKind
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.hook_resolver import BuiltinHookRef, HookRef, HookResolver
from untaped_recipe.infrastructure.hook_worker_client import (
    HookWorkerCallResult,
    HookWorkerClient,
)


@dataclass(frozen=True)
class HookDebugResult[T]:
    """Hook result plus diagnostics captured for one debug invocation."""

    result: T
    diagnostics: str


class HookExecutionError(RuntimeError):
    """Raised when a debug hook invocation fails inside hook code."""


class HookExecutor:
    """Dispatch hook calls through the correct runtime."""

    def __init__(
        self,
        resolver: HookResolver,
        *,
        workers: HookWorkerClient,
        helpers: HookHelpersPort,
    ) -> None:
        self._resolver = resolver
        self._workers = workers
        self._helpers = helpers

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> str:
        """Run a transform hook and return replacement content."""
        return self._transform(
            hook,
            content,
            local_hook_project=local_hook_project,
            target=target,
            file=file,
            inputs=inputs,
            args=args,
            capture_diagnostics=False,
        ).result

    def transform_for_debug(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> HookDebugResult[str]:
        """Run a transform hook and return successful diagnostics for debugging."""
        return self._transform(
            hook,
            content,
            local_hook_project=local_hook_project,
            target=target,
            file=file,
            inputs=inputs,
            args=args,
            capture_diagnostics=True,
        )

    def _transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool,
    ) -> HookDebugResult[str]:
        ref = self._resolver.resolve(hook, local_hook_project)
        _ensure_kind(ref, hook, expected="transform")
        if isinstance(ref, BuiltinHookRef):
            execution = _call_builtin_for_debug(
                lambda: _call_builtin_transform(
                    ref,
                    hook,
                    content,
                    inputs=inputs,
                    target=target,
                    file=file,
                    args=args,
                    helpers=self._helpers,
                ),
                capture_diagnostics=capture_diagnostics,
            )
            result = execution.result
            diagnostics = execution.diagnostics
        else:
            execution = _request_external_for_debug(
                self._workers,
                ref,
                _transform_payload(
                    content,
                    target=target,
                    file=file,
                    inputs=inputs,
                    args=args,
                ),
                capture_diagnostics=capture_diagnostics,
            )
            result = execution.result
            diagnostics = execution.diagnostics
        if not isinstance(result, str):
            raise ValueError(f"transform hook {hook!r} must return str")
        return HookDebugResult(result=result, diagnostics=diagnostics)

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> Verdict:
        """Run a validate hook and coerce its verdict."""
        return self._validate(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
            capture_diagnostics=False,
        ).result

    def validate_for_debug(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> HookDebugResult[Verdict]:
        """Run a validate hook and return successful diagnostics for debugging."""
        return self._validate(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
            capture_diagnostics=True,
        )

    def _validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool,
    ) -> HookDebugResult[Verdict]:
        ref = self._resolver.resolve(hook, local_hook_project)
        _ensure_kind(ref, hook, expected="validate")
        if isinstance(ref, BuiltinHookRef):
            execution = _call_builtin_for_debug(
                lambda: _call_builtin_validate(
                    ref,
                    hook,
                    inputs=inputs,
                    target=target,
                    args=args,
                    helpers=self._helpers,
                ),
                capture_diagnostics=capture_diagnostics,
            )
            result = execution.result
            diagnostics = execution.diagnostics
        else:
            execution = _request_external_for_debug(
                self._workers,
                ref,
                {
                    protocol.KIND: protocol.VALIDATE,
                    protocol.INPUTS: inputs,
                    protocol.TARGET: str(target),
                    protocol.ARGS: args,
                },
                capture_diagnostics=capture_diagnostics,
            )
            result = execution.result
            diagnostics = execution.diagnostics
        return HookDebugResult(result=_coerce_verdict(result), diagnostics=diagnostics)


def _ensure_kind(ref: HookRef, hook: str, *, expected: HookKind) -> None:
    if ref.kind != expected:
        raise ValueError(f"{expected} step hook {hook!r} resolves to {ref.kind} hook")


def _transform_payload(
    content: str,
    *,
    target: Path,
    file: Path,
    inputs: dict[str, object],
    args: dict[str, object],
) -> dict[str, object]:
    return {
        protocol.KIND: protocol.TRANSFORM,
        protocol.CONTENT: content,
        protocol.INPUTS: inputs,
        protocol.TARGET: str(target),
        protocol.FILE: str(file),
        protocol.ARGS: args,
    }


def _call_builtin_transform(
    ref: BuiltinHookRef,
    hook: str,
    content: str,
    *,
    inputs: dict[str, object],
    target: Path,
    file: Path,
    args: dict[str, object],
    helpers: HookHelpersPort,
) -> object:
    transform = getattr(ref.module, "transform", None)
    if transform is None:
        raise ValueError(f"transform hook {hook!r} has no transform callable")
    return transform(
        content,
        inputs=inputs,
        target=target,
        file=file,
        args=args,
        helpers=helpers,
    )


def _call_builtin_validate(
    ref: BuiltinHookRef,
    hook: str,
    *,
    inputs: dict[str, object],
    target: Path,
    args: dict[str, object],
    helpers: HookHelpersPort,
) -> object:
    validate = getattr(ref.module, "validate", None)
    if validate is None:
        raise ValueError(f"validate hook {hook!r} has no validate callable")
    return validate(inputs=inputs, target=target, args=args, helpers=helpers)


def _call_builtin_for_debug(
    call: object,
    *,
    capture_diagnostics: bool,
) -> HookDebugResult[object]:
    if not callable(call):
        raise TypeError("call must be callable")
    if not capture_diagnostics:
        return HookDebugResult(result=call(), diagnostics="")
    stdout = StringIO()
    try:
        with redirect_stdout(stdout):
            result = call()
    except Exception as exc:
        raise HookExecutionError(traceback.format_exc().rstrip()) from exc
    return HookDebugResult(result=result, diagnostics=stdout.getvalue().strip())


def _request_external_for_debug(
    workers: HookWorkerClient,
    ref: HookRef,
    payload: dict[str, object],
    *,
    capture_diagnostics: bool,
) -> HookDebugResult[object]:
    if isinstance(ref, BuiltinHookRef):
        raise TypeError("external worker request requires a uv hook ref")
    if not capture_diagnostics:
        return HookDebugResult(result=workers.request(ref, payload), diagnostics="")
    request_with_diagnostics = getattr(workers, "request_with_diagnostics", None)
    if not callable(request_with_diagnostics):
        return HookDebugResult(result=workers.request(ref, payload), diagnostics="")
    try:
        worker_result: HookWorkerCallResult = request_with_diagnostics(ref, payload)
    except Exception as exc:
        raise HookExecutionError(str(exc)) from exc
    return HookDebugResult(
        result=worker_result.result,
        diagnostics=worker_result.diagnostics,
    )


def _coerce_verdict(value: object) -> Verdict:
    if isinstance(value, Verdict):
        return value
    if isinstance(value, dict):
        return Verdict.model_validate(value)
    if value is None:
        return Verdict(status="pass")
    if isinstance(value, str):
        return Verdict(status="fail", message=value)
    raise ValueError(f"invalid validate verdict: {value!r}")
