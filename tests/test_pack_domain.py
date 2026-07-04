"""Tests for pack identity, manifests, and qualified references."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.domain.pack import (
    PackManifest,
    PackRef,
    pack_name_from_project,
    parse_ref,
)


def _write_pyproject(root: Path, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(content, encoding="utf-8")


def test_pack_name_from_project_strips_prefix_and_canonicalizes() -> None:
    assert pack_name_from_project("untaped-recipe-ansible") == "ansible"
    assert pack_name_from_project("Untaped_Recipe_AWX_Tools") == "awx-tools"


@pytest.mark.parametrize("project_name", ["", "untaped-recipe", "untaped_recipe"])
def test_pack_name_from_project_rejects_bare_prefix(project_name: str) -> None:
    with pytest.raises(ValueError, match="pack project name must include a pack name"):
        pack_name_from_project(project_name)


def test_pack_manifest_parses_recipes_hooks_and_project_metadata(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        "[project]\n"
        'name = "untaped-recipe-ansible"\n'
        'version = "0.1.0"\n'
        'dependencies = ["requests>=2"]\n\n'
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }\n',
    )

    manifest = PackManifest.from_pyproject(tmp_path)

    assert manifest.name == "ansible"
    assert manifest.version == "0.1.0"
    assert manifest.requires_hook_api == ">=0.9,<1"
    assert manifest.runtime_dependencies == ("requests>=2",)
    assert manifest.recipes["playbook-migration"].path == "recipes/playbook-migration/recipe.yml"
    assert manifest.hooks["add_play_collections"].module == (
        "ansible_hooks.hooks.add_play_collections"
    )


def test_pack_manifest_tables_are_optional_when_tool_table_exists(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        '[project]\nname = "untaped-recipe-empty"\n\n[tool.untaped_recipe]\n',
    )

    manifest = PackManifest.from_pyproject(tmp_path)

    assert manifest.name == "empty"
    assert manifest.version == "0"
    assert manifest.recipes == {}
    assert manifest.hooks == {}


def test_pack_manifest_parses_one_table_without_the_other(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        "[project]\n"
        'name = "untaped-recipe-hooks-only"\n\n'
        "[tool.untaped_recipe]\n\n"
        "[tool.untaped_recipe.hooks]\n"
        '"check" = { module = "hooks.check" }\n',
    )

    manifest = PackManifest.from_pyproject(tmp_path)

    assert manifest.recipes == {}
    assert manifest.hooks["check"].module == "hooks.check"


def test_pack_manifest_requires_tool_table(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        '[project]\nname = "untaped-recipe-missing-tool"\n',
    )

    with pytest.raises(ValueError, match=r"missing \[tool\.untaped_recipe\].*pyproject\.toml"):
        PackManifest.from_pyproject(tmp_path)


def test_pack_manifest_rejects_hook_kind_with_shared_error(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        "[project]\n"
        'name = "untaped-recipe-legacy"\n\n'
        "[tool.untaped_recipe]\n\n"
        "[tool.untaped_recipe.hooks]\n"
        '"check" = { kind = "validate", module = "hooks.check" }\n',
    )

    with pytest.raises(ValueError, match=r"kind was removed in 0\.9"):
        PackManifest.from_pyproject(tmp_path)


def test_parse_ref_accepts_qualified_and_bare_refs() -> None:
    assert parse_ref("ansible/set_owner") == PackRef(pack="ansible", name="set_owner")
    assert parse_ref("set_owner") == PackRef(pack=None, name="set_owner")


@pytest.mark.parametrize("text", ["a/b/c", "a/", "/b", "../x", "a/.."])
def test_parse_ref_rejects_path_like_refs(text: str) -> None:
    with pytest.raises(ValueError, match="qualified refs must use <pack>/<name>"):
        parse_ref(text)
