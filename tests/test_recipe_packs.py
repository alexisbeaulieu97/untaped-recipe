"""Tests for uv recipe projects and recipe packs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

import untaped_recipe.infrastructure.pack_library as pack_library_module
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


def test_recipe_yaml_rejects_embedded_name() -> None:
    with pytest.raises(ValueError, match="name"):
        Recipe.model_validate({"version": 1, "name": "demo", "steps": []})


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


def test_recipe_project_metadata_rejects_non_string_pack(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-pack-demo"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe]\n"
        "pack = true\n"
    )

    with pytest.raises(ValueError, match=r"\[tool\.untaped_recipe\]\.pack must be a string"):
        read_recipe_project_metadata(project)


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


def test_recipe_library_rejects_directory_metadata_id_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "library"
    project = root / "recipes" / "demo"
    project.mkdir(parents=True)
    (project / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-other"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"other" = { path = "recipe.yml" }\n'
    )
    (project / "uv.lock").write_text("version = 1\n")

    with pytest.raises(ValueError, match="does not match metadata"):
        RecipeLibrary(root).resolve_detail("demo")


def test_recipe_add_rejects_multi_recipe_standalone_projects(tmp_path: Path) -> None:
    root = tmp_path / "library"
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.yml").write_text("version: 1\nsteps: []\n")
    (source / "two.yml").write_text("version: 1\nsteps: []\n")
    (source / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-multi"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"one" = { path = "one.yml" }\n'
        '"two" = { path = "two.yml" }\n'
    )
    (source / "uv.lock").write_text("version = 1\n")

    with pytest.raises(
        ValueError, match="standalone recipe project must expose exactly one recipe"
    ):
        RecipeLibrary(root).add(source)


def test_pack_library_manages_empty_packs_and_nested_recipes(tmp_path: Path) -> None:
    root = tmp_path / "library"
    pack_root = PackLibrary(root).init("ansible", base_dir=tmp_path)

    metadata = read_recipe_project_metadata(pack_root)
    assert metadata.pack == "ansible"
    assert metadata.recipe_paths() == {}

    recipe_path = PackLibrary(root).init_recipe(pack_root, "playbook-migration")
    metadata = read_recipe_project_metadata(pack_root)

    assert recipe_path == pack_root / "recipes" / "playbook-migration" / "recipe.yml"
    assert metadata.recipe_paths() == {
        "playbook-migration": Path("recipes/playbook-migration/recipe.yml")
    }
    second_recipe_path = PackLibrary(root).init_recipe(pack_root, "inventory-cleanup")
    metadata = read_recipe_project_metadata(pack_root)
    assert second_recipe_path == pack_root / "recipes" / "inventory-cleanup" / "recipe.yml"
    assert metadata.recipe_paths() == {
        "inventory-cleanup": Path("recipes/inventory-cleanup/recipe.yml"),
        "playbook-migration": Path("recipes/playbook-migration/recipe.yml"),
    }
    added = PackLibrary(root).add(pack_root)
    assert added == root / "packs" / "ansible"
    resolution = RecipeLibrary(root).resolve_detail("ansible:playbook-migration")
    assert resolution.ref == "ansible:playbook-migration"

    removed = PackLibrary(root).remove_recipe(pack_root, "playbook-migration")
    assert removed == pack_root / "recipes" / "playbook-migration"
    assert read_recipe_project_metadata(pack_root).recipe_paths() == {
        "inventory-cleanup": Path("recipes/inventory-cleanup/recipe.yml")
    }


@pytest.mark.parametrize(
    ("recipe_id", "recipe_path"),
    [
        ("root", Path("recipe.yml")),
        ("one", Path("recipes") / "shared" / "one.yml"),
    ],
)
def test_pack_recipe_remove_refuses_non_generated_layout_without_deleting_pack(
    tmp_path: Path,
    recipe_id: str,
    recipe_path: Path,
) -> None:
    project = tmp_path / "pack"
    full_recipe_path = project / recipe_path
    full_recipe_path.parent.mkdir(parents=True)
    full_recipe_path.write_text("version: 1\nsteps: []\n")
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-pack-custom"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe]\n"
        'pack = "custom"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        f'"{recipe_id}" = {{ path = "{recipe_path.as_posix()}" }}\n'
    )
    (project / "uv.lock").write_text("version = 1\n")
    before = (project / "pyproject.toml").read_text()

    with pytest.raises(ValueError, match="only generated pack recipe layouts can be removed"):
        PackLibrary(tmp_path / "library").remove_recipe(project, recipe_id)

    assert project.is_dir()
    assert full_recipe_path.is_file()
    assert (project / "pyproject.toml").read_text() == before


def test_pack_recipe_remove_handles_valid_bare_toml_keys(tmp_path: Path) -> None:
    root = tmp_path / "library"
    project = PackLibrary(root).init("ansible", base_dir=tmp_path)
    recipe_path = project / "recipes" / "playbook" / "recipe.yml"
    recipe_path.parent.mkdir(parents=True)
    recipe_path.write_text("version: 1\nsteps: []\n")
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-pack-ansible"\n'
        'version = "0.1.0"\n\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'pack = "ansible"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        'playbook = { path = "recipes/playbook/recipe.yml" }\n'
    )

    removed = PackLibrary(root).remove_recipe(project, "playbook")

    assert removed == project / "recipes" / "playbook"
    assert not removed.exists()
    assert read_recipe_project_metadata(project).recipe_paths() == {}


def test_pack_recipe_init_rolls_back_on_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    pack_root = PackLibrary(root).init("ansible", base_dir=tmp_path)
    before = (pack_root / "pyproject.toml").read_text()

    def fail_lock(project_root: Path) -> None:
        raise ValueError(f"lock failed: {project_root}")

    monkeypatch.setattr(pack_library_module, "lock_project", fail_lock, raising=False)

    with pytest.raises(ValueError, match="lock failed"):
        PackLibrary(root).init_recipe(pack_root, "broken")

    assert (pack_root / "pyproject.toml").read_text() == before
    assert not (pack_root / "recipes" / "broken").exists()


def test_pack_recipe_init_refuses_existing_recipe_dir_without_deleting_contents(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    pack_root = PackLibrary(root).init("ansible", base_dir=tmp_path)
    recipe_dir = pack_root / "recipes" / "broken"
    recipe_dir.mkdir()
    notes = recipe_dir / "notes.md"
    notes.write_text("keep\n")

    with pytest.raises(ValueError, match="pack recipe directory already exists"):
        PackLibrary(root).init_recipe(pack_root, "broken")

    assert notes.read_text() == "keep\n"
    assert read_recipe_project_metadata(pack_root).recipe_paths() == {}


def test_pack_recipe_remove_rolls_back_on_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    pack_root = PackLibrary(root).init("ansible", base_dir=tmp_path)
    recipe_path = PackLibrary(root).init_recipe(pack_root, "playbook")
    before = (pack_root / "pyproject.toml").read_text()

    def fail_lock(project_root: Path) -> None:
        raise ValueError(f"lock failed: {project_root}")

    monkeypatch.setattr(pack_library_module, "lock_project", fail_lock, raising=False)

    with pytest.raises(ValueError, match="lock failed"):
        PackLibrary(root).remove_recipe(pack_root, "playbook")

    assert (pack_root / "pyproject.toml").read_text() == before
    assert recipe_path.is_file()


def test_pack_recipe_remove_preserves_replacement_dir_on_rollback_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "library"
    pack_root = PackLibrary(root).init("ansible", base_dir=tmp_path)
    recipe_path = PackLibrary(root).init_recipe(pack_root, "playbook")
    recipe_dir = recipe_path.parent
    before = (pack_root / "pyproject.toml").read_text()

    def fail_lock_with_conflict(project_root: Path) -> None:
        recipe_dir.mkdir()
        (recipe_dir / "replacement.md").write_text("keep replacement\n")
        raise ValueError("lock failed")

    monkeypatch.setattr(pack_library_module, "lock_project", fail_lock_with_conflict)

    with pytest.raises(ValueError, match="rollback incomplete"):
        PackLibrary(root).remove_recipe(pack_root, "playbook")

    assert (pack_root / "pyproject.toml").read_text() == before
    assert (recipe_dir / "replacement.md").read_text() == "keep replacement\n"
    backups = list((pack_root / "recipes").glob(".playbook.remove-tmp-*"))
    assert len(backups) == 1
    assert (backups[0] / "recipe.yml").is_file()


def test_pack_library_reports_error_paths(tmp_path: Path) -> None:
    root = tmp_path / "library"
    library = PackLibrary(root)
    pack_root = library.init("ansible", base_dir=tmp_path)
    (pack_root / "uv.lock").unlink()

    with pytest.raises(ValueError, match=r"missing uv\.lock"):
        library.add(pack_root)
    with pytest.raises(ValueError, match="pack not found"):
        library.resolve("missing")

    standalone = RecipeLibrary(root).init("demo", base_dir=tmp_path)
    with pytest.raises(ValueError, match="requires a recipe pack project"):
        library.add(standalone)

    (pack_root / "uv.lock").write_text("version = 1\n")
    library.add(pack_root)
    with pytest.raises(ValueError, match="already exists"):
        library.add(pack_root)
    with pytest.raises(ValueError, match="pack recipe not found"):
        library.recipe_path("ansible", "missing")


def test_malformed_pack_recipe_ref_is_reported_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pack recipe refs must use <pack>:<recipe>"):
        RecipeLibrary(tmp_path / "library").resolve_detail("ansible:playbook:extra")


@pytest.mark.parametrize("ref", ["missing:recipe", "pack:", "pack:recipe:extra"])
@pytest.mark.parametrize(
    "command",
    [
        ("show",),
        ("check", "--format", "json"),
        ("edit",),
        ("remove", "--yes"),
    ],
)
def test_recipe_commands_reject_pack_refs_before_library_resolution(
    ref: str,
    command: tuple[str, ...],
) -> None:
    result = CliInvoker().invoke(app, ["recipe", *command, ref])

    assert result.exit_code != 0
    if command[0] == "check":
        rows = json.loads(result.stdout)
        assert "pack recipes are managed with pack recipe" in rows[0]["error"]
    else:
        assert "pack recipes are managed with pack recipe" in result.output


def test_recipe_check_error_row_uses_existing_project_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (project / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"demo" = { path = "custom.yml" }\n'
    )

    result = CliInvoker().invoke(app, ["recipe", "check", str(project), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["path"] == str(project)
    assert "recipe file not found: custom.yml" in rows[0]["error"]


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

    recipe_show_pack_ref = invoker.invoke(app, ["recipe", "show", "ansible:playbook-migration"])
    assert recipe_show_pack_ref.exit_code != 0
    assert "pack recipes are managed with pack recipe" in recipe_show_pack_ref.output
    recipe_check_pack_ref = invoker.invoke(
        app,
        ["recipe", "check", "ansible:playbook-migration", "--format", "json"],
    )
    assert recipe_check_pack_ref.exit_code == 1, recipe_check_pack_ref.output
    check_rows = json.loads(recipe_check_pack_ref.stdout)
    assert "pack recipes are managed with pack recipe" in check_rows[0]["error"]
    recipe_edit_pack_ref = invoker.invoke(app, ["recipe", "edit", "ansible:playbook-migration"])
    assert recipe_edit_pack_ref.exit_code != 0
    assert "pack recipes are managed with pack recipe" in recipe_edit_pack_ref.output

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
