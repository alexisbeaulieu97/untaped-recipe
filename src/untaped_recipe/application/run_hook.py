"""Run one hook against explicit fixture context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from untaped_recipe.application.ports import HookExecutorPort
from untaped_recipe.domain.hook_project import HookKind
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import Verdict


@dataclass(frozen=True)
class TransformHookRun:
    """Completed transform hook debug invocation."""

    hook: str
    kind: Literal["transform"]
    target: Path
    file: Path
    relative_file: Path
    before: str
    content: str
    diagnostics: str


@dataclass(frozen=True)
class ValidateHookRun:
    """Completed validate hook debug invocation."""

    hook: str
    kind: Literal["validate"]
    target: Path
    verdict: Verdict
    diagnostics: str


HookRun = TransformHookRun | ValidateHookRun


class AmbiguousHookVerbError(ValueError):
    """Raised when hook exports need an explicit debug-run verb."""


def select_verb(
    exports: frozenset[str],
    *,
    file_given: bool,
    kind: HookKind | None,
) -> HookKind:
    """Select which hook function to invoke for one debug run."""
    if kind is not None:
        return kind
    if exports == frozenset({"transform"}):
        return "transform"
    if exports == frozenset({"validate"}):
        return "validate"
    if "transform" in exports and "validate" in exports:
        if file_given:
            return "transform"
        raise AmbiguousHookVerbError("ambiguous hook verb")
    raise ValueError("hook exports neither transform() nor validate()")


class RunHook:
    """Run a resolved hook kind once without writing target files."""

    def __init__(self, executor: HookExecutorPort) -> None:
        self._executor = executor

    @staticmethod
    def validate_context(
        *,
        kind: HookKind,
        target: Path,
        file: Path | None,
        content: str | None,
        content_file: Path | None,
    ) -> None:
        """Validate fixture context without running the hook."""
        resolved_target = _target_dir(target)
        if kind == "transform":
            _transform_content(
                resolved_target,
                file,
                content=content,
                content_file=content_file,
            )
            return
        _validate_validate_context(file=file, content=content, content_file=content_file)

    def run(
        self,
        hook: str,
        *,
        kind: HookKind,
        local_hook_project: Path | None,
        target: Path,
        file: Path | None,
        content: str | None,
        content_file: Path | None,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> HookRun:
        resolved_target = _target_dir(target)
        if kind == "transform":
            return self._run_transform(
                hook,
                local_hook_project=local_hook_project,
                target=resolved_target,
                file=file,
                content=content,
                content_file=content_file,
                inputs=inputs,
                args=args,
            )
        _validate_validate_context(file=file, content=content, content_file=content_file)
        execution = self._executor.validate(
            hook,
            local_hook_project=local_hook_project,
            target=resolved_target,
            inputs=inputs,
            args=args,
            capture_diagnostics=True,
        )
        return ValidateHookRun(
            hook=hook,
            kind="validate",
            target=resolved_target,
            verdict=execution.result,
            diagnostics=execution.diagnostics,
        )

    def _run_transform(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path | None,
        content: str | None,
        content_file: Path | None,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> TransformHookRun:
        before, resolved_file, relative_file = _transform_content(
            target,
            file,
            content=content,
            content_file=content_file,
        )
        execution = self._executor.transform(
            hook,
            before,
            local_hook_project=local_hook_project,
            target=target,
            file=resolved_file,
            inputs=inputs,
            args=args,
            capture_diagnostics=True,
        )
        return TransformHookRun(
            hook=hook,
            kind="transform",
            target=target,
            file=resolved_file,
            relative_file=relative_file,
            before=before,
            content=execution.result,
            diagnostics=execution.diagnostics,
        )


def _target_dir(target: Path) -> Path:
    resolved = target.expanduser().resolve()
    if not resolved.is_dir():
        raise ValueError(f"target is not a directory: {target}")
    return resolved


def _validate_validate_context(
    *,
    file: Path | None,
    content: str | None,
    content_file: Path | None,
) -> None:
    if file is not None or content is not None or content_file is not None:
        raise ValueError("validate hooks do not accept --file or content options")


def _transform_content(
    target: Path,
    file: Path | None,
    *,
    content: str | None,
    content_file: Path | None,
) -> tuple[str, Path, Path]:
    if file is None:
        raise ValueError("transform hooks require --file")
    if content is not None and content_file is not None:
        raise ValueError("provide --content or --content-file, not both")
    resolved_file = confined_path(target, file, field="file")
    if content_file is not None:
        try:
            return (
                content_file.expanduser().read_text(encoding="utf-8", newline=""),
                resolved_file,
                file,
            )
        except OSError as exc:
            raise ValueError(f"--content-file file not found: {content_file}") from exc
    if content is not None:
        return content, resolved_file, file
    if not resolved_file.exists():
        raise ValueError(f"transform file not found: {file}")
    if not resolved_file.is_file():
        raise ValueError(f"transform path is not a file: {file}")
    return resolved_file.read_text(encoding="utf-8", newline=""), resolved_file, file
