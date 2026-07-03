"""Resolve hook names to built-in hooks or uv hook projects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from untaped_recipe.builtins.registry import BUILTIN_HOOKS, BuiltinHook
from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.hook_project import (
    HookProjectMetadata,
    hook_module_file,
    is_valid_dotted_name,
    project_name_for_hook,
    read_hook_metadata,
    validate_hook_modules,
    validate_hook_project_contract,
)


@dataclass(frozen=True)
class BuiltinHookRef:
    """Reference to an engine-owned built-in hook module."""

    name: str
    exports: frozenset[str]
    module: ModuleType


@dataclass(frozen=True)
class UvHookRef:
    """Reference to an external uv-managed hook project."""

    name: str
    exports: frozenset[str]
    project_root: Path
    module: str


HookRef = BuiltinHookRef | UvHookRef


def ensure_hook_supports(ref: HookRef, hook: str, *, verb: str) -> None:
    """Reject a hook reference that does not export the verb the caller needs."""
    if verb not in ref.exports:
        raise ValueError(f"{verb} step hook {hook!r} does not export a {verb}() function")


class HookResolver:
    """Resolve logical hook names without importing external hook code."""

    def __init__(
        self,
        *,
        global_hooks: Path,
        builtins: dict[str, BuiltinHook] | None = None,
    ) -> None:
        self._global_hooks = global_hooks
        self._builtins = builtins if builtins is not None else BUILTIN_HOOKS
        self._metadata_cache: dict[Path, HookProjectMetadata] = {}
        self._validated_contracts: set[Path] = set()

    def resolve(self, name: str, local_hook_project: Path | None) -> HookRef:
        """Resolve a hook name to either a built-in or uv hook project reference."""
        if not is_valid_dotted_name(name):
            raise ValueError(f"hook must be a safe hook name: {name}")
        if local_hook_project is not None:
            local = self._resolve_project(local_hook_project, name)
            if local is not None:
                return local
        global_ref = self._resolve_project(self._global_project_root(name), name)
        if global_ref is not None:
            return global_ref
        builtin = self._builtins.get(name)
        if builtin is not None:
            return BuiltinHookRef(name=name, exports=builtin.exports, module=builtin.module)
        raise ValueError(f"hook not found: {name}")

    def _global_project_root(self, name: str) -> Path:
        return self._global_hooks / project_name_for_hook(name)

    def _resolve_project(self, project_root: Path, public_name: str) -> UvHookRef | None:
        pyproject = project_root / "pyproject.toml"
        if not pyproject.is_file():
            return None
        metadata = self._metadata_for(project_root)
        definition = metadata.hooks.get(public_name)
        if definition is None:
            return None
        self._validate_contract_once(project_root, metadata)
        if not (project_root / "uv.lock").is_file():
            raise ValueError(f"hook project is missing uv.lock: {project_root}")
        validate_hook_modules(project_root, metadata)
        module_file = hook_module_file(project_root, definition.module)
        exports = hook_exports(module_file)
        if not exports:
            raise ValueError(
                f"hook module for {public_name!r} exports neither transform() nor validate()"
            )
        return UvHookRef(
            name=public_name,
            exports=exports,
            project_root=project_root,
            module=definition.module,
        )

    def _metadata_for(self, project_root: Path) -> HookProjectMetadata:
        resolved = project_root.resolve()
        metadata = self._metadata_cache.get(resolved)
        if metadata is None:
            metadata = read_hook_metadata(project_root)
            self._metadata_cache[resolved] = metadata
        return metadata

    def _validate_contract_once(self, project_root: Path, metadata: HookProjectMetadata) -> None:
        resolved = project_root.resolve()
        if resolved in self._validated_contracts:
            return
        validate_hook_project_contract(project_root, metadata)
        self._validated_contracts.add(resolved)
