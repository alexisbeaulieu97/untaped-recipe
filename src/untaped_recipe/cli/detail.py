"""Structured detail records for ``recipe show``."""

from __future__ import annotations

from pathlib import Path

from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.hook_project import hook_module_file
from untaped_recipe.domain.pack import HookEntry, PackManifest
from untaped_recipe.domain.recipe import (
    CopyStep,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file


def recipe_detail(ref: str, recipe: Recipe, path: Path) -> dict[str, object]:
    """Return a structured recipe detail record."""
    return {
        "ref": ref,
        "description": recipe.description,
        "inputs": [
            {
                "name": name,
                "type": spec.type,
                "required": spec.required,
                "default": spec.default,
                "description": spec.description,
                "sensitive": spec.sensitive,
            }
            for name, spec in sorted(recipe.inputs.items())
        ],
        "steps": [_step_detail(step) for step in recipe.steps],
        "hooks": sorted(
            {step.hook for step in recipe.steps if isinstance(step, TransformStep | ValidateStep)}
        ),
        "path": str(path),
    }


def hook_detail(
    ref: str,
    entry: HookEntry,
    exports: frozenset[str],
    module_file: Path,
) -> dict[str, object]:
    """Return a structured hook detail record."""
    return {
        "ref": ref,
        "module": entry.module,
        "exports": sorted(exports),
        "path": str(module_file),
    }


def pack_detail(installed_name: str, manifest: PackManifest, root: Path) -> dict[str, object]:
    """Return a structured pack detail record."""
    record: dict[str, object] = {
        "name": installed_name,
        "project": manifest.project_name,
        "version": manifest.version,
        "recipes": [
            {
                "name": name,
                "description": _first_line(load_recipe_file(root / entry.path).description),
            }
            for name, entry in sorted(manifest.recipes.items())
        ],
        "hooks": [
            {
                "name": name,
                "exports": sorted(hook_exports(hook_module_file(root, entry.module))),
            }
            for name, entry in sorted(manifest.hooks.items())
        ],
        "path": str(root),
    }
    if manifest.name != installed_name:
        record["manifest_name"] = manifest.name
    return record


def _step_detail(
    step: CopyStep | RemoveStep | TemplateStep | TransformStep | ValidateStep,
) -> dict[str, object]:
    if isinstance(step, TransformStep):
        return {"type": step.type, "file_or_files": str(step.file), "hook": step.hook}
    if isinstance(step, ValidateStep):
        return {"type": step.type, "file_or_files": "", "hook": step.hook}
    if isinstance(step, TemplateStep):
        return {"type": step.type, "file_or_files": str(step.dest), "hook": ""}
    if isinstance(step, CopyStep):
        return {"type": step.type, "file_or_files": str(step.dest), "hook": ""}
    return {"type": step.type, "file_or_files": str(step.file), "hook": ""}


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""
