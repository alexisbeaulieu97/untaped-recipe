"""Plan one recipe against one target directory without writing files."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from untaped_recipe.application.files import read_existing_text_file
from untaped_recipe.application.ports import HookExecutorPort
from untaped_recipe.domain.paths import confined_path, safe_relative_path
from untaped_recipe.domain.plan import FileChange, TargetPlan
from untaped_recipe.domain.recipe import (
    CopyStep,
    InputSpec,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.domain.templates import render_field, render_template


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
                self._plan_template(step, recipe, recipe_dir, target, inputs, buffer)
            elif isinstance(step, CopyStep):
                self._plan_copy(step, recipe, recipe_dir, target, inputs, buffer)
            elif isinstance(step, RemoveStep):
                self._plan_remove(step, recipe, target, inputs, buffer, warnings)
            elif isinstance(step, TransformStep):
                self._plan_transform(
                    step,
                    recipe,
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
        recipe: Recipe,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
    ) -> None:
        template = _render_path(step.template, specs=recipe.inputs, values=inputs, field="template")
        dest = _render_path(step.dest, specs=recipe.inputs, values=inputs, field="dest")
        source = confined_path(recipe_dir, template, field="template")
        if not source.is_file():
            raise ValueError(f"template not found: {template}")
        if step.if_absent and _destination_exists(dest, target, buffer):
            return
        buffer[dest] = render_template(
            source.read_text(encoding="utf-8", newline=""),
            inputs,
            unknown_tokens=step.unknown_tokens,
        )

    def _plan_copy(
        self,
        step: CopyStep,
        recipe: Recipe,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
    ) -> None:
        source_relative = _render_path(
            step.source, specs=recipe.inputs, values=inputs, field="source"
        )
        dest = _render_path(step.dest, specs=recipe.inputs, values=inputs, field="dest")
        source = confined_path(recipe_dir, source_relative, field="source")
        if not source.is_file():
            raise ValueError(f"copy source not found: {source_relative}")
        if step.if_absent and _destination_exists(dest, target, buffer):
            return
        buffer[dest] = source.read_text(encoding="utf-8", newline="")

    def _plan_remove(
        self,
        step: RemoveStep,
        recipe: Recipe,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
        warnings: list[str],
    ) -> None:
        if step.globs:
            globs = _render_path_patterns(
                step.globs, specs=recipe.inputs, values=inputs, field="globs"
            )
            exclude = _render_path_patterns(
                step.exclude, specs=recipe.inputs, values=inputs, field="exclude"
            )
            matches = _expand_glob_files(target, globs, exclude)
            if not matches:
                warnings.append(f"globs matched no files: {', '.join(globs)}")
            for relative in matches:
                self._plan_remove_file(relative, target, buffer)
            return
        assert step.file is not None
        relative = _render_path(step.file, specs=recipe.inputs, values=inputs, field="file")
        self._plan_remove_file(relative, target, buffer)

    def _plan_remove_file(
        self,
        relative: Path,
        target: Path,
        buffer: dict[Path, str | None],
    ) -> None:
        path = confined_path(target, relative, field="file")
        if path.exists() or relative in buffer:
            buffer[relative] = None

    def _plan_transform(
        self,
        step: TransformStep,
        recipe: Recipe,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
        warnings: list[str],
    ) -> None:
        if step.globs:
            globs = _render_path_patterns(
                step.globs, specs=recipe.inputs, values=inputs, field="globs"
            )
            exclude = _render_path_patterns(
                step.exclude, specs=recipe.inputs, values=inputs, field="exclude"
            )
            matches = _expand_glob_files(target, globs, exclude)
            if not matches:
                warnings.append(f"globs matched no files: {', '.join(globs)}")
            for relative in matches:
                self._plan_transform_file(
                    step,
                    relative,
                    local_hook_project,
                    target,
                    inputs,
                    buffer,
                    warnings,
                )
            return
        assert step.file is not None
        relative = _render_path(step.file, specs=recipe.inputs, values=inputs, field="file")
        self._plan_transform_file(
            step,
            relative,
            local_hook_project,
            target,
            inputs,
            buffer,
            warnings,
        )

    def _plan_transform_file(
        self,
        step: TransformStep,
        relative: Path,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        buffer: dict[Path, str | None],
        warnings: list[str],
    ) -> None:
        current = buffer.get(relative)
        path = confined_path(target, relative, field="file")
        if current is None:
            if relative in buffer:
                raise ValueError(f"cannot transform deleted file: {relative}")
            if not path.exists() and step.optional:
                warnings.append(f"optional transform skipped missing file: {relative}")
                return
            current = read_existing_text_file(
                path,
                missing=f"transform file not found: {relative}",
                not_file=f"transform path is not a file: {relative}",
                decode_error=_binary_error(relative),
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
        buffer[relative] = execution.result

    def _changes(self, target: Path, buffer: dict[Path, str | None]) -> list[FileChange]:
        changes: list[FileChange] = []
        for relative, after in buffer.items():
            path = confined_path(target, relative, field="file")
            before = _read_before(path, relative) if path.is_file() else None
            if before == after:
                continue
            changes.append(
                FileChange(target=target, relative_path=relative, before=before, after=after)
            )
        return changes


def _destination_exists(
    relative: Path,
    target: Path,
    buffer: dict[Path, str | None],
) -> bool:
    if relative in buffer:
        return buffer[relative] is not None
    return confined_path(target, relative, field="dest").is_file()


def _render_path(
    value: Path,
    *,
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
    field: str,
) -> Path:
    rendered = render_field(value.as_posix(), specs=specs, values=values, field=field)
    return safe_relative_path(Path(rendered), field=field)


def _render_path_patterns(
    patterns: tuple[str, ...],
    *,
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
    field: str,
) -> tuple[str, ...]:
    return tuple(
        _render_path(Path(pattern), specs=specs, values=values, field=field).as_posix()
        for pattern in patterns
    )


def _expand_glob_files(
    target: Path, globs: tuple[str, ...], exclude: tuple[str, ...]
) -> list[Path]:
    matches: dict[str, Path] = {}
    for pattern in globs:
        for candidate in target.glob(pattern):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                relative = candidate.relative_to(target)
            except ValueError:
                continue
            relative = confined_path(target, relative, field="file").relative_to(target)
            relative_posix = relative.as_posix()
            if _is_excluded(relative_posix, exclude):
                continue
            matches[relative_posix] = relative
    return [matches[key] for key in sorted(matches)]


def _is_excluded(relative_posix: str, patterns: tuple[str, ...]) -> bool:
    # full_match keeps exclude in the same pattern language as globs
    # (`*` never crosses `/`; `**` does).
    path = PurePosixPath(relative_posix)
    return any(relative_posix == pattern or path.full_match(pattern) for pattern in patterns)


def _read_before(path: Path, relative: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", newline="")
    except UnicodeDecodeError as exc:
        raise ValueError(_binary_error(relative)) from exc


def _binary_error(relative: Path) -> str:
    return (
        f"file is not valid UTF-8: {relative.as_posix()} "
        "(binary files are unsupported; for globs, exclude: skips it)"
    )
