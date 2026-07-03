"""Tests for recipe libraries, hook lookup, and backup restore."""

from __future__ import annotations

from pathlib import Path

import pytest

import untaped_recipe.infrastructure.file_writer as file_writer_module
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.backup import BackupStore
from untaped_recipe.infrastructure.file_writer import flush_changes
from untaped_recipe.infrastructure.hook_resolver import BuiltinHookRef, HookResolver, UvHookRef
from untaped_recipe.infrastructure.pack_store import PackLibrary


def test_hook_resolver_uses_recipe_local_then_installed_pack(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    library_root = tmp_path / "library"
    pack_source = tmp_path / "pack-source"
    _write_hook_project(recipe_dir, hook_name="pick", package="local_hooks")
    _write_hook_project(pack_source, hook_name="pick", package="pack_hooks")
    PackLibrary(library_root=library_root).add(
        pack_source,
        source=str(pack_source),
        rev=None,
        name="shared",
        force=False,
    )

    local_ref = HookResolver(library_root=library_root).resolve("pick", recipe_dir)
    installed_ref = HookResolver(library_root=library_root).resolve("pick", None)

    assert isinstance(local_ref, UvHookRef)
    assert local_ref.project_root == recipe_dir
    assert local_ref.module == "local_hooks.hooks.pick"
    assert isinstance(installed_ref, UvHookRef)
    assert installed_ref.project_root == library_root / "packs" / "shared"
    assert installed_ref.module == "pack_hooks.hooks.pick"


def test_hook_resolver_falls_back_to_builtins(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()

    ref = HookResolver(library_root=tmp_path / "library").resolve("yaml_edit", recipe_dir)

    assert isinstance(ref, BuiltinHookRef)


def test_hook_resolver_rejects_hook_paths_that_escape_recipe(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    with pytest.raises(ValueError, match="safe hook name"):
        HookResolver(library_root=tmp_path / "library").resolve("../outside.py", recipe_dir)


def _write_hook_project(
    root: Path,
    *,
    hook_name: str,
    package: str = "project_hooks",
) -> None:
    (root / "src" / package / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / f"{hook_name}.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "project-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.hooks]\n"
        f'"{hook_name}" = {{ module = "{package}.hooks.{hook_name}" }}\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


class _UnusedHooks:
    def validate(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("hook executor should not be used")

    def transform(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("hook executor should not be used")


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


def test_backup_restore_of_crlf_file_does_not_false_trip_hash_guard(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_bytes(b"after one\r\nafter two\r\n")
    target = tmp_path / "target"
    target.mkdir()
    config = target / "config.txt"
    original = b"before one\r\nbefore two\r\n"
    config.write_bytes(original)
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "template", "template": "template.txt", "dest": "config.txt"}],
        }
    )
    planner = ApplyRecipe(_UnusedHooks())  # type: ignore[arg-type]
    plan = planner(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})
    store = BackupStore(tmp_path / "backups")
    draft = store.start(recipe_name="demo", inputs={})
    reservation = draft.stage(plan.changes, inputs={})

    flush_changes(plan.changes)
    draft.commit(reservation)
    store.restore(draft.id)

    assert config.read_bytes() == original


def test_backup_restore_rejects_symlink_escape(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "link").symlink_to(outside, target_is_directory=True)
    escaped = outside / "config.txt"
    escaped.write_text("after\n")
    store = BackupStore(tmp_path / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("link/config.txt"),
                before="before\n",
                after="after\n",
            )
        ],
    )

    with pytest.raises(Exception, match="symlink"):
        store.restore(bundle.id)

    assert escaped.read_text() == "after\n"


def test_backup_restore_rolls_back_prior_files_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    first = target / "one.txt"
    second = target / "two.txt"
    first.write_text("one-before\n")
    second.write_text("two-before\n")
    store = BackupStore(tmp_path / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("one.txt"),
                before="one-before\n",
                after="one-after\n",
            ),
            FileChange(
                target=target,
                relative_path=Path("two.txt"),
                before="two-before\n",
                after="two-after\n",
            ),
        ],
    )
    first.write_text("one-after\n")
    second.write_text("two-after\n")
    original_replace = file_writer_module.os.replace

    def fail_second_replace(source: Path, dest: Path) -> None:
        if Path(dest).name == "two.txt":
            raise OSError("disk full")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_replace)

    with pytest.raises(Exception, match="disk full"):
        store.restore(bundle.id)

    assert first.read_text() == "one-after\n"
    assert second.read_text() == "two-after\n"
