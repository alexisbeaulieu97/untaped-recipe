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


def _fail_lock(project_root: Path) -> None:
    raise ValueError("failed to create project uv.lock: mirror is missing untaped-recipe")


def _fail_if_lock_called(project_root: Path) -> None:
    raise AssertionError(f"lock_project should not be called for {project_root}")


def _assert_repairable_lock_error(
    exc_info: pytest.ExceptionInfo[ValueError],
    *,
    project_root: Path,
    created_path: Path,
    created_label: str,
) -> None:
    message = str(exc_info.value)
    assert exc_info.type.__name__ == "ScaffoldLockError"
    assert created_label in message
    assert str(created_path) in message
    assert "mirror is missing untaped-recipe" in message
    assert (
        f"fix the index or add a [tool.uv.sources] override, then run `uv lock` in {project_root}"
    ) in message


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
        'dev = ["untaped-recipe>=0.9", "pytest"]\n'
        "\n"
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["src"]\n'
        "\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n'
    )
    assert (pack_dir / "src" / "ansible_pack" / "hooks" / "__init__.py").is_file()


def test_scaffold_pack_lock_failure_keeps_pack_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_lock)
    pack_dir = tmp_path / "ansible"

    with pytest.raises(ValueError) as exc_info:
        pack_scaffold.scaffold_pack(pack_dir, "ansible")

    _assert_repairable_lock_error(
        exc_info,
        project_root=pack_dir,
        created_path=pack_dir,
        created_label="recipe pack",
    )
    assert (pack_dir / "pyproject.toml").is_file()
    assert (pack_dir / "src" / "ansible_pack" / "__init__.py").is_file()
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


def test_scaffold_recipe_lock_failure_keeps_recipe_case_and_manifest_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_lock)
    with pytest.raises(ValueError) as exc_info:
        pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")

    recipe_path = tmp_path / "ansible" / "recipes" / "playbook" / "recipe.yml"
    _assert_repairable_lock_error(
        exc_info,
        project_root=tmp_path / "ansible",
        created_path=recipe_path,
        created_label="recipe",
    )
    assert recipe_path.is_file()
    assert (tmp_path / "ansible" / "tests" / "playbook" / "basic" / "given").is_dir()
    assert (tmp_path / "ansible" / "tests" / "playbook" / "basic" / "case.yml").is_file()
    manifest = PackManifest.from_pyproject(tmp_path / "ansible")
    assert manifest.recipes["playbook"].path == "recipes/playbook/recipe.yml"


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


def test_scaffold_hook_writes_direct_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    pack_scaffold.scaffold_hook(tmp_path / "ansible", "set_owner")

    test_path = tmp_path / "ansible" / "tests" / "test_hook_set_owner.py"
    content = test_path.read_text(encoding="utf-8")
    compile(content, str(test_path), "exec")
    assert "from untaped_recipe.hook_worker import HookHelpers" in content
    assert "from ansible_pack.hooks.set_owner import transform" in content
    assert "def test_" in content


def test_scaffold_hook_validate_kind_writes_matching_pytest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    pack_scaffold.scaffold_hook(tmp_path / "ansible", "check", kind="validate")

    test_path = tmp_path / "ansible" / "tests" / "test_hook_check.py"
    content = test_path.read_text(encoding="utf-8")
    compile(content, str(test_path), "exec")
    assert "from ansible_pack.hooks.check import validate" in content
    assert "pass_()" in content


def test_scaffold_hook_rejects_existing_test_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")
    tests_dir = tmp_path / "ansible" / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_hook_set_owner.py").write_text("mine\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hook test already exists"):
        pack_scaffold.scaffold_hook(tmp_path / "ansible", "set_owner")

    module_path = tmp_path / "ansible" / "src" / "ansible_pack" / "hooks" / "set_owner.py"
    assert not module_path.exists()
    assert (tests_dir / "test_hook_set_owner.py").read_text(encoding="utf-8") == "mine\n"


def test_scaffold_hook_creation_failure_removes_test_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    def _boom(pyproject: Path, name: str, module: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pack_scaffold, "_append_hook_row", _boom)
    with pytest.raises(OSError, match="disk full"):
        pack_scaffold.scaffold_hook(tmp_path / "ansible", "set_owner")

    assert not (tmp_path / "ansible" / "tests" / "test_hook_set_owner.py").exists()
    assert not (tmp_path / "ansible" / "tests").exists()


def test_scaffolded_hook_pack_passes_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from untaped_recipe.application.harness import orphaned_test_dirs
    from untaped_recipe.infrastructure.pack_store import InstalledPack

    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_dir = tmp_path / "ansible"
    pack_scaffold.scaffold_pack(pack_dir, "ansible")
    pack_scaffold.scaffold_hook(pack_dir, "set_owner")
    (pack_dir / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    manifest = PackManifest.from_pyproject(pack_dir)
    assert orphaned_test_dirs(InstalledPack.local(pack_dir, manifest)) == []
    result = CliInvoker().invoke(app, ["check", str(pack_dir), "--format", "json"])
    assert result.exit_code == 0, result.output


def test_scaffold_hook_lock_failure_keeps_module_and_manifest_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_lock)
    with pytest.raises(ValueError) as exc_info:
        pack_scaffold.scaffold_hook(tmp_path / "ansible", "set_owner")

    module_path = tmp_path / "ansible" / "src" / "ansible_pack" / "hooks" / "set_owner.py"
    _assert_repairable_lock_error(
        exc_info,
        project_root=tmp_path / "ansible",
        created_path=module_path,
        created_label="hook module",
    )
    assert module_path.is_file()
    manifest = PackManifest.from_pyproject(tmp_path / "ansible")
    assert manifest.hooks["set_owner"].module == "ansible_pack.hooks.set_owner"


def test_new_pack_no_lock_never_invokes_uv_and_writes_scaffold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_if_lock_called)

    result = CliInvoker().invoke(app, ["new", "pack", "ansible", "--no-lock"])

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "ansible") in result.stdout
    assert "uv.lock was not created/refreshed" in result.stderr
    assert "hooks need `uv lock` before running" in result.stderr
    assert (tmp_path / "ansible" / "pyproject.toml").is_file()
    assert (tmp_path / "ansible" / "src" / "ansible_pack" / "__init__.py").is_file()
    assert not (tmp_path / "ansible" / "uv.lock").exists()


def test_new_recipe_no_lock_never_invokes_uv_and_writes_scaffold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")
    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_if_lock_called)

    result = CliInvoker().invoke(app, ["new", "recipe", "./ansible/playbook", "--no-lock"])

    assert result.exit_code == 0, result.output
    recipe_path = tmp_path / "ansible" / "recipes" / "playbook" / "recipe.yml"
    assert "ansible/recipes/playbook/recipe.yml" in result.stdout
    assert "uv.lock was not created/refreshed" in result.stderr
    assert "hooks need `uv lock` before running" in result.stderr
    assert recipe_path.is_file()
    assert (tmp_path / "ansible" / "tests" / "playbook" / "basic" / "case.yml").is_file()
    manifest = PackManifest.from_pyproject(tmp_path / "ansible")
    assert manifest.recipes["playbook"].path == "recipes/playbook/recipe.yml"


def test_new_hook_no_lock_never_invokes_uv_and_writes_scaffold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")
    monkeypatch.setattr(pack_scaffold, "lock_project", _fail_if_lock_called)

    result = CliInvoker().invoke(app, ["new", "hook", "./ansible/set_owner", "--no-lock"])

    assert result.exit_code == 0, result.output
    module_path = tmp_path / "ansible" / "src" / "ansible_pack" / "hooks" / "set_owner.py"
    assert "ansible/src/ansible_pack/hooks/set_owner.py" in result.stdout
    assert "uv.lock was not created/refreshed" in result.stderr
    assert "hooks need `uv lock` before running" in result.stderr
    assert module_path.is_file()
    manifest = PackManifest.from_pyproject(tmp_path / "ansible")
    assert manifest.hooks["set_owner"].module == "ansible_pack.hooks.set_owner"


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
