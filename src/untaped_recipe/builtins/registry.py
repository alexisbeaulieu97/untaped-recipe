"""Direct registry for engine-owned built-in hooks."""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from untaped_recipe.builtins.hooks import yaml_edit

_HOOK_EXPORT_NAMES = ("transform", "validate")


@dataclass(frozen=True)
class BuiltinHook:
    """Engine-owned hook definition."""

    module: ModuleType
    exports: frozenset[str]


def _module_exports(module: ModuleType) -> frozenset[str]:
    return frozenset(name for name in _HOOK_EXPORT_NAMES if hasattr(module, name))


BUILTIN_HOOKS: dict[str, BuiltinHook] = {
    "yaml_edit": BuiltinHook(module=yaml_edit, exports=_module_exports(yaml_edit)),
}

__all__ = ["BUILTIN_HOOKS", "BuiltinHook"]
