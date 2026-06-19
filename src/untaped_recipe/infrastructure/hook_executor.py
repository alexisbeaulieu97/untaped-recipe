"""Execute resolved hooks in-process for built-ins or through uv workers."""

from __future__ import annotations

from pathlib import Path

from untaped_recipe import worker_protocol as protocol
from untaped_recipe.application.ports import HookHelpersPort
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.hook_resolver import BuiltinHookRef, HookResolver
from untaped_recipe.infrastructure.hook_worker_client import HookWorkerClient


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
        ref = self._resolver.resolve(hook, local_hook_project)
        if isinstance(ref, BuiltinHookRef):
            transform = getattr(ref.module, "transform", None)
            if transform is None:
                raise ValueError(f"transform hook {hook!r} has no transform callable")
            result = transform(
                content,
                inputs=inputs,
                target=target,
                file=file,
                args=args,
                helpers=self._helpers,
            )
        else:
            result = self._workers.request(
                ref,
                {
                    protocol.KIND: protocol.TRANSFORM,
                    protocol.CONTENT: content,
                    protocol.INPUTS: inputs,
                    protocol.TARGET: str(target),
                    protocol.FILE: str(file),
                    protocol.ARGS: args,
                },
            )
        if not isinstance(result, str):
            raise ValueError(f"transform hook {hook!r} must return str")
        return result

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
        ref = self._resolver.resolve(hook, local_hook_project)
        if isinstance(ref, BuiltinHookRef):
            validate = getattr(ref.module, "validate", None)
            if validate is None:
                raise ValueError(f"validate hook {hook!r} has no validate callable")
            result = validate(inputs=inputs, target=target, args=args, helpers=self._helpers)
        else:
            result = self._workers.request(
                ref,
                {
                    protocol.KIND: protocol.VALIDATE,
                    protocol.INPUTS: inputs,
                    protocol.TARGET: str(target),
                    protocol.ARGS: args,
                },
            )
        return _coerce_verdict(result)


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
