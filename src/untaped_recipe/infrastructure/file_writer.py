"""Transactional target file writes for apply and restore."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from pathlib import Path

from untaped.api import UntapedError

from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import FileChange


class ApplyWriteError(UntapedError):
    """A planned target could not be written safely."""

    def __init__(self, message: str, *, rollback_incomplete: bool = False) -> None:
        super().__init__(message)
        self.rollback_incomplete = rollback_incomplete


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
        rollback_errors = _rollback(applied, created_dirs)
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise ApplyWriteError(
                f"{exc}; rollback incomplete: {details}",
                rollback_incomplete=True,
            ) from exc
        if isinstance(exc, ApplyWriteError):
            raise
        raise ApplyWriteError(str(exc)) from exc
    finally:
        _remove_staged_files(staged.values())


def _verify_current_content(changes: tuple[FileChange, ...]) -> None:
    for change in changes:
        path = _change_path(change)
        try:
            current = path.read_text(encoding="utf-8", newline="") if path.is_file() else None
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
            tmp.write_text(change.after, encoding="utf-8", newline="")
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


def _rollback(applied: list[FileChange], created_dirs: list[Path]) -> list[str]:
    errors: list[str] = []
    for change in reversed(applied):
        tmp: Path | None = None
        label = str(change.relative_path)
        try:
            path = _change_path(change)
            label = str(path)
            if change.before is None:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.untaped-recipe.rollback.tmp")
            tmp.write_text(change.before, encoding="utf-8", newline="")
            os.replace(tmp, path)
        except (OSError, ApplyWriteError) as exc:
            errors.append(f"{label}: {exc}")
        finally:
            if tmp is not None:
                _remove_staged_files((tmp,))
    _remove_created_dirs(created_dirs)
    return errors


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
