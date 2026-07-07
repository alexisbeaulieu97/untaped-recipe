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
        Target(path=Path("/tmp/a")),
        Target(path=Path("relative")),
    ]


def test_resolves_pipe_target_paths_and_generic_path_fallback() -> None:
    lines = [
        (1, _env("workspace.workspace", {"path": "/tmp/ws"})),
        (
            2,
            _env(
                "workspace.repo",
                {"path": "/tmp/ws", "target_path": "/tmp/ws/api", "repo": "api"},
            ),
        ),
        (3, _env("other.kind", {"path": "/tmp/ws", "target_path": "/tmp/explicit"})),
        (4, _env("other.kind", {"path": "/tmp/fallback"})),
        (5, _env("workspace.summary", {"path": "/tmp/ws", "repo_count": 0, "repo": ""})),
    ]

    assert resolve_target_lines(lines) == [
        Target(
            path=Path("/tmp/ws"),
            record={"path": "/tmp/ws"},
        ),
        Target(
            path=Path("/tmp/ws/api"),
            record={"path": "/tmp/ws", "target_path": "/tmp/ws/api", "repo": "api"},
        ),
        Target(
            path=Path("/tmp/explicit"),
            record={"path": "/tmp/ws", "target_path": "/tmp/explicit"},
        ),
        Target(
            path=Path("/tmp/fallback"),
            record={"path": "/tmp/fallback"},
        ),
    ]


def test_foreign_records_with_repo_use_generic_path_fallback() -> None:
    record = {"path": "/tmp/checkout", "repo": "api"}

    assert resolve_target_lines([(1, _env("github.pr", record))]) == [
        Target(path=Path("/tmp/checkout"), record=record)
    ]


def test_rejects_malformed_or_unusable_pipe_records() -> None:
    with pytest.raises(ValueError, match="line 1: invalid JSON"):
        resolve_target_lines([(1, '{"untaped":')])

    with pytest.raises(
        ValueError,
        match=r"line 1: workspace.repo pipe record requires target_path.*rerun.*workspace",
    ):
        resolve_target_lines([(1, _env("workspace.repo", {"path": "/tmp/ws"}))])

    with pytest.raises(
        ValueError,
        match=r"line 1: workspace.repo pipe record requires target_path.*rerun.*workspace",
    ):
        resolve_target_lines([(1, _env("workspace.repo", {"path": "/tmp/ws", "repo": ""}))])

    with pytest.raises(ValueError, match="line 1: target_path must be absolute"):
        resolve_target_lines(
            [(1, _env("workspace.repo", {"path": "/tmp/ws", "target_path": "api"}))]
        )

    with pytest.raises(ValueError, match="line 1: target_path must be a non-empty string"):
        resolve_target_lines(
            [(1, _env("workspace.repo", {"path": "/tmp/ws", "target_path": "   "}))]
        )

    with pytest.raises(ValueError, match="line 1: record path is missing or blank"):
        resolve_target_lines([(1, _env("other.kind", {"name": "api"}))])


@pytest.mark.parametrize("line", ["2024", "true", "[1]", '"api"'])
def test_non_object_json_stdin_lines_are_bare_paths(line: str) -> None:
    # A directory literally named "2024" must be pipeable; only JSON objects
    # enter record parsing (0.12 ruling supersedes the 0.8.1 rejection).
    targets = resolve_target_lines([(1, line)])

    assert [target.path for target in targets] == [Path(line)]
    assert targets[0].record is None
