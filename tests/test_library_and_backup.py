"""Tests for recipe libraries, hook lookup, and backup restore."""

from __future__ import annotations

from pathlib import Path

import pytest

import untaped_recipe.infrastructure.file_writer as file_writer_module
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.backup import (
    BackupBundle,
    BackupStore,
    RestoreItem,
    prune_selection,
)
from untaped_recipe.infrastructure.file_writer import flush_changes
from untaped_recipe.infrastructure.hook_resolver import BuiltinHookRef, HookResolver, UvHookRef
from untaped_recipe.infrastructure.pack_store import PackLibrary, pack_content_hash


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


def test_pack_add_ignores_dev_and_build_junk(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    pack_source = tmp_path / "pack-source"
    _write_hook_project(pack_source, hook_name="pick")
    (pack_source / ".venv" / "bin").mkdir(parents=True)
    (pack_source / ".venv" / "bin" / "python").write_text("")
    (pack_source / "__pycache__").mkdir()
    (pack_source / "__pycache__" / "junk.pyc").write_text("")
    (pack_source / "dist").mkdir()
    (pack_source / "dist" / "pack-0.1.0.tar.gz").write_text("")
    (pack_source / "pack.egg-info").mkdir()
    (pack_source / "pack.egg-info" / "PKG-INFO").write_text("")

    PackLibrary(library_root=library_root).add(
        pack_source,
        source=str(pack_source),
        rev=None,
        name="clean",
        force=False,
    )

    installed = library_root / "packs" / "clean"
    assert (installed / "pyproject.toml").is_file()
    assert (installed / "uv.lock").is_file()
    assert not (installed / ".venv").exists()
    assert not (installed / "__pycache__").exists()
    assert not (installed / "dist").exists()
    assert not (installed / "pack.egg-info").exists()


def _add_pack(library_root: Path, source: Path, *, name: str, **kwargs: object) -> None:
    PackLibrary(library_root=library_root).add(
        source,
        source=str(source),
        rev=None,
        name=name,
        force=bool(kwargs.get("force", False)),
        discard_edits=bool(kwargs.get("discard_edits", False)),
    )


def test_pack_add_force_blocks_on_local_edits(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    pack_source = tmp_path / "pack-source"
    _write_hook_project(pack_source, hook_name="pick")
    _add_pack(library_root, pack_source, name="guarded")
    installed = library_root / "packs" / "guarded"
    (installed / "src" / "project_hooks" / "hooks" / "pick.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content + 'edited'\n"
    )

    library = PackLibrary(library_root=library_root)
    assert library.local_edits("guarded") is True
    with pytest.raises(
        ValueError,
        match=r"pack 'guarded' has local edits in the library",
    ):
        _add_pack(library_root, pack_source, name="guarded", force=True)

    _add_pack(library_root, pack_source, name="guarded", force=True, discard_edits=True)
    assert PackLibrary(library_root=library_root).local_edits("guarded") is False
    _add_pack(library_root, pack_source, name="guarded", force=True)


def test_pack_add_force_proceeds_without_local_edits(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    pack_source = tmp_path / "pack-source"
    _write_hook_project(pack_source, hook_name="pick")
    _add_pack(library_root, pack_source, name="clean")

    _add_pack(library_root, pack_source, name="clean", force=True)


def test_pack_add_force_treats_legacy_rows_as_unguarded(tmp_path: Path) -> None:
    library_root = tmp_path / "library"
    pack_source = tmp_path / "pack-source"
    _write_hook_project(pack_source, hook_name="pick")
    _add_pack(library_root, pack_source, name="legacy")
    index_path = library_root / "packs.toml"
    index_path.write_text(
        index_path.read_text(encoding="utf-8").replace("content_hash", "ignored_field"),
        encoding="utf-8",
    )
    installed = library_root / "packs" / "legacy"
    (installed / "src" / "project_hooks" / "hooks" / "pick.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content + 'edited'\n"
    )
    assert PackLibrary(library_root=library_root).local_edits("legacy") is False

    _add_pack(library_root, pack_source, name="legacy", force=True)

    assert "content_hash" in index_path.read_text(encoding="utf-8")
    assert PackLibrary(library_root=library_root).local_edits("legacy") is False


def test_pack_content_hash_reports_unreadable_files_cleanly(tmp_path: Path) -> None:
    import os

    pack_source = tmp_path / "pack-source"
    _write_hook_project(pack_source, hook_name="pick")
    locked = pack_source / "uv.lock"
    locked.chmod(0o000)
    if os.access(locked, os.R_OK):  # running as root; permission bits are advisory
        pytest.skip("cannot make files unreadable as root")
    try:
        with pytest.raises(ValueError, match=r"cannot hash pack file uv\.lock"):
            pack_content_hash(pack_source)
    finally:
        locked.chmod(0o644)


def test_pack_content_hash_ignores_junk_and_sees_edits(tmp_path: Path) -> None:
    pack_source = tmp_path / "pack-source"
    _write_hook_project(pack_source, hook_name="pick")
    before = pack_content_hash(pack_source)

    (pack_source / "__pycache__").mkdir()
    (pack_source / "__pycache__" / "junk.pyc").write_text("junk")
    assert pack_content_hash(pack_source) == before

    (pack_source / "uv.lock").write_text("version = 2\n")
    assert pack_content_hash(pack_source) != before


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


def test_backup_store_plans_restore_actions_and_hash_guard(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    existing = target / "config.yml"
    created = target / "new.txt"
    removed = target / "old.txt"
    existing.write_text("before\n")
    removed.write_text("old\n")
    store = BackupStore(tmp_path / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("config.yml"),
                before="before\n",
                after="after\n",
            ),
            FileChange(target=target, relative_path=Path("new.txt"), before=None, after="new\n"),
            FileChange(target=target, relative_path=Path("old.txt"), before="old\n", after=None),
        ],
    )
    existing.write_text("after\n")
    created.write_text("new\n")
    removed.unlink()

    assert store.plan_restore(bundle.id) == [
        RestoreItem(path=existing, action="restore"),
        RestoreItem(path=created, action="delete"),
        RestoreItem(path=removed, action="create"),
    ]

    existing.write_text("user edit\n")
    with pytest.raises(ValueError, match="changed since backup"):
        store.plan_restore(bundle.id)
    assert created.read_text() == "new\n"
    assert not removed.exists()


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


def test_prune_selection_never_age_prunes_unparsable_ids(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    bundles = [
        BackupBundle(id="custom-name", path=tmp_path / "custom-name"),
        BackupBundle(
            id="20200101T000000000000Z-aaaaaaaa",
            path=tmp_path / "20200101T000000000000Z-aaaaaaaa",
        ),
    ]

    pruned = prune_selection(
        bundles,
        keep=None,
        max_age_days=30,
        now=datetime(2026, 7, 7, tzinfo=UTC),
    )

    assert [bundle.id for bundle in pruned] == ["20200101T000000000000Z-aaaaaaaa"]


def test_prune_selection_unparsable_ids_do_not_consume_keep_slots(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    bundles = [
        BackupBundle(id="aaa-custom", path=tmp_path / "aaa-custom"),
        BackupBundle(
            id="20990101T000000000000Z-bbbbbbbb",
            path=tmp_path / "20990101T000000000000Z-bbbbbbbb",
        ),
        BackupBundle(
            id="20200101T000000000000Z-cccccccc",
            path=tmp_path / "20200101T000000000000Z-cccccccc",
        ),
    ]

    pruned = prune_selection(
        bundles,
        keep=1,
        max_age_days=None,
        now=datetime(2026, 7, 7, tzinfo=UTC),
    )

    assert [bundle.id for bundle in pruned] == ["20200101T000000000000Z-cccccccc"]
