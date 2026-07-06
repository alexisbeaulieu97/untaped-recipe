"""Tests for unified pack scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped.testing import CliInvoker

import untaped_recipe.infrastructure.pack_scaffold as pack_scaffold
from untaped_recipe import app
from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.pack import PackManifest

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_scaffold_pack_writes_parseable_manifest_with_hook_api_floors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_dir = tmp_path / "ansible"

    pack_scaffold.scaffold_pack(pack_dir, "ansible")

    manifest = PackManifest.from_pyproject(pack_dir)
    pyproject = (pack_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert manifest.name == "ansible"
    assert pyproject == (
        "[project]\n"
        'name = "untaped-recipe-ansible"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n"
        "\n"
        "[dependency-groups]\n"
        'dev = ["untaped-recipe>=0.9"]\n'
        "\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n'
    )
    assert (pack_dir / "src" / "ansible_pack" / "hooks" / "__init__.py").is_file()


def test_scaffold_recipe_appends_manifest_row_and_rejects_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    recipe_path = pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")

    manifest = PackManifest.from_pyproject(tmp_path / "ansible")
    assert recipe_path == tmp_path / "ansible" / "recipes" / "playbook" / "recipe.yml"
    assert manifest.recipes["playbook"].path == "recipes/playbook/recipe.yml"
    assert "version: 1" in recipe_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="recipe already exists"):
        pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")


def test_scaffold_recipe_creates_starter_test_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")

    case_dir = tmp_path / "ansible" / "tests" / "playbook" / "basic"
    assert (case_dir / "given").is_dir()
    case_yml = (case_dir / "case.yml").read_text(encoding="utf-8")
    assert case_yml.startswith("#")
    from untaped_recipe.application.harness import load_case_spec

    assert load_case_spec(case_dir).expect == "success"


def test_scaffold_recipe_rolls_back_test_case_on_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    def _boom(project_root: Path) -> None:
        raise ValueError("lock failed")

    monkeypatch.setattr(pack_scaffold, "lock_project", _boom)
    with pytest.raises(ValueError, match="lock failed"):
        pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")

    assert not (tmp_path / "ansible" / "tests").exists()
    assert not (tmp_path / "ansible" / "recipes" / "playbook").exists()


def test_scaffold_recipe_rejects_existing_starter_test_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")
    (tmp_path / "ansible" / "tests" / "playbook" / "basic").mkdir(parents=True)

    with pytest.raises(ValueError, match="recipe tests already exist: playbook"):
        pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")


def test_scaffold_hook_writes_exporting_stub_and_manifest_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    module_path = pack_scaffold.scaffold_hook(tmp_path / "ansible", "set_owner")

    manifest = PackManifest.from_pyproject(tmp_path / "ansible")
    assert hook_exports(module_path) == frozenset({"transform"})
    assert manifest.hooks["set_owner"].module == "ansible_pack.hooks.set_owner"
    assert "kind" not in (tmp_path / "ansible" / "pyproject.toml").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="hook already exists"):
        pack_scaffold.scaffold_hook(tmp_path / "ansible", "set_owner")


def test_scaffold_hook_can_write_validate_stub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    module_path = pack_scaffold.scaffold_hook(tmp_path / "ansible", "check", kind="validate")

    assert hook_exports(module_path) == frozenset({"validate"})


def test_new_hook_explicit_local_path_splits_on_last_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "some-local-pack", "some-local-pack")

    result = CliInvoker().invoke(app, ["new", "hook", "./some-local-pack/probe"])

    assert result.exit_code == 0, result.output
    assert (
        tmp_path / "some-local-pack" / "src" / "some_local_pack_pack" / "hooks" / "probe.py"
    ).is_file()
    manifest = PackManifest.from_pyproject(tmp_path / "some-local-pack")
    assert manifest.hooks["probe"].module == "some_local_pack_pack.hooks.probe"


def test_new_hook_rejects_bare_multi_segment_ref_with_exact_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliInvoker().invoke(app, ["new", "hook", "a/b/c"])

    assert result.exit_code != 0
    assert "qualified refs must use <pack>/<name>" in result.output
