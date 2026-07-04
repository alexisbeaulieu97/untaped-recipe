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
    read_hook_metadata,
    validate_hook_modules,
    validate_hook_project_contract,
)
from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.infrastructure.pack_store import PackLibrary

_ProjectContract = HookProjectMetadata | PackManifest


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
        library_root: Path | None = None,
        builtins: dict[str, BuiltinHook] | None = None,
    ) -> None:
        self._library = PackLibrary(library_root=library_root) if library_root is not None else None
        self._builtins = builtins if builtins is not None else BUILTIN_HOOKS
        self._metadata_cache: dict[Path, HookProjectMetadata] = {}
        self._validated_projects: set[Path] = set()
        self._exports_cache: dict[Path, frozenset[str]] = {}

    def resolve(self, name: str, local_hook_project: Path | None) -> HookRef:
        """Resolve a hook name to either a built-in or uv hook project reference."""
        if "/" in name:
            return self._resolve_qualified(name)
        if not is_valid_dotted_name(name):
            raise ValueError(f"hook must be a safe hook name: {name}")
        if local_hook_project is not None:
            local = self._resolve_project(local_hook_project, name)
            if local is not None:
                return local
        library_ref = self._resolve_library(name)
        if library_ref is not None:
            return library_ref
        builtin = self._builtins.get(name)
        if builtin is not None:
            return BuiltinHookRef(name=name, exports=builtin.exports, module=builtin.module)
        raise ValueError(f"hook not found: {name}")

    def _resolve_qualified(self, name: str) -> HookRef:
        if name.startswith(("/", "./", "../", "~")):
            raise ValueError(f"hook must be a safe hook name: {name}")
        try:
            ref = parse_ref(name)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if ref.pack is None:
            raise ValueError(f"hook must be a safe hook name: {name}")
        library_ref = self._resolve_library(name)
        if library_ref is None:
            raise ValueError(f"hook not found: {name}")
        return library_ref

    def _resolve_library(self, name: str) -> UvHookRef | None:
        if self._library is None:
            return None
        ref = parse_ref(name)
        try:
            pack, hook = self._library.find_hook(ref)
        except ValueError as exc:
            if ref.pack is not None:
                raise
            if not str(exc).startswith("hook not found:"):
                raise
            return None
        return self._uv_ref(
            pack.root,
            pack.manifest,
            ref_name=name,
            public_name=ref.name,
            module=hook.module,
        )

    def _resolve_project(self, project_root: Path, public_name: str) -> UvHookRef | None:
        pyproject = project_root / "pyproject.toml"
        if not pyproject.is_file():
            return None
        metadata = self._metadata_for(project_root)
        definition = metadata.hooks.get(public_name)
        if definition is None:
            return None
        return self._uv_ref(
            project_root,
            metadata,
            ref_name=public_name,
            public_name=public_name,
            module=definition.module,
        )

    def _uv_ref(
        self,
        project_root: Path,
        contract: _ProjectContract,
        *,
        ref_name: str,
        public_name: str,
        module: str,
    ) -> UvHookRef:
        self._validate_project_once(project_root, contract)
        module_file = hook_module_file(project_root, module)
        exports = self._exports_for(module_file)
        if not exports:
            raise ValueError(
                f"hook module for {public_name!r} exports neither transform() nor validate()"
            )
        return UvHookRef(
            name=ref_name,
            exports=exports,
            project_root=project_root,
            module=module,
        )

    def _metadata_for(self, project_root: Path) -> HookProjectMetadata:
        resolved = project_root.resolve()
        metadata = self._metadata_cache.get(resolved)
        if metadata is None:
            metadata = read_hook_metadata(project_root)
            self._metadata_cache[resolved] = metadata
        return metadata

    def _exports_for(self, module_file: Path) -> frozenset[str]:
        exports = self._exports_cache.get(module_file)
        if exports is None:
            exports = hook_exports(module_file)
            self._exports_cache[module_file] = exports
        return exports

    def _validate_project_once(self, project_root: Path, contract: _ProjectContract) -> None:
        resolved = project_root.resolve()
        if resolved in self._validated_projects:
            return
        validate_hook_project_contract(project_root, contract)
        if not (project_root / "uv.lock").is_file():
            raise ValueError(f"hook project is missing uv.lock: {project_root}")
        validate_hook_modules(project_root, contract)
        self._validated_projects.add(resolved)
