"""Direct registry for engine-owned built-in hooks."""

from __future__ import annotations

from types import ModuleType

from untaped_recipe.builtins.hooks import yaml_edit

BUILTIN_HOOKS: dict[str, ModuleType] = {
    "yaml_edit": yaml_edit,
}

__all__ = ["BUILTIN_HOOKS"]
