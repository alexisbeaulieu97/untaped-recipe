"""Execute resolved hooks in-process for built-ins or through uv workers."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from untaped_recipe import worker_protocol as protocol
from untaped_recipe.application.ports import HookDebugResult, HookHelpersPort
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_resolver import (
    BuiltinHookRef,
    HookResolver,
    UvHookRef,
    ensure_hook_supports,
)
from untaped_recipe.infrastructure.hook_worker_client import (
    APPLY_DIAGNOSTIC_LIMIT,
    DEBUG_DIAGNOSTIC_LIMIT,
    DEBUG_DIAGNOSTIC_SETTLE_SECONDS,
    HookWorkerClient,
)


class HookExecutionError(RuntimeError):
    """Raised when a debug hook invocation fails inside hook code."""


class HookExecutor:
    """Dispatch hook calls through the correct runtime."""

    def __init__(
        self,
        resolver: HookResolver,
        *,
        workers: HookWorkerClient,
        helpers_factory: Callable[[], HookHelpersPort] = HookHelpers,
    ) -> None:
        self._resolver = resolver
        self._workers = workers
        # A fresh helpers instance is built per builtin call so warn()
        # accumulation is isolated per target (and per planning thread).
        self._helpers_factory = helpers_factory

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
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        """Run a transform hook and return replacement content plus diagnostics."""
        return self._transform(
            hook,
            content,
            local_hook_project=local_hook_project,
            target=target,
            file=file,
            inputs=inputs,
            args=args,
            capture_diagnostics=capture_diagnostics,
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
        ensure_hook_supports(ref, hook, verb="transform")
        if isinstance(ref, BuiltinHookRef):
            helpers = self._helpers_factory()
            execution = _call_builtin_with_capture(
                lambda: _call_builtin_transform(
                    ref,
                    hook,
                    content,
                    inputs=inputs,
                    target=target,
                    file=file,
                    args=args,
                    helpers=helpers,
                ),
                capture_diagnostics=capture_diagnostics,
            )
            result = execution.result
            diagnostics = execution.diagnostics
            warnings = helpers.drain_warnings()
        else:
            execution = _request_external(
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
            warnings = execution.warnings
        if not isinstance(result, str):
            raise ValueError(f"transform hook {hook!r} must return str")
        return HookDebugResult(result=result, diagnostics=diagnostics, warnings=warnings)

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        """Run a validate hook and return its coerced verdict plus diagnostics."""
        return self._validate(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
            capture_diagnostics=capture_diagnostics,
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
        ensure_hook_supports(ref, hook, verb="validate")
        if isinstance(ref, BuiltinHookRef):
            helpers = self._helpers_factory()
            execution = _call_builtin_with_capture(
                lambda: _call_builtin_validate(
                    ref,
                    hook,
                    inputs=inputs,
                    target=target,
                    args=args,
                    helpers=helpers,
                ),
                capture_diagnostics=capture_diagnostics,
            )
            result = execution.result
            diagnostics = execution.diagnostics
            warnings = helpers.drain_warnings()
        else:
            execution = _request_external(
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
            warnings = execution.warnings
        # A legacy `{"status": "warn"}` verdict (or an old hook returning
        # helpers.warn(...) as its verdict) is accepted for this release and
        # mapped to pass + an accumulated warning; documented as deprecated.
        verdict, legacy_warnings = _coerce_verdict(result)
        return HookDebugResult(
            result=verdict,
            diagnostics=diagnostics,
            warnings=warnings + legacy_warnings,
        )


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


def _call_builtin_with_capture(
    call: Callable[[], object],
    *,
    capture_diagnostics: bool,
) -> HookDebugResult[object]:
    if not capture_diagnostics:
        return HookDebugResult(result=call(), diagnostics="")
    stdout = StringIO()
    try:
        with redirect_stdout(stdout):
            result = call()
    except Exception as exc:
        raise HookExecutionError(traceback.format_exc().rstrip()) from exc
    return HookDebugResult(result=result, diagnostics=stdout.getvalue().strip())


def _request_external(
    workers: HookWorkerClient,
    ref: UvHookRef,
    payload: dict[str, object],
    *,
    capture_diagnostics: bool,
) -> HookDebugResult[object]:
    diagnostic_limit = DEBUG_DIAGNOSTIC_LIMIT if capture_diagnostics else APPLY_DIAGNOSTIC_LIMIT
    settle_seconds = DEBUG_DIAGNOSTIC_SETTLE_SECONDS if capture_diagnostics else 0
    try:
        worker_result = workers.request(
            ref,
            payload,
            diagnostic_limit=diagnostic_limit,
            settle_seconds=settle_seconds,
        )
    except Exception as exc:
        if not capture_diagnostics:
            raise
        raise HookExecutionError(str(exc)) from exc
    return HookDebugResult(
        result=worker_result.result,
        diagnostics=worker_result.diagnostics if capture_diagnostics else "",
        warnings=worker_result.warnings,
    )


def _coerce_verdict(value: object) -> tuple[Verdict, tuple[str, ...]]:
    """Coerce a raw validate result into a verdict plus any legacy warnings."""
    if isinstance(value, Verdict):
        return value, ()
    if isinstance(value, dict):
        if value.get("status") == "warn":
            message = str(value.get("message", ""))
            return Verdict(status="pass"), ((message,) if message else ())
        return Verdict.model_validate(value), ()
    if value is None:
        return Verdict(status="pass"), ()
    if isinstance(value, str):
        return Verdict(status="fail", message=value), ()
    raise ValueError(f"invalid validate verdict: {value!r}")
