"""Metadata for uv-backed recipe and pack projects."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from untaped_recipe.domain.paths import safe_library_name, safe_relative_path


class RecipeDefinition(BaseModel):
    """One recipe exposed by a uv recipe or pack project."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path

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
        pack = str(raw_pack) if raw_pack is not None else None
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
    _append_recipe_table_entry(project_root / "pyproject.toml", recipe_id, relative_path)


def remove_recipe_metadata(project_root: Path, recipe_id: str) -> None:
    """Remove one generated recipe metadata table from a project pyproject."""
    safe_library_name(recipe_id, field="recipe")
    pyproject = project_root / "pyproject.toml"
    lines = pyproject.read_text().splitlines()
    if not _remove_recipe_table_entry(lines, recipe_id):
        header = f'[tool.untaped_recipe.recipes."{recipe_id}"]'
        start = _find_header(lines, header)
        if start is None:
            raise ValueError(f"recipe not found in project metadata: {recipe_id}")
        end = start + 1
        while end < len(lines) and not lines[end].startswith("["):
            end += 1
        del lines[start:end]
    while lines and lines[-1] == "":
        lines.pop()
    pyproject.write_text("\n".join(lines) + "\n")


def _mapping(value: object, field: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"[{field}] must be a table")
    return value


def _append_recipe_table_entry(path: Path, recipe_id: str, relative_path: Path) -> None:
    entry = f'"{recipe_id}" = {{ path = "{relative_path.as_posix()}" }}'
    lines = path.read_text().splitlines()
    header_index = _find_header(lines, "[tool.untaped_recipe.recipes]")
    if header_index is None:
        while lines and lines[-1] == "":
            lines.pop()
        lines.extend(["", "[tool.untaped_recipe.recipes]", entry])
        path.write_text("\n".join(lines) + "\n")
        return
    insert_at = header_index + 1
    while insert_at < len(lines) and not lines[insert_at].startswith("["):
        insert_at += 1
    lines.insert(insert_at, entry)
    path.write_text("\n".join(lines) + "\n")


def _remove_recipe_table_entry(lines: list[str], recipe_id: str) -> bool:
    header_index = _find_header(lines, "[tool.untaped_recipe.recipes]")
    if header_index is None:
        return False
    index = header_index + 1
    prefix = f'"{recipe_id}"'
    while index < len(lines) and not lines[index].startswith("["):
        if lines[index].strip().startswith(prefix):
            del lines[index]
            return True
        index += 1
    return False


def _find_header(lines: list[str], header: str) -> int | None:
    for index, line in enumerate(lines):
        if line.strip() == header:
            return index
    return None
