"""Tests for recipe libraries, hook lookup, and backup restore."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.domain.plan import FileChange
from untaped_recipe.infrastructure.backup import BackupStore
from untaped_recipe.infrastructure.hook_library import HookLibrary
from untaped_recipe.infrastructure.hook_loader import HookLoader
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary


def test_recipe_library_resolves_name_before_path_and_copies_packages(tmp_path: Path) -> None:
    root = tmp_path / "library"
    source = tmp_path / "source-recipe"
    source.mkdir()
    (source / "recipe.yml").write_text("version: 1\nname: copied\nsteps: []\n")
    library = RecipeLibrary(root)

    copied = library.add(source, name="copied")

    assert copied == root / "recipes" / "copied" / "recipe.yml"
    assert library.resolve("copied") == copied
    explicit = tmp_path / "copied"
    explicit.write_text("version: 1\nname: explicit\nsteps: []\n")
    assert library.resolve(str(explicit)) == explicit
    assert [entry.name for entry in library.list()] == ["copied"]

    invalid_package = tmp_path / "invalid"
    invalid_package.mkdir()
    with pytest.raises(ValueError, match=r"recipe\.yml"):
        library.add(invalid_package, name="invalid")


def test_recipe_and_hook_libraries_reject_unsafe_names(tmp_path: Path) -> None:
    recipe_source = tmp_path / "recipe.yml"
    recipe_source.write_text("version: 1\nname: demo\nsteps: []\n")
    hook_source = tmp_path / "hook.py"
    hook_source.write_text("VALUE = 1\n")
    root = tmp_path / "library"

    recipe_library = RecipeLibrary(root)
    hook_library = HookLibrary(root)

    with pytest.raises(ValueError, match="safe library name"):
        recipe_library.add(recipe_source, name="../outside")
    with pytest.raises(ValueError, match="safe library name"):
        recipe_library.remove("/tmp/outside")
    with pytest.raises(ValueError, match="safe library name"):
        hook_library.add(hook_source, name="../outside")
    with pytest.raises(ValueError, match="safe library name"):
        hook_library.remove("/tmp/outside")


def test_hook_loader_uses_recipe_local_then_global_then_builtin(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_hooks = recipe_dir / "hooks"
    global_hooks = tmp_path / "global"
    builtins = tmp_path / "builtins"
    recipe_hooks.mkdir(parents=True)
    global_hooks.mkdir()
    builtins.mkdir()
    (recipe_hooks / "pick.py").write_text("VALUE = 'local'\n")
    (global_hooks / "pick.py").write_text("VALUE = 'global'\n")
    (builtins / "pick.py").write_text("VALUE = 'builtin'\n")

    module = HookLoader(global_hooks=global_hooks, builtins=(builtins,)).load("pick", recipe_dir)

    assert module.VALUE == "local"


def test_hook_loader_caches_modules_by_resolved_path(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_hooks = recipe_dir / "hooks"
    recipe_hooks.mkdir(parents=True)
    (recipe_hooks / "pick.py").write_text("VALUE = object()\n")
    loader = HookLoader(global_hooks=tmp_path / "global", builtins=())

    first = loader.load("pick", recipe_dir)
    second = loader.load("pick", recipe_dir)

    assert first is second


def test_hook_loader_rejects_hook_paths_that_escape_recipe(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    with pytest.raises(ValueError, match="safe hook name"):
        HookLoader(global_hooks=tmp_path / "global", builtins=()).load("../outside.py", recipe_dir)


def test_backup_store_records_and_restores_touched_files(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    existing = target / "config.yml"
    created = target / "new.txt"
    removed = target / "old.txt"
    existing.write_text("before\n")
    removed.write_text("old\n")
    changes = [
        FileChange(
            target=target,
            relative_path=Path("config.yml"),
            before="before\n",
            after="after\n",
        ),
        FileChange(target=target, relative_path=Path("new.txt"), before=None, after="new\n"),
        FileChange(target=target, relative_path=Path("old.txt"), before="old\n", after=None),
    ]
    store = BackupStore(tmp_path / "backups")

    bundle = store.create(
        recipe_name="demo",
        inputs={"x": 1},
        changes=changes,
    )
    existing.write_text("after\n")
    created.write_text("new\n")
    removed.unlink()

    store.restore(bundle.id[:8])

    assert existing.read_text() == "before\n"
    assert not created.exists()
    assert removed.read_text() == "old\n"

    existing.write_text("user edit\n")
    with pytest.raises(ValueError, match="changed since backup"):
        store.restore(bundle.id)
    store.restore("latest", force=True)
    assert existing.read_text() == "before\n"
