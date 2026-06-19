"""Execute resolved hooks in-process for built-ins or through uv workers."""

from __future__ import annotations

from pathlib import Path

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
        recipe_dir: Path,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> str:
        """Run a transform hook and return replacement content."""
        ref = self._resolver.resolve(hook, recipe_dir)
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
                    "kind": "transform",
                    "content": content,
                    "inputs": inputs,
                    "target": str(target),
                    "file": str(file),
                    "args": args,
                },
            )
        if not isinstance(result, str):
            raise ValueError(f"transform hook {hook!r} must return str")
        return result

    def validate(
        self,
        hook: str,
        *,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> Verdict:
        """Run a validate hook and coerce its verdict."""
        ref = self._resolver.resolve(hook, recipe_dir)
        if isinstance(ref, BuiltinHookRef):
            validate = getattr(ref.module, "validate", None)
            if validate is None:
                raise ValueError(f"validate hook {hook!r} has no validate callable")
            result = validate(inputs=inputs, target=target, args=args, helpers=self._helpers)
        else:
            result = self._workers.request(
                ref,
                {
                    "kind": "validate",
                    "inputs": inputs,
                    "target": str(target),
                    "args": args,
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
