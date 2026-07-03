"""Shared target-file validation for application workflows."""

from __future__ import annotations

from pathlib import Path


def read_existing_text_file(
    path: Path,
    *,
    missing: str,
    not_file: str,
) -> str:
    """Read an existing text file or raise the supplied user-facing error."""
    if not path.exists():
        raise ValueError(missing)
    if not path.is_file():
        raise ValueError(not_file)
    return path.read_text(encoding="utf-8", newline="")
