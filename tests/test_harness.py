"""Tests for golden-fixture harness discovery and specs."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.application.harness import (
    discover_cases,
    load_case_spec,
    orphaned_test_dirs,
)
from untaped_recipe.domain.pack import PackManifest
from untaped_recipe.infrastructure.pack_store import InstalledPack


def _write_pack(
    root: Path,
    *,
    recipes: dict[str, str],
    recipe_bodies: dict[str, str] | None = None,
) -> InstalledPack:
    """Write a minimal pack project and wrap it as a local InstalledPack."""
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, relative in recipes.items():
        recipe_path = root / relative
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        body = (recipe_bodies or {}).get(name, "version: 1\nsteps: []\n")
        recipe_path.write_text(body, encoding="utf-8")
        rows.append(f'"{name}" = {{ path = "{relative}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return InstalledPack.local(root, PackManifest.from_pyproject(root))


def _write_case(pack_root: Path, recipe: str, case: str, *, case_yml: str | None = None) -> Path:
    case_dir = pack_root / "tests" / recipe / case
    (case_dir / "given").mkdir(parents=True)
    if case_yml is not None:
        (case_dir / "case.yml").write_text(case_yml, encoding="utf-8")
    return case_dir


def test_discover_cases_lists_cases_per_manifest_recipe_sorted(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"},
    )
    _write_case(pack.root, "two", "beta")
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "one", "gamma")

    cases = discover_cases(pack)

    assert [(case.recipe_name, case.case_name) for case in cases] == [
        ("one", "alpha"),
        ("one", "gamma"),
        ("two", "beta"),
    ]
    assert cases[0].pack_name == "demo"
    assert cases[0].recipe_path == pack.root / "recipes/one.yml"
    assert cases[0].case_dir == pack.root / "tests" / "one" / "alpha"


def test_discover_cases_scopes_to_one_recipe_and_ignores_files_and_hidden_dirs(
    tmp_path: Path,
) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"},
    )
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "two", "beta")
    (pack.root / "tests" / "one" / "README.md").write_text("notes\n", encoding="utf-8")
    (pack.root / "tests" / "one" / ".hidden").mkdir()

    cases = discover_cases(pack, recipe="one")

    assert [(case.recipe_name, case.case_name) for case in cases] == [("one", "alpha")]


def test_orphaned_test_dirs_flags_dirs_naming_no_recipe(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml"})
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "renamed", "old")
    (pack.root / "tests" / ".cache").mkdir()

    assert orphaned_test_dirs(pack) == ["renamed"]


def test_orphaned_test_dirs_is_empty_without_tests_dir(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml"})

    assert orphaned_test_dirs(pack) == []


def test_load_case_spec_defaults_when_file_missing(tmp_path: Path) -> None:
    assert load_case_spec(tmp_path).expect == "success"


def test_load_case_spec_rejects_non_mapping_and_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "case.yml").write_text("- not\n- a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"case\.yml must contain a YAML mapping"):
        load_case_spec(tmp_path)

    (tmp_path / "case.yml").write_text("expect: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid case\.yml"):
        load_case_spec(tmp_path)


def test_load_case_spec_rejects_unknown_fields(tmp_path: Path) -> None:
    (tmp_path / "case.yml").write_text("targets: [a.yml]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid case\.yml"):
        load_case_spec(tmp_path)
