"""Tests for uv recipe projects and recipe packs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.domain.recipe_project import read_recipe_project_metadata
from untaped_recipe.infrastructure.backup import BackupStore
from untaped_recipe.infrastructure.pack_library import PackLibrary
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_recipe_yaml_is_behavior_without_embedded_name() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "description": "A recipe whose public name comes from its project.",
            "steps": [],
        }
    )

    assert recipe.description.startswith("A recipe")


def test_recipe_project_metadata_supports_standalone_recipes_and_packs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"add-config" = { path = "recipe.yml" }\n'
    )

    metadata = read_recipe_project_metadata(project)

    assert metadata.pack is None
    assert metadata.recipe_paths() == {"add-config": Path("recipe.yml")}

    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-pack-ansible"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe]\n"
        'pack = "ansible"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }\n'
    )

    metadata = read_recipe_project_metadata(project)

    assert metadata.pack == "ansible"
    assert metadata.recipe_paths()["playbook-migration"] == Path(
        "recipes/playbook-migration/recipe.yml"
    )


def test_recipe_library_installs_and_resolves_uv_recipe_projects(tmp_path: Path) -> None:
    root = tmp_path / "library"
    source = tmp_path / "source"
    source.mkdir()
    (source / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (source / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"demo" = { path = "recipe.yml" }\n'
    )
    (source / "uv.lock").write_text("version = 1\n")

    added = RecipeLibrary(root).add(source)
    resolution = RecipeLibrary(root).resolve_detail("demo")

    assert added == root / "recipes" / "demo"
    assert resolution.path == root / "recipes" / "demo" / "recipe.yml"
    assert resolution.ref == "demo"
    assert resolution.local_hook_project == root / "recipes" / "demo"
    assert RecipeLibrary(root).list()[0].name == "demo"


def test_pack_library_manages_empty_packs_and_nested_recipes(tmp_path: Path) -> None:
    root = tmp_path / "library"
    pack_root = PackLibrary(root).init("ansible", base_dir=tmp_path)

    metadata = read_recipe_project_metadata(pack_root)
    assert metadata.pack == "ansible"
    assert metadata.recipe_paths() == {}

    recipe_path = PackLibrary(root).init_recipe(pack_root, "playbook-migration")
    metadata = read_recipe_project_metadata(pack_root)

    assert recipe_path == pack_root / "recipes" / "playbook-migration" / "recipe.yml"
    assert (
        '"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }'
        in (pack_root / "pyproject.toml").read_text()
    )
    assert metadata.recipe_paths() == {
        "playbook-migration": Path("recipes/playbook-migration/recipe.yml")
    }
    added = PackLibrary(root).add(pack_root)
    assert added == root / "packs" / "ansible"
    resolution = RecipeLibrary(root).resolve_detail("ansible:playbook-migration")
    assert resolution.ref == "ansible:playbook-migration"

    removed = PackLibrary(root).remove_recipe(pack_root, "playbook-migration")
    assert removed == pack_root / "recipes" / "playbook-migration"
    assert read_recipe_project_metadata(pack_root).recipe_paths() == {}


def test_cli_recipe_and_pack_authoring_workflows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    editor = tmp_path / "editor.sh"
    marker = tmp_path / "edited.txt"
    editor.write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > {marker}\n")
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    monkeypatch.chdir(tmp_path)
    invoker = CliInvoker()

    initialized_recipe = invoker.invoke(app, ["recipe", "init", "demo"])
    assert initialized_recipe.exit_code == 0, initialized_recipe.output
    recipe_project = Path(initialized_recipe.stdout.strip())
    assert (recipe_project / "recipe.yml").is_file()
    assert (recipe_project / "uv.lock").is_file()

    local_hook = invoker.invoke(
        app,
        ["recipe", "hook", "init", str(recipe_project), "check", "--kind", "validate"],
    )
    assert local_hook.exit_code == 0, local_hook.output
    assert (
        "def validate" in (recipe_project / "src" / "demo_hooks" / "hooks" / "check.py").read_text()
    )

    added_recipe = invoker.invoke(app, ["recipe", "add", str(recipe_project)])
    assert added_recipe.exit_code == 0, added_recipe.output
    listed_recipes = invoker.invoke(app, ["recipe", "list", "--format", "json"])
    assert json.loads(listed_recipes.stdout)[0]["name"] == "demo"
    shown_recipe = invoker.invoke(app, ["recipe", "show", "demo"])
    assert "version: 1" in shown_recipe.stdout

    initialized_pack = invoker.invoke(app, ["pack", "init", "ansible"])
    assert initialized_pack.exit_code == 0, initialized_pack.output
    pack_project = Path(initialized_pack.stdout.strip())
    pack_recipe = invoker.invoke(
        app,
        ["pack", "recipe", "init", str(pack_project), "playbook-migration"],
    )
    assert pack_recipe.exit_code == 0, pack_recipe.output
    pack_hook = invoker.invoke(
        app,
        ["pack", "hook", "init", str(pack_project), "check", "--kind", "validate"],
    )
    assert pack_hook.exit_code == 0, pack_hook.output

    added_pack = invoker.invoke(app, ["pack", "add", str(pack_project)])
    assert added_pack.exit_code == 0, added_pack.output
    listed_packs = invoker.invoke(app, ["pack", "list", "--format", "json"])
    assert json.loads(listed_packs.stdout)[0]["name"] == "ansible"
    pack_recipes = invoker.invoke(
        app,
        ["pack", "recipe", "list", "ansible", "--format", "json"],
    )
    assert json.loads(pack_recipes.stdout)[0]["name"] == "playbook-migration"

    edited_pack_recipe = invoker.invoke(
        app,
        ["pack", "recipe", "edit", "ansible", "playbook-migration"],
    )
    assert edited_pack_recipe.exit_code == 0, edited_pack_recipe.output
    assert marker.read_text().endswith("recipe.yml")


def test_apply_supports_pack_refs_local_pack_recipe_selector_and_backup_refs(
    tmp_path: Path,
) -> None:
    root = library_root()
    recipe_project = RecipeLibrary(root).init("standalone", base_dir=tmp_path)
    (recipe_project / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: templates/out.txt\n"
        "    dest: out.txt\n"
    )
    (recipe_project / "templates" / "out.txt").write_text("standalone\n")
    RecipeLibrary(root).add(recipe_project)

    pack_project = PackLibrary(root).init("ansible", base_dir=tmp_path)
    pack_recipe = PackLibrary(root).init_recipe(pack_project, "playbook")
    pack_recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: templates/out.txt\n"
        "    dest: out.txt\n"
    )
    (pack_recipe.parent / "templates" / "out.txt").write_text("pack\n")
    PackLibrary(root).add(pack_project)
    target = tmp_path / "target"
    target.mkdir()

    installed_pack = CliInvoker().invoke(
        app,
        ["apply", "ansible:playbook", str(target), "--yes", "--format", "json"],
    )
    assert installed_pack.exit_code == 0, installed_pack.output
    assert (target / "out.txt").read_text() == "pack\n"
    assert BackupStore(root / "backups").metadata("latest")["recipe"] == "ansible:playbook"

    local_target = tmp_path / "local-target"
    local_target.mkdir()
    local_pack = CliInvoker().invoke(
        app,
        [
            "apply",
            str(pack_project),
            str(local_target),
            "--recipe",
            "playbook",
            "--yes",
            "--format",
            "json",
        ],
    )
    assert local_pack.exit_code == 0, local_pack.output
    assert (local_target / "out.txt").read_text() == "pack\n"
