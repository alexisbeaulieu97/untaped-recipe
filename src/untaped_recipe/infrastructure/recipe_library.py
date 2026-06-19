"""Local recipe library storage."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from untaped_recipe.domain.paths import safe_library_name
from untaped_recipe.domain.recipe import Recipe


@dataclass(frozen=True)
class RecipeEntry:
    """One recipe library entry."""

    name: str
    path: Path
    kind: str


class RecipeLibrary:
    """Manage local recipe packages."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def recipes_dir(self) -> Path:
        """Directory containing recipe packages."""
        return self._root / "recipes"

    def resolve(self, recipe: str) -> Path:
        """Resolve a recipe name or path."""
        try:
            recipe_name = safe_library_name(recipe, field="recipe")
        except ValueError:
            recipe_name = ""
        if recipe_name:
            package = self.recipes_dir / recipe_name / "recipe.yml"
            if package.is_file():
                return package
            single = self.recipes_dir / f"{recipe_name}.yml"
            if single.is_file():
                return single
        path = Path(recipe).expanduser()
        if path.is_file():
            return path
        if path.is_dir() and (path / "recipe.yml").is_file():
            return path / "recipe.yml"
        raise ValueError(f"recipe not found: {recipe}")

    def add(self, source: Path, *, name: str | None = None) -> Path:
        """Copy a local recipe file or package into the library."""
        source = source.expanduser()
        self.recipes_dir.mkdir(parents=True, exist_ok=True)
        recipe_name = safe_library_name(name or source.stem)
        if source.is_dir():
            recipe_file = source / "recipe.yml"
            if not recipe_file.is_file():
                raise ValueError(f"recipe package must contain recipe.yml: {source}")
            _validate_recipe_file(recipe_file)
            dest_dir = self.recipes_dir / recipe_name
            if dest_dir.exists():
                raise ValueError(f"recipe already exists: {recipe_name}")
            shutil.copytree(source, dest_dir)
            return dest_dir / "recipe.yml"
        if not source.is_file():
            raise ValueError(f"recipe source not found: {source}")
        _validate_recipe_file(source)
        dest = self.recipes_dir / f"{recipe_name}.yml"
        if dest.exists():
            raise ValueError(f"recipe already exists: {recipe_name}")
        shutil.copy2(source, dest)
        return dest

    def remove(self, name: str) -> Path:
        """Remove a recipe from the library."""
        recipe_name = safe_library_name(name, field="recipe")
        package_dir = self.recipes_dir / recipe_name
        single = self.recipes_dir / f"{recipe_name}.yml"
        if package_dir.is_dir():
            shutil.rmtree(package_dir)
            return package_dir
        if single.is_file():
            single.unlink()
            return single
        raise ValueError(f"recipe not found: {name}")

    def list(self) -> list[RecipeEntry]:
        """List recipes in the library."""
        if not self.recipes_dir.is_dir():
            return []
        entries: list[RecipeEntry] = []
        for child in sorted(self.recipes_dir.iterdir(), key=lambda p: p.name):
            if child.is_dir() and (child / "recipe.yml").is_file():
                entries.append(
                    RecipeEntry(
                        name=child.name,
                        path=child / "recipe.yml",
                        kind="package",
                    )
                )
            elif child.is_file() and child.suffix in {".yml", ".yaml"}:
                entries.append(RecipeEntry(name=child.stem, path=child, kind="file"))
        return entries


def _validate_recipe_file(path: Path) -> None:
    Recipe.model_validate(yaml.safe_load(path.read_text()) or {})
