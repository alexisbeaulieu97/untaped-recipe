"""Path validation helpers for recipe-owned and target-owned files."""

from __future__ import annotations

import re
from pathlib import Path

_LIBRARY_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def safe_relative_path(value: Path, *, field: str) -> Path:
    """Return a path only if it is a safe relative file path."""
    if value.is_absolute() or ".." in value.parts or value == Path("."):
        raise ValueError(f"{field} must be a safe relative path")
    return value


def safe_library_name(value: str, *, field: str = "name") -> str:
    """Return a logical library name that cannot affect path structure."""
    name = value.strip()
    if not name or name in {".", ".."} or not _LIBRARY_NAME_RE.fullmatch(name):
        raise ValueError(f"{field} must be a safe library name")
    return name


def confined_path(base: Path, relative: Path, *, field: str) -> Path:
    """Resolve a target-relative path without following nested symlinks."""
    relative = safe_relative_path(relative, field=field)
    root = base.expanduser().resolve()
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{field} must not traverse a symlink: {relative}")
        if current.exists():
            try:
                current.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"{field} must stay under {base}: {relative}") from exc
    return root / relative
