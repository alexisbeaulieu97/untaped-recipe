"""Shared YAML dump option application for in-process and worker helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_TOP_LEVEL_OPTIONS = frozenset(
    {
        "width",
        "preserve_quotes",
        "indent",
        "block_seq_indent",
        "explicit_start",
        "explicit_end",
    }
)
_INDENT_OPTIONS = frozenset({"mapping", "sequence", "offset"})
_SCALAR_OPTIONS: dict[str, tuple[type[object], str]] = {
    "block_seq_indent": (int, "an integer"),
    "explicit_start": (bool, "a boolean"),
    "explicit_end": (bool, "a boolean"),
}


def apply_yaml_dump_options(yaml: Any, options: Mapping[str, object] | None) -> None:
    """Apply supported YAML dump formatting options to a ruamel YAML instance."""
    opts: Mapping[str, object] = {} if options is None else options
    _reject_unknown_keys(opts, _TOP_LEVEL_OPTIONS, label="YAML dump option")
    preserve_quotes = _optional(opts, "preserve_quotes", bool, "a boolean")
    width = _optional(opts, "width", int, "an integer")
    yaml.preserve_quotes = True if preserve_quotes is None else preserve_quotes
    yaml.width = 4096 if width is None else width

    indent = opts.get("indent")
    if indent is not None:
        if not isinstance(indent, Mapping):
            raise TypeError("YAML dump option 'indent' must be a mapping")
        _reject_unknown_keys(indent, _INDENT_OPTIONS, label="YAML indent option")
        indent_options = {
            key: value
            for key in ("mapping", "sequence", "offset")
            if (value := _optional(indent, key, int, "an integer")) is not None
        }
        if indent_options:
            yaml.indent(**indent_options)

    for name, (expected_type, label) in _SCALAR_OPTIONS.items():
        scalar_value = _optional(opts, name, expected_type, label)
        if scalar_value is not None:
            setattr(yaml, name, scalar_value)


def _optional[T](
    options: Mapping[str, object],
    key: str,
    expected_type: type[T],
    label: str,
) -> T | None:
    value = options.get(key)
    if value is None:
        return None
    if type(value) is not expected_type:
        raise TypeError(f"YAML dump option {key!r} must be {label}")
    return value


def _reject_unknown_keys(
    options: Mapping[str, object],
    supported: frozenset[str],
    *,
    label: str,
) -> None:
    unknown = sorted(str(key) for key in options if key not in supported)
    if unknown:
        raise TypeError(f"unsupported {label}: {unknown[0]}")
