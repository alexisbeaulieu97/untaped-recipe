"""Plan one recipe against one target directory without writing files."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from untaped_recipe.application.ports import HookHelpersPort, HookLoaderPort
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import FileChange, TargetPlan, Verdict
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

    def __init__(
        self,
        hook_loader: HookLoaderPort,
        *,
        helpers: HookHelpersPort,
        template_renderer: Callable[[str, dict[str, object]], str] = render_template,
    ) -> None:
        self._hooks = hook_loader
        self._helpers = helpers
        self._render_template = template_renderer

    def __call__(
        self,
        *,
        recipe: Recipe,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
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
                self._plan_transform(step, recipe_dir, target, inputs, buffer)
            elif isinstance(step, ValidateStep):
                self._plan_validate(
                    step.hook,
                    step.args,
                    recipe_dir,
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
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        warnings: list[str],
    ) -> None:
        module = self._hooks.load(hook, recipe_dir)
        validate = getattr(module, "validate", None)
        if validate is None:
            raise ValueError(f"validate hook {hook!r} has no validate callable")
        verdict = _coerce_verdict(
            validate(inputs=inputs, target=target, args=args, helpers=self._helpers)
        )
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
        buffer[step.dest] = self._render_template(source.read_text(), inputs)

    def _plan_copy(self, step: CopyStep, recipe_dir: Path, buffer: dict[Path, str | None]) -> None:
        source = confined_path(recipe_dir, step.source, field="source")
        if not source.is_file():
            raise ValueError(f"copy source not found: {step.source}")
        buffer[step.dest] = source.read_text()

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
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
    ) -> None:
        current = buffer.get(step.file)
        path = confined_path(target, step.file, field="file")
        if current is None:
            if step.file in buffer:
                raise ValueError(f"cannot transform deleted file: {step.file}")
            if not path.is_file():
                raise ValueError(f"transform file not found: {step.file}")
            current = path.read_text()
        module = self._hooks.load(step.hook, recipe_dir)
        transform = getattr(module, "transform", None)
        if transform is None:
            raise ValueError(f"transform hook {step.hook!r} has no transform callable")
        buffer[step.file] = transform(
            current,
            inputs=inputs,
            target=target,
            file=path,
            args=step.args,
            helpers=self._helpers,
        )

    def _changes(self, target: Path, buffer: dict[Path, str | None]) -> list[FileChange]:
        changes: list[FileChange] = []
        for relative, after in buffer.items():
            path = confined_path(target, relative, field="file")
            before = path.read_text() if path.is_file() else None
            if before == after:
                continue
            changes.append(
                FileChange(target=target, relative_path=relative, before=before, after=after)
            )
        return changes


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
