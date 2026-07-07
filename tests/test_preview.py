"""Tests for human preview rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped_recipe.cli.preview import render_preview
from untaped_recipe.domain.plan import FileChange, TargetPlan
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.settings import RecipeSettings


@pytest.fixture(autouse=True)
def wide_preview_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "500")


def _recipe(*, sensitive: bool = False) -> Recipe:
    inputs = {"token": {"type": "str", "sensitive": True}} if sensitive else {}
    return Recipe.model_validate({"version": 1, "inputs": inputs, "steps": []})


def _change(target: Path, name: str, *, before: str | None = None, after: str | None) -> FileChange:
    return FileChange(target=target, relative_path=Path(name), before=before, after=after)


def _plan(
    target: Path, *changes: FileChange, display_inputs: dict[str, object] | None = None
) -> TargetPlan:
    return TargetPlan(
        target=target,
        status="planned",
        changes=changes,
        display_inputs=display_inputs or {},
    )


def test_recipe_settings_default_preview_max_rows_and_zero_unlimited() -> None:
    assert RecipeSettings().preview_max_rows == 50
    assert RecipeSettings(preview_max_rows=0).preview_max_rows == 0
    with pytest.raises(ValidationError):
        RecipeSettings(preview_max_rows=-1)


@pytest.mark.parametrize(
    ("preview_max_rows", "file_count", "collapses"),
    [
        (3, 2, False),
        (3, 3, False),
        (3, 4, True),
        (0, 4, False),
    ],
)
def test_table_preview_collapses_only_above_file_row_threshold(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    preview_max_rows: int,
    file_count: int,
    collapses: bool,
) -> None:
    target = tmp_path / "target"
    changes = tuple(
        _change(target, f"file-{index}.txt", after=f"{index}\n") for index in range(file_count)
    )

    render_preview(
        _recipe(),
        [_plan(target, *changes)],
        preview="table",
        preview_max_rows=preview_max_rows,
    )

    stderr = capsys.readouterr().err
    assert "Recipe preview:" in stderr
    if collapses:
        assert "files" in stderr
        assert str(target) in stderr
        assert "file-0.txt" not in stderr
    else:
        assert "action" in stderr
        assert "file-0.txt" in stderr


def test_table_preview_tier_two_aggregates_exact_change_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "target"
    plan = _plan(
        target,
        _change(target, "created.txt", after="one\ntwo\n"),
        _change(target, "modified.txt", before="old\n", after="new\nmore\n"),
    )

    render_preview(_recipe(), [plan], preview="table", preview_max_rows=1)

    stderr = capsys.readouterr().err
    assert "2" in stderr
    assert "+4 -1" in stderr


def test_table_preview_tier_two_at_target_threshold_shows_all_targets(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plans = []
    for index in range(2):
        target = tmp_path / f"target-{index}"
        plans.append(
            _plan(
                target,
                _change(target, "a.txt", after="a\n"),
                _change(target, "b.txt", after="b\n"),
            )
        )

    render_preview(_recipe(), plans, preview="table", preview_max_rows=2)

    stderr = capsys.readouterr().err
    assert str(tmp_path / "target-0") in stderr
    assert str(tmp_path / "target-1") in stderr
    assert "showing first" not in stderr


def test_table_preview_tier_three_truncates_target_rows(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plans = []
    for index in range(3):
        target = tmp_path / f"target-{index}"
        plans.append(_plan(target, _change(target, "out.txt", after=f"{index}\n")))

    render_preview(_recipe(), plans, preview="table", preview_max_rows=2)

    stderr = capsys.readouterr().err
    assert str(tmp_path / "target-0") in stderr
    assert str(tmp_path / "target-1") in stderr
    assert str(tmp_path / "target-2") not in stderr
    assert "showing first 2 of 3 targets (use --preview diff for full detail)" in stderr


def test_table_preview_keeps_sensitive_and_error_tables_when_collapsed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    normal = tmp_path / "normal"
    sensitive = tmp_path / "sensitive"
    error = tmp_path / "error"
    plans = [
        _plan(normal, _change(normal, "first.txt", after="one\n")),
        _plan(normal, _change(normal, "second.txt", after="two\n")),
        _plan(
            sensitive,
            _change(sensitive, "secret.txt", after="token\n"),
            display_inputs={"token": "***"},
        ),
        TargetPlan(target=error, status="error", error="failed planning"),
    ]

    render_preview(_recipe(sensitive=True), plans, preview="table", preview_max_rows=1)

    stderr = capsys.readouterr().err
    assert "files_changed" in stderr
    assert str(sensitive) in stderr
    assert "secret.txt" not in stderr
    assert "failed planning" in stderr


def test_diff_preview_ignores_preview_row_limit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "target"
    plan = _plan(
        target,
        _change(target, "one.txt", before="before\n", after="after\n"),
        _change(target, "two.txt", before="before\n", after="after\n"),
    )

    render_preview(_recipe(), [plan], preview="diff", preview_max_rows=1)

    stderr = capsys.readouterr().err
    assert "one.txt" in stderr
    assert "two.txt" in stderr
    assert "showing first" not in stderr
