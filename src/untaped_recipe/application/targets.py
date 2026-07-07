"""Resolve recipe target directories from bare paths or untaped pipe records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path

from untaped.api import is_envelope_line, parse_envelope_line


@dataclass(frozen=True)
class Target:
    """One target directory plus optional pipe-record context."""

    path: Path
    record: Mapping[str, object] | None = None


def resolve_target_lines(lines: list[tuple[int, str]]) -> list[Target]:
    """Resolve raw non-blank stdin lines to target paths."""
    targets: list[Target] = []
    for lineno, text in lines:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            if text.lstrip().startswith("{"):
                raise ValueError(f"line {lineno}: invalid JSON: {exc.msg}") from exc
            targets.append(Target(path=Path(text)))
            continue
        if not isinstance(obj, dict):
            # Bare path lines that happen to parse as JSON scalars ("2024",
            # "true") are still paths; only JSON objects enter record parsing.
            targets.append(Target(path=Path(text)))
            continue
        if not is_envelope_line(obj):
            targets.append(Target(path=Path(text)))
            continue
        env = parse_envelope_line(lineno, text)
        kind = attrgetter("kind")(env)
        if _is_summary_kind(kind):
            continue
        targets.append(
            Target(
                path=_target_from_record(kind, env.record, lineno),
                record=dict(env.record),
            )
        )
    return targets


def _target_from_record(kind: str | None, record: dict[str, object], lineno: int) -> Path:
    target_path = _target_path(record, lineno)
    if target_path is not None:
        return target_path
    if kind == "workspace.repo":
        # Bounded migration shim for pre-target_path workspace repo records;
        # generic target consumers should otherwise rely on path/target_path.
        raise ValueError(
            f"line {lineno}: workspace.repo pipe record requires target_path; "
            "rerun or upgrade untaped-workspace so repo records include target_path"
        )
    path_value = _string_field(record, "path")
    if path_value is None:
        raise ValueError(f"line {lineno}: record path is missing or blank")
    return Path(path_value)


def _is_summary_kind(kind: str | None) -> bool:
    return kind is not None and kind.endswith(".summary")


def _target_path(record: dict[str, object], lineno: int) -> Path | None:
    value = record.get("target_path")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"line {lineno}: target_path must be a non-empty string")
    path = Path(value.strip())
    if not path.is_absolute():
        raise ValueError(f"line {lineno}: target_path must be absolute")
    return path


def _string_field(record: dict[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
