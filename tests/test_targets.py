"""Tests for target parsing from paths and untaped pipe records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from untaped_recipe.application.targets import Target, resolve_target_lines


def _env(kind: str | None, record: dict[str, object]) -> str:
    return json.dumps({"untaped": "1", "kind": kind, "record": record})


def test_resolves_bare_paths() -> None:
    assert resolve_target_lines([(1, "/tmp/a"), (2, "relative")]) == [
        Target(path=Path("/tmp/a"), lineno=1),
        Target(path=Path("relative"), lineno=2),
    ]


def test_resolves_workspace_pipe_kinds() -> None:
    lines = [
        (1, _env("workspace.workspace", {"path": "/tmp/ws"})),
        (2, _env("workspace.repo", {"path": "/tmp/ws", "repo": "api"})),
        (3, _env("other.kind", {"path": "/tmp/explicit"})),
    ]

    assert resolve_target_lines(lines) == [
        Target(
            path=Path("/tmp/ws"),
            record={"path": "/tmp/ws"},
            kind="workspace.workspace",
            lineno=1,
        ),
        Target(
            path=Path("/tmp/ws/api"),
            record={"path": "/tmp/ws", "repo": "api"},
            kind="workspace.repo",
            lineno=2,
        ),
        Target(
            path=Path("/tmp/explicit"),
            record={"path": "/tmp/explicit"},
            kind="other.kind",
            lineno=3,
        ),
    ]


def test_rejects_malformed_or_unusable_pipe_records() -> None:
    with pytest.raises(ValueError, match="line 1: invalid JSON"):
        resolve_target_lines([(1, '{"untaped":')])

    with pytest.raises(
        ValueError,
        match=r"line 1: workspace\.repo record requires path and repo",
    ):
        resolve_target_lines([(1, _env("workspace.repo", {"path": "/tmp/ws"}))])

    with pytest.raises(ValueError, match="line 1: record path is missing or blank"):
        resolve_target_lines([(1, _env("other.kind", {"repo": "api"}))])
