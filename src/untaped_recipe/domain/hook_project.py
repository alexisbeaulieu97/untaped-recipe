"""Models for uv-managed hook project metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, field_validator

_HOOK_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


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
        if not _MODULE_RE.fullmatch(value):
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
            if not _HOOK_NAME_RE.fullmatch(name):
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


def _nested_mapping(data: Mapping[str, object], path: tuple[str, ...]) -> object | None:
    current: object = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current
