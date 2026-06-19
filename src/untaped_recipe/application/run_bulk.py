"""Bulk apply orchestration for planned target changes."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from untaped.errors import UntapedError

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import FileChange, TargetPlan
from untaped_recipe.domain.recipe import InputSpec, Recipe


class ApplyWriteError(UntapedError):
    """A planned target could not be written safely."""


class RunBulkApply:
    """Plan a recipe across many target directories."""

    def __init__(self, planner: ApplyRecipe) -> None:
        self._planner = planner

    def plan(
        self,
        *,
        recipe: Recipe,
        recipe_dir: Path,
        targets: list[Path],
        inputs: dict[str, object],
        parallel: int = 1,
    ) -> list[TargetPlan]:
        """Return a plan or error row for every target."""
        resolved_inputs = InputSpec.resolve_all(recipe.inputs, overrides=inputs)
        if parallel <= 1 or len(targets) <= 1:
            return [
                self._plan_one(recipe, recipe_dir, target, resolved_inputs) for target in targets
            ]
        outcomes: list[TargetPlan] = []
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(self._plan_one, recipe, recipe_dir, target, resolved_inputs): index
                for index, target in enumerate(targets)
            }
            for future in as_completed(futures):
                outcomes.append(future.result())
        order = {target: index for index, target in enumerate(targets)}
        outcomes.sort(key=lambda plan: order.get(plan.target, len(order)))
        return outcomes

    def _plan_one(
        self,
        recipe: Recipe,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
    ) -> TargetPlan:
        try:
            return self._planner(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs=inputs)
        except Exception as exc:
            return TargetPlan(target=target, status="error", error=str(exc))


def flush_changes(changes: tuple[FileChange, ...]) -> None:
    """Write all planned changes for one target after planning succeeds."""
    _verify_current_content(changes)
    staged, created_dirs = _stage_replacements(changes)
    applied: list[FileChange] = []
    try:
        for change in changes:
            path = _change_path(change)
            if change.after is None:
                if path.exists():
                    path.unlink()
                applied.append(change)
                continue
            os.replace(staged[change], path)
            applied.append(change)
    except (OSError, ApplyWriteError) as exc:
        _remove_staged_files(staged.values())
        _rollback(applied, created_dirs)
        if isinstance(exc, ApplyWriteError):
            raise
        raise ApplyWriteError(str(exc)) from exc
    finally:
        _remove_staged_files(staged.values())


def _verify_current_content(changes: tuple[FileChange, ...]) -> None:
    for change in changes:
        path = _change_path(change)
        try:
            current = path.read_text() if path.is_file() else None
        except OSError as exc:
            raise ApplyWriteError(str(exc)) from exc
        if current != change.before:
            raise ApplyWriteError(f"{path} changed since planning")


def _stage_replacements(
    changes: tuple[FileChange, ...],
) -> tuple[dict[FileChange, Path], list[Path]]:
    staged: dict[FileChange, Path] = {}
    created_dirs: list[Path] = []
    try:
        for change in changes:
            if change.after is None:
                continue
            path = _change_path(change)
            created_dirs.extend(_ensure_parent(path))
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.untaped-recipe.tmp")
            tmp.write_text(change.after)
            staged[change] = tmp
    except ApplyWriteError:
        _remove_staged_files(staged.values())
        _remove_created_dirs(created_dirs)
        raise
    except OSError as exc:
        _remove_staged_files(staged.values())
        _remove_created_dirs(created_dirs)
        raise ApplyWriteError(str(exc)) from exc
    return staged, created_dirs


def _ensure_parent(path: Path) -> list[Path]:
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            directory.mkdir()
            created.append(directory)
    except OSError:
        _remove_created_dirs(created)
        raise
    return created


def _rollback(applied: list[FileChange], created_dirs: list[Path]) -> None:
    for change in reversed(applied):
        try:
            path = _change_path(change)
            if change.before is None:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.untaped-recipe.rollback.tmp")
            tmp.write_text(change.before)
            os.replace(tmp, path)
        except OSError, ApplyWriteError:
            continue
    _remove_created_dirs(created_dirs)


def _remove_created_dirs(created_dirs: list[Path]) -> None:
    for directory in reversed(created_dirs):
        try:
            directory.rmdir()
        except OSError:
            continue


def _remove_staged_files(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            continue


def _change_path(change: FileChange) -> Path:
    try:
        return confined_path(change.target, change.relative_path, field="relative_path")
    except ValueError as exc:
        raise ApplyWriteError(str(exc)) from exc
