"""Plan one recipe against one target directory without writing files."""

from __future__ import annotations

from pathlib import Path

from untaped_recipe.application.files import read_existing_text_file
from untaped_recipe.application.ports import HookExecutorPort
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import FileChange, TargetPlan
from untaped_recipe.domain.recipe import (
    CopyStep,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.domain.templates import render_template


class ApplyRecipe:
    """Build an in-memory target plan."""

    def __init__(self, hook_executor: HookExecutorPort) -> None:
        self._hooks = hook_executor

    def __call__(
        self,
        *,
        recipe: Recipe,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        local_hook_project: Path | None = None,
    ) -> TargetPlan:
        """Plan every step for one target."""
        if not target.is_dir():
            raise ValueError(f"target is not a directory: {target}")
        buffer: dict[Path, str | None] = {}
        warnings: list[str] = []
        for step in recipe.steps:
            if isinstance(step, TemplateStep):
                self._plan_template(step, recipe_dir, inputs, buffer)
            elif isinstance(step, CopyStep):
                self._plan_copy(step, recipe_dir, buffer)
            elif isinstance(step, RemoveStep):
                self._plan_remove(step, target, buffer)
            elif isinstance(step, TransformStep):
                self._plan_transform(
                    step,
                    local_hook_project,
                    target,
                    inputs,
                    buffer,
                    warnings,
                )
            elif isinstance(step, ValidateStep):
                self._plan_validate(
                    step.hook,
                    step.args,
                    local_hook_project,
                    target,
                    inputs,
                    warnings,
                )
            else:
                raise ValueError(f"unsupported recipe step: {step!r}")
        changes = tuple(self._changes(target, buffer))
        return TargetPlan(
            target=target,
            status="planned",
            changes=changes,
            warnings=tuple(warnings),
        )

    def _plan_validate(
        self,
        hook: str,
        args: dict[str, object],
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        warnings: list[str],
    ) -> None:
        execution = self._hooks.validate(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
        )
        verdict = execution.result
        if verdict.status == "warn":
            warnings.append(verdict.message)
        if verdict.failed:
            raise ValueError(verdict.message or f"validate hook {hook!r} failed")

    def _plan_template(
        self,
        step: TemplateStep,
        recipe_dir: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
    ) -> None:
        source = confined_path(recipe_dir, step.template, field="template")
        if not source.is_file():
            raise ValueError(f"template not found: {step.template}")
        buffer[step.dest] = render_template(
            source.read_text(encoding="utf-8", newline=""),
            inputs,
        )

    def _plan_copy(self, step: CopyStep, recipe_dir: Path, buffer: dict[Path, str | None]) -> None:
        source = confined_path(recipe_dir, step.source, field="source")
        if not source.is_file():
            raise ValueError(f"copy source not found: {step.source}")
        buffer[step.dest] = source.read_text(encoding="utf-8", newline="")

    def _plan_remove(
        self,
        step: RemoveStep,
        target: Path,
        buffer: dict[Path, str | None],
    ) -> None:
        path = confined_path(target, step.file, field="file")
        if path.exists() or step.file in buffer:
            buffer[step.file] = None

    def _plan_transform(
        self,
        step: TransformStep,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
        warnings: list[str],
    ) -> None:
        current = buffer.get(step.file)
        path = confined_path(target, step.file, field="file")
        if current is None:
            if step.file in buffer:
                raise ValueError(f"cannot transform deleted file: {step.file}")
            if not path.exists() and step.optional:
                warnings.append(f"optional transform skipped missing file: {step.file}")
                return
            current = read_existing_text_file(
                path,
                missing=f"transform file not found: {step.file}",
                not_file=f"transform path is not a file: {step.file}",
            )
        execution = self._hooks.transform(
            step.hook,
            current,
            local_hook_project=local_hook_project,
            inputs=inputs,
            target=target,
            file=path,
            args=step.args,
        )
        buffer[step.file] = execution.result

    def _changes(self, target: Path, buffer: dict[Path, str | None]) -> list[FileChange]:
        changes: list[FileChange] = []
        for relative, after in buffer.items():
            path = confined_path(target, relative, field="file")
            before = path.read_text(encoding="utf-8", newline="") if path.is_file() else None
            if before == after:
                continue
            changes.append(
                FileChange(target=target, relative_path=relative, before=before, after=after)
            )
        return changes
