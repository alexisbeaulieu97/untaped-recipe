"""Resolve recipe target directories from bare paths or untaped pipe records."""

from __future__ import annotations

import json
from pathlib import Path

from untaped.pipe import is_envelope_line, parse_envelope_line


def resolve_target_lines(lines: list[tuple[int, str]]) -> list[Path]:
    """Resolve raw non-blank stdin lines to target paths."""
    targets: list[Path] = []
    for lineno, text in lines:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            if text.lstrip().startswith("{"):
                raise ValueError(f"line {lineno}: invalid JSON: {exc.msg}") from exc
            targets.append(Path(text))
            continue
        if not is_envelope_line(obj):
            targets.append(Path(text))
            continue
        env = parse_envelope_line(lineno, text)
        targets.append(_target_from_record(env.kind, env.record, lineno))
    return targets


def _target_from_record(kind: str | None, record: dict[str, object], lineno: int) -> Path:
    path_value = _string_field(record, "path")
    if kind == "workspace.repo":
        repo = _string_field(record, "repo")
        if path_value is None or repo is None:
            raise ValueError(f"line {lineno}: workspace.repo record requires path and repo")
        return Path(path_value) / repo
    if path_value is None:
        raise ValueError(f"line {lineno}: record path is missing or blank")
    return Path(path_value)


def _string_field(record: dict[str, object], field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
