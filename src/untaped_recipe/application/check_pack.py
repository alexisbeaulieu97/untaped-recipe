"""Validate packs, recipes, and the installed library (check use case)."""

from __future__ import annotations

from pathlib import Path

from untaped.api import ConfigError

from untaped_recipe.application.harness import orphaned_test_dirs
from untaped_recipe.application.inputs import validate_recipe_input_sources
from untaped_recipe.application.resolution import (
    is_explicit_recipe_path,
    resolve_explicit_recipe,
)
from untaped_recipe.builtins.registry import BUILTIN_HOOKS
from untaped_recipe.domain.hook_project import (
    read_hook_metadata,
    validate_hook_modules,
    validate_hook_project_contract,
)
from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.recipe import (
    CopyStep,
    Recipe,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.infrastructure import HookResolver
from untaped_recipe.infrastructure.hook_resolver import ensure_hook_supports
from untaped_recipe.infrastructure.pack_store import InstalledPack, PackLibrary
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file
from untaped_recipe.infrastructure.uv_project import check_lock


class _LockFreshness:
    """Probe `uv lock --check` at most once per project root per command."""

    def __init__(self) -> None:
        self._results: dict[Path, str | None] = {}

    def check(self, project_root: Path) -> None:
        key = project_root.resolve()
        if key not in self._results:
            try:
                check_lock(project_root)
            except ValueError as exc:
                self._results[key] = str(exc)
            else:
                self._results[key] = None
        error = self._results[key]
        if error is not None:
            raise ValueError(error)


def check_ref(root: Path, ref_text: str) -> dict[str, object]:
    """Check one installed pack, recipe ref, or explicit path."""
    library = PackLibrary(library_root=root)
    locks = _LockFreshness()
    if is_explicit_recipe_path(ref_text):
        path = Path(ref_text).expanduser()
        if path.is_dir() and (path / "pyproject.toml").is_file():
            try:
                manifest = PackManifest.from_pyproject(path)
            except (ValueError, OSError) as exc:
                return _pack_check_row(path.name, path, status="error", error=str(exc))
            return _check_pack(root, InstalledPack.local(path, manifest), locks)
        resolved = resolve_explicit_recipe(path, recipe_id=None)
        return _check_recipe(root, resolved.path, resolved.ref, resolved.local_hook_project, locks)
    pack = library.find_pack(ref_text)
    if pack is not None:
        return _check_pack(root, pack, locks)
    ref = parse_ref(ref_text)
    try:
        pack, recipe = library.find_recipe(ref)
    except ValueError as exc:
        if str(exc).startswith("recipe not found") and "/" not in ref_text:
            builtin = BUILTIN_HOOKS.get(ref_text)
            if builtin is not None:
                return _builtin_check_row(ref_text, Path(builtin.module.__file__ or ""))
        raise
    return _check_recipe(root, pack.root / recipe.path, f"{pack.name}/{ref.name}", pack.root, locks)


def check_library(root: Path) -> list[dict[str, object]]:
    """Check every installed pack plus index/directory reconciliation."""
    library = PackLibrary(library_root=root)
    locks = _LockFreshness()
    rows = [_check_reconcile_problem(root, problem) for problem in library.reconcile()]
    rows.extend(_check_pack(root, pack, locks) for pack in library.packs())
    return rows


def _check_reconcile_problem(root: Path, problem: str) -> dict[str, object]:
    name = _quoted_name(problem)
    return _pack_check_row(
        name,
        root / "packs" / name if name else None,
        status="error",
        error=problem,
    )


def _quoted_name(message: str) -> str:
    parts = message.split("'", maxsplit=2)
    return parts[1] if len(parts) == 3 else ""


def _builtin_check_row(name: str, path: Path) -> dict[str, object]:
    return {
        "recipe": name,
        "status": "pass",
        "path": str(path),
        "error": "",
    }


def _pack_check_row(
    name: str,
    path: Path | None,
    *,
    status: str,
    recipes: int = 0,
    hooks: int = 0,
    error: str = "",
) -> dict[str, object]:
    return {
        "pack": name,
        "status": status,
        "path": str(path) if path is not None else "",
        "recipes": recipes,
        "hooks": hooks,
        "error": error,
    }


def _check_pack(root: Path, pack: InstalledPack, locks: _LockFreshness) -> dict[str, object]:
    try:
        if not (pack.root / "uv.lock").is_file():
            raise ValueError(f"pack project is missing uv.lock: {pack.root}")
        if pack.manifest.hooks:
            locks.check(pack.root)
        validate_hook_project_contract(pack.root, pack.manifest)
        validate_hook_modules(pack.root, pack.manifest)
        for recipe_name, recipe in sorted(pack.manifest.recipes.items()):
            row = _check_recipe(
                root, pack.root / recipe.path, f"{pack.name}/{recipe_name}", pack.root, locks
            )
            if row["status"] == "error":
                raise ValueError(f"{recipe_name}: {row['error']}")
        orphans = orphaned_test_dirs(pack)
        if orphans:
            raise ValueError("tests directory names no known recipe: " + ", ".join(orphans))
    except (ConfigError, ValueError, OSError) as exc:
        return _pack_check_row(
            pack.name,
            pack.root,
            status="error",
            recipes=len(pack.manifest.recipes),
            hooks=len(pack.manifest.hooks),
            error=str(exc),
        )
    return _pack_check_row(
        pack.name,
        pack.root,
        status="pass",
        recipes=len(pack.manifest.recipes),
        hooks=len(pack.manifest.hooks),
    )


def _check_recipe(
    root: Path,
    recipe_path: Path,
    recipe_ref: str,
    local_hook_project: Path | None,
    locks: _LockFreshness,
) -> dict[str, object]:
    try:
        recipe = load_recipe_file(recipe_path)
        validate_recipe_input_sources(recipe)
        _check_project_lock(local_hook_project)
        _check_assets(recipe, recipe_path.parent)
        _check_local_hook_project(local_hook_project, locks)
        _check_hooks(recipe, root, local_hook_project)
    except (ConfigError, ValueError, OSError) as exc:
        return {
            "recipe": recipe_ref,
            "status": "error",
            "path": str(recipe_path),
            "error": str(exc),
        }
    return {
        "recipe": recipe_ref,
        "status": "pass",
        "path": str(recipe_path),
        "error": "",
    }


def _check_project_lock(local_hook_project: Path | None) -> None:
    if local_hook_project is not None and not (local_hook_project / "uv.lock").is_file():
        raise ValueError(f"recipe project is missing uv.lock: {local_hook_project}")


def _check_assets(recipe: Recipe, recipe_dir: Path) -> None:
    for step in recipe.steps:
        if isinstance(step, TemplateStep):
            source = confined_path(recipe_dir, step.template, field="template")
            if not source.is_file():
                raise ValueError(f"template not found: {step.template}")
        elif isinstance(step, CopyStep):
            source = confined_path(recipe_dir, step.source, field="source")
            if not source.is_file():
                raise ValueError(f"copy source not found: {step.source}")


def _check_local_hook_project(local_hook_project: Path | None, locks: _LockFreshness) -> None:
    if local_hook_project is None or not (local_hook_project / "pyproject.toml").is_file():
        return
    metadata = read_hook_metadata(local_hook_project)
    if not metadata.hooks:
        return
    validate_hook_project_contract(local_hook_project, metadata)
    if not (local_hook_project / "uv.lock").is_file():
        raise ValueError(f"hook project is missing uv.lock: {local_hook_project}")
    locks.check(local_hook_project)
    validate_hook_modules(local_hook_project, metadata)


def _check_hooks(recipe: Recipe, root: Path, local_hook_project: Path | None) -> None:
    resolver = HookResolver(library_root=root)
    for step in recipe.steps:
        if isinstance(step, TransformStep):
            ref = resolver.resolve(step.hook, local_hook_project)
            ensure_hook_supports(ref, step.hook, verb="transform")
        elif isinstance(step, ValidateStep):
            ref = resolver.resolve(step.hook, local_hook_project)
            ensure_hook_supports(ref, step.hook, verb="validate")
