"""Resolve hook names to built-in hooks or uv hook projects."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from untaped_recipe.builtins.registry import BUILTIN_HOOKS
from untaped_recipe.domain.hook_project import HookProjectMetadata


@dataclass(frozen=True)
class BuiltinHookRef:
    """Reference to an engine-owned built-in hook module."""

    public_name: str
    module: ModuleType


@dataclass(frozen=True)
class UvHookRef:
    """Reference to an external uv-managed hook project."""

    project_root: Path
    public_name: str
    module: str


HookRef = BuiltinHookRef | UvHookRef


class HookResolver:
    """Resolve logical hook names without importing external hook code."""

    def __init__(
        self,
        *,
        global_hooks: Path,
        builtins: dict[str, ModuleType] | None = None,
    ) -> None:
        self._global_hooks = global_hooks
        self._builtins = builtins if builtins is not None else BUILTIN_HOOKS

    def resolve(self, name: str, recipe_dir: Path) -> HookRef:
        """Resolve a hook name to either a built-in or uv hook project reference."""
        if not _is_hook_name(name):
            raise ValueError(f"hook must be a safe hook name: {name}")
        local = self._resolve_project(recipe_dir, name)
        if local is not None:
            return local
        global_ref = self._resolve_project(self._global_project_root(name), name)
        if global_ref is not None:
            return global_ref
        builtin = self._builtins.get(name)
        if builtin is not None:
            return BuiltinHookRef(public_name=name, module=builtin)
        raise ValueError(f"hook not found: {name}")

    def _global_project_root(self, name: str) -> Path:
        project = name.split(".", maxsplit=1)[0]
        return self._global_hooks / project

    def _resolve_project(self, project_root: Path, public_name: str) -> UvHookRef | None:
        pyproject = project_root / "pyproject.toml"
        if not pyproject.is_file():
            return None
        metadata = _read_metadata(pyproject)
        definition = metadata.hooks.get(public_name)
        if definition is None:
            return None
        if not (project_root / "uv.lock").is_file():
            raise ValueError(f"hook project is missing uv.lock: {project_root}")
        return UvHookRef(
            project_root=project_root,
            public_name=public_name,
            module=definition.module,
        )


def _read_metadata(pyproject: Path) -> HookProjectMetadata:
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid hook project pyproject: {pyproject}") from exc
    return HookProjectMetadata.from_pyproject(data)


def _is_hook_name(name: str) -> bool:
    try:
        HookProjectMetadata(hooks={name: {"module": "valid.module"}})
    except ValueError:
        return False
    return True
