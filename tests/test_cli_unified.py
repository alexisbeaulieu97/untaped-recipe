"""Tests for the unified 0.9 pack-facing CLI surface."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.infrastructure.pack_store import PackLibrary

pytestmark = pytest.mark.usefixtures("isolate_config")


def _write_pack(
    root: Path,
    *,
    manifest_name: str,
    recipes: dict[str, str],
    hooks: dict[str, str] | None = None,
    recipe_body: str = "version: 1\nsteps: []\n",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    recipe_rows: list[str] = []
    for name, relative in recipes.items():
        recipe_path = root / relative
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(recipe_body, encoding="utf-8")
        recipe_rows.append(f'"{name}" = {{ path = "{relative}" }}')
    hook_rows: list[str] = []
    for name, module in (hooks or {}).items():
        module_path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(
            "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
            encoding="utf-8",
        )
        package = root / "src" / Path(module.split(".")[0])
        package.mkdir(exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        hooks_package = root / "src" / Path(*module.split(".")[:-1])
        (hooks_package / "__init__.py").write_text("", encoding="utf-8")
        hook_rows.append(f'"{name}" = {{ module = "{module}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{manifest_name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        + "\n".join(recipe_rows)
        + "\n"
        + ("\n[tool.untaped_recipe.hooks]\n" + "\n".join(hook_rows) + "\n" if hook_rows else ""),
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _install_pack(source: Path, *, name: str | None = None) -> None:
    PackLibrary(library_root=library_root()).add(
        source,
        source=str(source),
        rev=None,
        name=name,
        force=False,
    )


def test_unified_list_recipes_hooks_and_packs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)

    recipes = CliInvoker().invoke(app, ["list", "--format", "json"])
    hooks = CliInvoker().invoke(app, ["list", "--hooks", "--format", "json"])
    packs = CliInvoker().invoke(app, ["list", "--packs", "--format", "json"])

    assert recipes.exit_code == 0, recipes.output
    assert json.loads(recipes.stdout) == [
        {
            "pack": "ansible",
            "name": "playbook",
            "ref": "ansible/playbook",
            "path": str(library_root() / "packs" / "ansible" / "recipes/playbook/recipe.yml"),
        }
    ]
    assert json.loads(hooks.stdout)[0]["ref"] == "ansible/check"
    assert json.loads(packs.stdout)[0]["name"] == "ansible"


def test_list_hooks_shows_builtins_even_on_empty_library(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--hooks", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "pack": "(builtin)",
            "name": "yaml_edit",
            "ref": "yaml_edit",
            "module": "untaped_recipe.builtins.hooks.yaml_edit",
            "path": rows[0]["path"],
        }
    ]
    assert rows[0]["path"].endswith("yaml_edit.py")
    assert "no packs installed" not in result.stderr


def test_list_hooks_orders_library_rows_before_builtins(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)

    result = CliInvoker().invoke(app, ["list", "--hooks", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [row["ref"] for row in rows] == ["ansible/check", "yaml_edit"]


def test_list_recipes_keeps_empty_library_message(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["list"])

    assert result.exit_code == 0, result.output
    assert "no packs installed" in result.stderr


def test_list_packs_keeps_empty_library_message(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["list", "--packs"])

    assert result.exit_code == 0, result.output
    assert "no packs installed" in result.stderr


def test_show_builtin_hook_renders_detail(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["show", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    detail = json.loads(result.stdout)
    assert detail["ref"] == "yaml_edit"
    assert detail["module"] == "untaped_recipe.builtins.hooks.yaml_edit"
    assert "transform" in detail["exports"]


def test_check_builtin_hook_renders_pass_row(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["check", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "recipe": "yaml_edit",
            "status": "pass",
            "path": rows[0]["path"],
            "error": "",
        }
    ]
    assert rows[0]["path"].endswith("yaml_edit.py")


def test_check_without_ref_does_not_enumerate_builtins(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["check", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert "no packs installed" in result.stderr


def test_check_prefers_library_pack_over_builtin(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="yaml_edit", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source, name="yaml_edit")

    result = CliInvoker().invoke(app, ["check", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "pack": "yaml_edit",
            "status": "pass",
            "path": str(library_root() / "packs" / "yaml_edit"),
            "recipes": 1,
            "hooks": 0,
            "error": "",
        }
    ]


def test_check_prefers_library_recipe_over_builtin(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="shadow", recipes={"yaml_edit": "recipes/yaml.yml"})
    _install_pack(source)

    result = CliInvoker().invoke(app, ["check", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "recipe": "shadow/yaml_edit",
            "status": "pass",
            "path": str(library_root() / "packs" / "shadow" / "recipes/yaml.yml"),
            "error": "",
        }
    ]


def test_check_unknown_bare_ref_keeps_recipe_miss(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["check", "not_a_builtin", "--format", "json"])

    assert result.exit_code == 1
    assert "recipe not found: not_a_builtin" in result.stderr


def test_show_prefers_library_hook_over_builtin(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="shadow",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"yaml_edit": "shadow_pack.hooks.yaml_edit"},
    )
    _install_pack(source)

    result = CliInvoker().invoke(app, ["show", "yaml_edit", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["module"] == "shadow_pack.hooks.yaml_edit"


def test_edit_rejects_builtin_hook(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["edit", "yaml_edit"])

    assert result.exit_code == 1
    assert "built-in hooks are engine-owned and cannot be edited: yaml_edit" in result.stderr


def test_unified_show_pack_and_recipe(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source)

    pack = CliInvoker().invoke(app, ["show", "ansible", "--format", "json"])
    recipe = CliInvoker().invoke(app, ["show", "ansible/playbook", "--format", "json"])

    assert pack.exit_code == 0, pack.output
    assert json.loads(pack.stdout)["name"] == "ansible"
    assert recipe.exit_code == 0, recipe.output
    assert json.loads(recipe.stdout)["ref"] == "ansible/playbook"


def test_unified_check_pack_validates_recipe_hook_exports(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
        recipe_body="version: 1\nsteps:\n  - type: validate\n    hook: check\n",
    )
    _install_pack(source)

    result = CliInvoker().invoke(app, ["check", "ansible", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_check_flags_orphaned_tests_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    (source / "tests" / "playbook" / "basic" / "given").mkdir(parents=True)
    (source / "tests" / "renamed" / "old" / "given").mkdir(parents=True)
    _install_pack(source)

    result = CliInvoker().invoke(app, ["check", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "error"
    assert row["error"] == "tests directory names no known recipe: renamed"


def test_check_reports_stale_lockfile_for_hook_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)

    def _stale(project_root: Path) -> None:
        raise ValueError(f"lockfile is stale — run 'uv lock' in {project_root}")

    monkeypatch.setattr("untaped_recipe.application.check_pack.check_lock", _stale)
    result = CliInvoker().invoke(app, ["check", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "error"
    assert "lockfile is stale — run 'uv lock' in" in row["error"]


def test_check_probes_lock_freshness_once_per_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={
            "one": "recipes/one/recipe.yml",
            "two": "recipes/two/recipe.yml",
            "three": "recipes/three/recipe.yml",
        },
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)
    probed: list[Path] = []
    monkeypatch.setattr("untaped_recipe.application.check_pack.check_lock", probed.append)

    result = CliInvoker().invoke(app, ["check", "ansible", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(probed) == 1


def test_check_skips_lock_probe_for_hookless_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="plain", recipes={"ok": "recipes/ok.yml"})
    _install_pack(source)
    probed: list[Path] = []
    monkeypatch.setattr("untaped_recipe.application.check_pack.check_lock", probed.append)

    result = CliInvoker().invoke(app, ["check", "plain", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert probed == []


def test_check_hookless_pack_without_lock_passes_pack_ref_and_library(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="plain", recipes={"ok": "recipes/ok.yml"})
    _install_pack(source)
    installed = library_root() / "packs" / "plain"
    (installed / "uv.lock").unlink()

    pack_ref = CliInvoker().invoke(app, ["check", "plain", "--format", "json"])
    library = CliInvoker().invoke(app, ["check", "--format", "json"])

    assert pack_ref.exit_code == 0, pack_ref.output
    assert library.exit_code == 0, library.output
    assert json.loads(pack_ref.stdout) == [
        {
            "pack": "plain",
            "status": "pass",
            "path": str(installed),
            "recipes": 1,
            "hooks": 0,
            "error": "",
        }
    ]
    assert json.loads(library.stdout) == json.loads(pack_ref.stdout)


def test_check_hookless_explicit_project_without_lock_passes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="plain", recipes={"ok": "recipes/ok.yml"})
    (source / "uv.lock").unlink()

    result = CliInvoker().invoke(app, ["check", str(source), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "pack": "plain",
            "status": "pass",
            "path": str(source),
            "recipes": 1,
            "hooks": 0,
            "error": "",
        }
    ]


def test_check_hook_pack_without_lock_keeps_pack_error_exact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook.yml"},
        hooks={"check": "ansible_pack.hooks.check"},
    )
    _install_pack(source)
    installed = library_root() / "packs" / "ansible"
    (installed / "uv.lock").unlink()

    result = CliInvoker().invoke(app, ["check", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)[0]["error"] == f"pack project is missing uv.lock: {installed}"


def test_check_without_ref_reports_library_reconcile_and_pack_rows(tmp_path: Path) -> None:
    good_source = tmp_path / "good-source"
    stale_source = tmp_path / "stale-source"
    _write_pack(good_source, manifest_name="good", recipes={"ok": "recipes/ok.yml"})
    _write_pack(stale_source, manifest_name="stale", recipes={"old": "recipes/old.yml"})
    _install_pack(good_source, name="good")
    _install_pack(stale_source, name="stale")
    shutil.rmtree(library_root() / "packs" / "stale")
    _write_pack(
        library_root() / "packs" / "orphan",
        manifest_name="orphan",
        recipes={"playbook": "recipes/playbook.yml"},
    )

    result = CliInvoker().invoke(app, ["check", "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    errors = {row["error"] for row in rows if row["status"] == "error"}
    assert errors == {
        "pack 'stale' is in packs.toml but missing from packs/",
        "pack directory 'orphan' is not recorded in packs.toml",
    }
    passes = {row["pack"] for row in rows if row["status"] == "pass"}
    assert passes == {"good", "orphan"}


def test_check_without_ref_healthy_library_exits_zero(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source)

    result = CliInvoker().invoke(app, ["check", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {
            "pack": "ansible",
            "status": "pass",
            "path": str(library_root() / "packs" / "ansible"),
            "recipes": 1,
            "hooks": 0,
            "error": "",
        }
    ]


def test_library_miss_hints_at_existing_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    result = CliInvoker().invoke(app, ["show", "demo"])

    assert result.exit_code == 1
    assert "recipe not found: demo" in result.stderr
    assert "a path named 'demo' exists" in result.stderr
    assert "prefix ./" in result.stderr


def test_library_miss_without_matching_path_keeps_plain_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliInvoker().invoke(app, ["show", "demo"])

    assert result.exit_code == 1
    assert "recipe not found: demo" in result.stderr
    assert "a path named" not in result.stderr


def test_explicit_path_miss_hints_at_library_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="pack", recipes={"demo": "recipes/demo.yml"})
    _install_pack(source)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    result = CliInvoker().invoke(app, ["apply", "./demo", str(tmp_path), "--yes"])

    assert result.exit_code == 1
    assert "recipe file not found" in result.stderr
    assert "did you mean the library ref 'demo'?" in result.stderr


def test_missing_explicit_recipe_path_hints_at_library_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="pack", recipes={"demo": "recipes/demo.yml"})
    _install_pack(source)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", "./demo.yml", str(target), "--yes"])

    assert result.exit_code == 1
    assert "recipe file not found: demo.yml" in result.stderr
    assert "did you mean the library ref 'demo'?" in result.stderr


def test_explicit_path_miss_without_library_match_keeps_plain_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "demo").mkdir()

    result = CliInvoker().invoke(app, ["apply", "./demo", str(tmp_path), "--yes"])

    assert result.exit_code == 1
    assert "recipe file not found" in result.stderr
    assert "did you mean" not in result.stderr


def test_missing_explicit_recipe_path_reports_guard_for_apply_and_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    expected = "recipe file not found: nope.yml"

    apply_result = CliInvoker().invoke(app, ["apply", "./nope.yml", str(target), "--yes"])
    check_result = CliInvoker().invoke(app, ["check", "./nope.yml", "--format", "json"])

    assert apply_result.exit_code == 1
    assert check_result.exit_code == 1
    assert expected in apply_result.stderr
    assert expected in check_result.stderr
    assert "Traceback" not in apply_result.output
    assert "Traceback" not in check_result.output


def test_apply_bare_ref_uses_library_even_when_matching_local_directory_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ansible" / "playbook").mkdir(parents=True)
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source)
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", "ansible/playbook", str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "recipe not found" not in result.output


def test_apply_explicit_and_yaml_paths_load_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    recipe_dir = tmp_path / "a" / "b"
    recipe_dir.mkdir(parents=True)
    recipe = recipe_dir / "recipe.yml"
    recipe.write_text("version: 1\nsteps: []\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    explicit_dir = CliInvoker().invoke(app, ["apply", "./a/b/recipe.yml", str(target), "--dry-run"])
    yaml_suffix = CliInvoker().invoke(app, ["apply", "a/b/recipe.yml", str(target), "--dry-run"])

    assert explicit_dir.exit_code == 0, explicit_dir.output
    assert yaml_suffix.exit_code == 0, yaml_suffix.output


def test_unified_remove_destructive_gating_and_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    _install_pack(source)
    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: False)

    refused = CliInvoker().invoke(app, ["remove", "ansible"])
    removed = CliInvoker().invoke(app, ["remove", "ansible", "--yes"])

    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert removed.exit_code == 0, removed.output
    assert not (library_root() / "packs" / "ansible").exists()


def test_cli_emit_kinds_are_the_surviving_pack_unification_set() -> None:
    allowed = {
        "recipe.outcome",
        "recipe.backup",
        "recipe.hook_run",
        "recipe.recipe",
        "recipe.hook",
        "recipe.pack",
        "recipe.check",
        "recipe.test",
    }
    cli_dir = Path(__file__).parents[1] / "src" / "untaped_recipe" / "cli"
    found: set[str] = set()
    for path in cli_dir.glob("*.py"):
        found.update(re.findall(r'kind="(recipe\.[^"]+)"', path.read_text(encoding="utf-8")))

    assert found == allowed
