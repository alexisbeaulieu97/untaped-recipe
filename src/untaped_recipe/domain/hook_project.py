"""Models for uv-managed hook project metadata."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

_DOTTED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


class HookDefinition(BaseModel):
    """One public hook entry in a hook project's pyproject metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    module: str = ""

    @field_validator("module")
    @classmethod
    def _module_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("module is required")
        if not is_valid_dotted_name(value):
            raise ValueError(f"invalid module name: {value}")
        return value


class HookProjectMetadata(BaseModel):
    """Parsed `[tool.untaped_recipe.hooks]` table."""

    model_config = ConfigDict(frozen=True)

    hooks: dict[str, HookDefinition]

    @field_validator("hooks")
    @classmethod
    def _hook_names(cls, value: dict[str, HookDefinition]) -> dict[str, HookDefinition]:
        for name in value:
            if not is_valid_dotted_name(name):
                raise ValueError(f"invalid hook name: {name}")
        return value

    @classmethod
    def from_pyproject(cls, data: Mapping[str, object]) -> HookProjectMetadata:
        """Build hook metadata from parsed pyproject data."""
        hooks = _nested_mapping(data, ("tool", "untaped_recipe", "hooks"))
        if hooks is None:
            return cls(hooks={})
        if not isinstance(hooks, Mapping):
            raise ValueError("[tool.untaped_recipe.hooks] must be a table")
        return cls(hooks=dict(hooks))


def is_valid_dotted_name(name: str) -> bool:
    """Return true when ``name`` is a safe dotted hook/module identifier."""
    return bool(_DOTTED_NAME_RE.fullmatch(name.strip()))


def normalize_hook_name(name: str) -> str:
    """Validate and return a public hook name."""
    normalized = name.strip()
    if not is_valid_dotted_name(normalized):
        raise ValueError(f"invalid hook name: {name}")
    return normalized


def project_name_for_hook(name: str) -> str:
    """Return the hook library project directory for a public hook name."""
    return normalize_hook_name(name).split(".", maxsplit=1)[0]


def project_name_from_metadata(metadata: HookProjectMetadata) -> str:
    """Return the single library project directory implied by hook metadata."""
    if not metadata.hooks:
        raise ValueError("hook project must declare at least one hook")
    project_names = {project_name_for_hook(public_name) for public_name in metadata.hooks}
    if len(project_names) != 1:
        raise ValueError("hook project hooks must share the same namespace")
    return next(iter(project_names))


def read_hook_metadata(project_root: Path) -> HookProjectMetadata:
    """Read hook metadata from a uv hook project's pyproject."""
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ValueError(f"hook project must contain pyproject.toml: {project_root}")
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid hook project pyproject: {pyproject}") from exc
    return HookProjectMetadata.from_pyproject(data)


def hook_module_file(project_root: Path, module: str) -> Path:
    """Return the required src-layout file path for a declared hook module."""
    return project_root / "src" / Path(*module.split(".")).with_suffix(".py")


def validate_hook_modules(project_root: Path, metadata: HookProjectMetadata) -> None:
    """Require every declared hook module to resolve to a file under ``src``."""
    for definition in metadata.hooks.values():
        module_file = hook_module_file(project_root, definition.module)
        if not module_file.is_file():
            raise ValueError(f"hook module file not found: {module_file}")


def _nested_mapping(data: Mapping[str, object], path: tuple[str, ...]) -> object | None:
    current: object = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current
