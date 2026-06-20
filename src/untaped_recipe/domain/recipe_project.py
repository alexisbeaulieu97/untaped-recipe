"""Metadata for uv-backed recipe and pack projects."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any, cast

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tomlkit.exceptions import ParseError
from tomlkit.toml_document import TOMLDocument

from untaped_recipe.domain.paths import safe_library_name, safe_relative_path


class RecipeDefinition(BaseModel):
    """One recipe exposed by a uv recipe or pack project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path

    @field_validator("path", mode="before")
    @classmethod
    def _path_is_string(cls, value: object) -> object:
        if not isinstance(value, str | Path):
            raise ValueError("recipe path must be a string")
        return value

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: Path) -> Path:
        return safe_relative_path(value, field="recipe path")


class RecipeProjectMetadata(BaseModel):
    """Parsed ``[tool.untaped_recipe]`` recipe project metadata."""

    model_config = ConfigDict(frozen=True)

    pack: str | None = None
    recipes: dict[str, RecipeDefinition] = Field(default_factory=dict)

    @field_validator("pack")
    @classmethod
    def _safe_pack(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return safe_library_name(value, field="pack")

    @field_validator("recipes")
    @classmethod
    def _safe_recipe_ids(
        cls,
        value: dict[str, RecipeDefinition],
    ) -> dict[str, RecipeDefinition]:
        for recipe_id in value:
            safe_library_name(recipe_id, field="recipe")
        return value

    @classmethod
    def from_pyproject(cls, data: Mapping[str, object]) -> RecipeProjectMetadata:
        """Build recipe project metadata from parsed pyproject data."""
        tool = _mapping(data.get("tool"), "tool")
        if tool is None:
            return cls()
        untaped = _mapping(tool.get("untaped_recipe"), "tool.untaped_recipe")
        if untaped is None:
            return cls()
        raw_pack = untaped.get("pack")
        if raw_pack is not None and not isinstance(raw_pack, str):
            raise ValueError("[tool.untaped_recipe].pack must be a string")
        pack = raw_pack
        raw_recipes = untaped.get("recipes")
        if raw_recipes is None:
            recipes: dict[str, object] = {}
        elif isinstance(raw_recipes, Mapping):
            recipes = dict(raw_recipes)
        else:
            raise ValueError("[tool.untaped_recipe.recipes] must be a table")
        return cls(pack=pack, recipes=recipes)

    def recipe_paths(self) -> dict[str, Path]:
        """Return exposed recipe ids mapped to project-relative paths."""
        return {recipe_id: definition.path for recipe_id, definition in self.recipes.items()}


def read_recipe_project_metadata(project_root: Path) -> RecipeProjectMetadata:
    """Read recipe or pack metadata from a uv project's pyproject."""
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ValueError(f"recipe project must contain pyproject.toml: {project_root}")
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid recipe project pyproject: {pyproject}") from exc
    return RecipeProjectMetadata.from_pyproject(data)


def append_recipe_metadata(project_root: Path, recipe_id: str, relative_path: Path) -> None:
    """Append one exposed recipe entry to a generated project pyproject."""
    safe_library_name(recipe_id, field="recipe")
    relative_path = safe_relative_path(relative_path, field="recipe path")
    metadata = read_recipe_project_metadata(project_root)
    if recipe_id in metadata.recipes:
        raise ValueError(f"recipe already exists in project metadata: {recipe_id}")
    pyproject = project_root / "pyproject.toml"
    doc = _read_toml_document(pyproject)
    recipes = _recipes_table(doc, create=True)
    if recipes is None:
        raise ValueError("[tool.untaped_recipe.recipes] must be a table")
    entry = tomlkit.inline_table()
    entry["path"] = relative_path.as_posix()
    recipes[recipe_id] = entry
    pyproject.write_text(doc.as_string())


def remove_recipe_metadata(project_root: Path, recipe_id: str) -> None:
    """Remove one generated recipe metadata table from a project pyproject."""
    safe_library_name(recipe_id, field="recipe")
    pyproject = project_root / "pyproject.toml"
    metadata = read_recipe_project_metadata(project_root)
    if recipe_id not in metadata.recipes:
        raise ValueError(f"recipe not found in project metadata: {recipe_id}")
    doc = _read_toml_document(pyproject)
    recipes = _recipes_table(doc, create=False)
    if recipes is None or recipe_id not in recipes:
        raise ValueError(f"recipe not found in project metadata: {recipe_id}")
    del recipes[recipe_id]
    pyproject.write_text(doc.as_string())


def _mapping(value: object, field: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"[{field}] must be a table")
    return value


def _read_toml_document(path: Path) -> TOMLDocument:
    try:
        return tomlkit.loads(path.read_text())
    except ParseError as exc:
        raise ValueError(f"invalid recipe project pyproject: {path}") from exc


def _recipes_table(
    doc: TOMLDocument,
    *,
    create: bool,
) -> MutableMapping[str, Any] | None:
    tool = _toml_table(doc, "tool", "tool", create=create)
    if tool is None:
        return None
    untaped = _toml_table(tool, "untaped_recipe", "tool.untaped_recipe", create=create)
    if untaped is None:
        return None
    return _toml_table(
        untaped,
        "recipes",
        "tool.untaped_recipe.recipes",
        create=create,
    )


def _toml_table(
    container: MutableMapping[str, Any],
    key: str,
    field: str,
    *,
    create: bool,
) -> MutableMapping[str, Any] | None:
    value = container.get(key)
    if value is None:
        if not create:
            return None
        table = tomlkit.table()
        container[key] = table
        return cast(MutableMapping[str, Any], table)
    if not isinstance(value, MutableMapping):
        raise ValueError(f"[{field}] must be a table")
    return cast(MutableMapping[str, Any], value)
