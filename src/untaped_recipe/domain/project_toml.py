"""Shared pyproject TOML editing helpers."""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Literal, cast, overload

import tomlkit
from tomlkit import TOMLDocument
from tomlkit.exceptions import ParseError


def read_toml_document(path: Path) -> TOMLDocument:
    """Read a pyproject TOML document for format-preserving edits."""
    try:
        return tomlkit.loads(path.read_text())
    except ParseError as exc:
        raise ValueError(f"invalid pyproject.toml: {path}") from exc


@overload
def toml_table(
    container: MutableMapping[str, Any],
    key: str,
    field: str,
    *,
    create: Literal[True],
) -> MutableMapping[str, Any]: ...


@overload
def toml_table(
    container: MutableMapping[str, Any],
    key: str,
    field: str,
    *,
    create: Literal[False],
) -> MutableMapping[str, Any] | None: ...


@overload
def toml_table(
    container: MutableMapping[str, Any],
    key: str,
    field: str,
    *,
    create: bool,
) -> MutableMapping[str, Any] | None: ...


def toml_table(
    container: MutableMapping[str, Any],
    key: str,
    field: str,
    *,
    create: bool,
) -> MutableMapping[str, Any] | None:
    """Return or create a nested TOML table."""
    value = container.get(key)
    if value is None:
        if not create:
            return None
        table = tomlkit.table()
        container[key] = table
        return cast(MutableMapping[str, Any], table)
    if not isinstance(value, MutableMapping):
        raise ValueError(f"[{field}] must be a table")
    return cast(MutableMapping[str, Any], value)
