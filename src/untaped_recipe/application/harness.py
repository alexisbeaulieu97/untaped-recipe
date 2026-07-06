"""Golden-fixture test harness for recipe packs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from untaped_recipe.domain.testcase import CaseSpec
from untaped_recipe.infrastructure.pack_store import InstalledPack


@dataclass(frozen=True)
class DiscoveredCase:
    """One golden case directory resolved against a pack manifest."""

    pack_name: str
    pack_root: Path
    recipe_name: str
    recipe_path: Path
    case_name: str
    case_dir: Path


def discover_cases(pack: InstalledPack, *, recipe: str | None = None) -> list[DiscoveredCase]:
    """List golden cases for one pack, optionally scoped to one recipe."""
    tests_dir = pack.root / "tests"
    names = [recipe] if recipe is not None else sorted(pack.manifest.recipes)
    cases: list[DiscoveredCase] = []
    for name in names:
        entry = pack.manifest.recipes.get(name)
        if entry is None:
            continue
        recipe_tests = tests_dir / name
        if not recipe_tests.is_dir():
            continue
        for case_dir in sorted(recipe_tests.iterdir(), key=lambda path: path.name):
            if not case_dir.is_dir() or case_dir.name.startswith("."):
                continue
            cases.append(
                DiscoveredCase(
                    pack_name=pack.name,
                    pack_root=pack.root,
                    recipe_name=name,
                    recipe_path=pack.root / entry.path,
                    case_name=case_dir.name,
                    case_dir=case_dir,
                )
            )
    return cases


def orphaned_test_dirs(pack: InstalledPack) -> list[str]:
    """Return tests/ subdirectories that name no recipe in the manifest."""
    tests_dir = pack.root / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in tests_dir.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in pack.manifest.recipes
    )


def load_case_spec(case_dir: Path) -> CaseSpec:
    """Parse an optional case.yml; absent file means all defaults."""
    case_file = case_dir / "case.yml"
    if not case_file.is_file():
        return CaseSpec()
    try:
        loaded = yaml.safe_load(case_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid case.yml: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("case.yml must contain a YAML mapping")
    try:
        return CaseSpec.model_validate(loaded)
    except ValidationError as exc:
        raise ValueError(f"invalid case.yml: {exc}") from exc
