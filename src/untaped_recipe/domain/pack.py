"""Pack identity, manifest parsing, and qualified reference helpers."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name
from pydantic import BaseModel, ConfigDict, field_validator

from untaped_recipe.domain.hook_project import HookProjectMetadata

PACK_PROJECT_PREFIX = "untaped-recipe-"
_PACK_PROJECT_BARE_NAME = PACK_PROJECT_PREFIX.removesuffix("-")


def pack_name_from_project(project_name: str) -> str:
    """Return the public pack name implied by a Python project name."""
    normalized = str(canonicalize_name(project_name))
    if not normalized or normalized == _PACK_PROJECT_BARE_NAME:
        raise ValueError("pack project name must include a pack name")
    if normalized.startswith(PACK_PROJECT_PREFIX):
        pack_name = normalized[len(PACK_PROJECT_PREFIX) :]
        if not pack_name:
            raise ValueError("pack project name must include a pack name")
        return pack_name
    return normalized


class RecipeEntry(BaseModel):
    """One recipe exposed by a pack manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str

    @field_validator("path")
    @classmethod
    def _path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recipe path must not be empty")
        return value


class HookEntry(BaseModel):
    """One hook exposed by a pack manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    module: str


class PackManifest(BaseModel):
    """Parsed pack metadata from a uv project's pyproject."""

    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    requires_hook_api: str | None = None
    recipes: dict[str, RecipeEntry]
    hooks: dict[str, HookEntry]
    runtime_dependencies: tuple[str, ...] = ()

    @classmethod
    def from_pyproject(cls, project_root: Path) -> PackManifest:
        """Read and parse one pack manifest from ``project_root``."""
        pyproject = project_root / "pyproject.toml"
        if not pyproject.is_file():
            raise ValueError(f"pack project must contain pyproject.toml: {project_root}")
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid pack project pyproject: {pyproject}") from exc

        project = _mapping(data.get("project"), "project")
        if project is None:
            raise ValueError(f"pack project pyproject missing [project]: {pyproject}")
        raw_name = project.get("name")
        if not isinstance(raw_name, str):
            raise ValueError("[project].name must be a string")
        raw_version = project.get("version", "0")
        if not isinstance(raw_version, str):
            raise ValueError("[project].version must be a string")

        tool_config = _nested_mapping(data, ("tool", "untaped_recipe"))
        if tool_config is None:
            raise ValueError(f"pack project pyproject missing [tool.untaped_recipe]: {pyproject}")
        if not isinstance(tool_config, Mapping):
            raise ValueError("[tool.untaped_recipe] must be a table")

        recipes = tool_config.get("recipes")
        if recipes is None:
            recipe_entries: dict[str, object] = {}
        elif isinstance(recipes, Mapping):
            recipe_entries = dict(recipes)
        else:
            raise ValueError("[tool.untaped_recipe.recipes] must be a table")

        hook_metadata = HookProjectMetadata.from_pyproject(data)
        hook_entries = {
            name: HookEntry(module=definition.module)
            for name, definition in hook_metadata.hooks.items()
        }
        return cls(
            name=pack_name_from_project(raw_name),
            version=raw_version,
            requires_hook_api=hook_metadata.requires_hook_api,
            recipes=recipe_entries,
            hooks=hook_entries,
            runtime_dependencies=hook_metadata.runtime_dependencies,
        )


@dataclass(frozen=True)
class PackRef:
    """A bare or pack-qualified recipe/hook reference."""

    pack: str | None
    name: str


def parse_ref(text: str) -> PackRef:
    """Parse ``name`` or ``pack/name`` into a structured reference."""
    parts = text.split("/")
    if len(parts) == 1:
        name = parts[0]
        if not name or name == "..":
            raise ValueError("qualified refs must use <pack>/<name>")
        return PackRef(pack=None, name=name)
    if len(parts) != 2:
        raise ValueError("qualified refs must use <pack>/<name>")
    pack, name = parts
    if not pack or not name or pack == ".." or name == "..":
        raise ValueError("qualified refs must use <pack>/<name>")
    return PackRef(pack=pack, name=name)


def _nested_mapping(data: Mapping[str, object], path: tuple[str, ...]) -> object | None:
    current: object = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _mapping(value: object, field: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"[{field}] must be a table")
    return value
