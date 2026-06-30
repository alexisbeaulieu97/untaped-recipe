"""Shared YAML dump option application for in-process and worker helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def apply_yaml_dump_options(yaml: Any, options: Mapping[str, object] | None) -> None:
    """Apply supported YAML dump formatting options to a ruamel YAML instance."""
    opts: Mapping[str, object] = {} if options is None else options
    preserve_quotes = _optional_bool(opts, "preserve_quotes")
    width = _optional_int(opts, "width")
    yaml.preserve_quotes = True if preserve_quotes is None else preserve_quotes
    yaml.width = 4096 if width is None else width

    indent = opts.get("indent")
    if indent is not None:
        if not isinstance(indent, Mapping):
            raise TypeError("YAML dump option 'indent' must be a mapping")
        indent_options = {
            key: value
            for key in ("mapping", "sequence", "offset")
            if (value := _optional_int(indent, key)) is not None
        }
        if indent_options:
            yaml.indent(**indent_options)

    block_seq_indent = _optional_int(opts, "block_seq_indent")
    if block_seq_indent is not None:
        yaml.block_seq_indent = block_seq_indent
    explicit_start = _optional_bool(opts, "explicit_start")
    if explicit_start is not None:
        yaml.explicit_start = explicit_start
    explicit_end = _optional_bool(opts, "explicit_end")
    if explicit_end is not None:
        yaml.explicit_end = explicit_end


def _optional_int(options: Mapping[str, object], key: str) -> int | None:
    value = options.get(key)
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"YAML dump option {key!r} must be an integer")
    return value


def _optional_bool(options: Mapping[str, object], key: str) -> bool | None:
    value = options.get(key)
    if value is None:
        return None
    if type(value) is not bool:
        raise TypeError(f"YAML dump option {key!r} must be a boolean")
    return value
