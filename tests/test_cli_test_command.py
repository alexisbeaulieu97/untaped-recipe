"""Tests for the test command (golden-case runner)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.infrastructure.pack_store import PackLibrary

pytestmark = pytest.mark.usefixtures("isolate_config")

_COPY_RECIPE = (
    "version: 1\n"
    "steps:\n"
    "  - type: copy\n"
    "    source: assets/payload.txt\n"
    "    dest: out.txt\n"
)


def _write_pack(root: Path, *, manifest_name: str, recipe_body: str = _COPY_RECIPE) -> None:
    recipe_path = root / "recipes" / "emit" / "recipe.yml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(recipe_body, encoding="utf-8")
    asset = root / "recipes" / "emit" / "assets" / "payload.txt"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text("payload\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{manifest_name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"emit" = { path = "recipes/emit/recipe.yml" }\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _write_passing_case(root: Path) -> Path:
    case_dir = root / "tests" / "emit" / "basic"
    (case_dir / "given").mkdir(parents=True)
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "out.txt").write_text("payload\n", encoding="utf-8")
    return case_dir


def _install(source: Path) -> None:
    PackLibrary(library_root=library_root()).add(
        source, source=str(source), rev=None, name=None, force=False
    )


def test_test_pack_runs_cases_and_exits_zero_on_pass(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"pack": "demo", "recipe": "emit", "case": "basic", "status": "pass", "detail": ""}
    ]
    assert "Recipe tests: 1 passed, 0 failed, 0 errored" in result.stderr


def test_test_failure_renders_diff_on_stderr_and_exits_one(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    case_dir = _write_passing_case(source)
    (case_dir / "expected" / "out.txt").write_text("different\n", encoding="utf-8")
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])

    assert result.exit_code == 1
    row = json.loads(result.stdout)[0]
    assert row["status"] == "fail"
    assert row["detail"] == "files differ: out.txt"
    assert "# demo/emit/basic" in result.stderr
    assert "-different" in result.stderr
    assert "+payload" in result.stderr


def test_test_recipe_scope_and_no_cases_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo/emit", "--format", "json"])

    assert result.exit_code == 1
    row = json.loads(result.stdout)[0]
    assert row == {
        "pack": "demo",
        "recipe": "emit",
        "case": "",
        "status": "error",
        "detail": "no test cases found",
    }


def test_bare_test_reports_packs_without_tests_but_passes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _install(source)

    result = CliInvoker().invoke(app, ["test", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""
    assert "packs without tests: demo" in result.stderr


def test_test_reports_orphaned_tests_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)
    (source / "tests" / "renamed" / "old" / "given").mkdir(parents=True)
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])

    assert result.exit_code == 1
    rows = json.loads(result.stdout)
    orphan = next(row for row in rows if row["recipe"] == "renamed")
    assert orphan["status"] == "error"
    assert orphan["detail"] == "tests directory names no known recipe"


def test_test_explicit_path_runs_local_pack(tmp_path: Path) -> None:
    source = tmp_path / "local-pack"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)

    result = CliInvoker().invoke(app, ["test", str(source), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_test_dot_runs_pack_at_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "local-pack"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)
    monkeypatch.chdir(source)

    result = CliInvoker().invoke(app, ["test", ".", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_test_rejects_recipe_file_paths(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["test", "./recipe.yml"])

    assert result.exit_code != 0
    assert "test requires a pack directory or ref, not a recipe file" in result.output
