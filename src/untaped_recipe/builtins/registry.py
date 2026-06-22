"""Direct registry for engine-owned built-in hooks."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from untaped_recipe.builtins.hooks import yaml_edit
from untaped_recipe.domain.hook_project import HookKind


@dataclass(frozen=True)
class BuiltinHook:
    """Engine-owned hook definition."""

    kind: HookKind
    module: ModuleType


BUILTIN_HOOKS: dict[str, BuiltinHook] = {
    "yaml_edit": BuiltinHook(kind="transform", module=yaml_edit),
}

__all__ = ["BUILTIN_HOOKS", "BuiltinHook"]
