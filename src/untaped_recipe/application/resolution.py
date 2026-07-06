"""Resolve recipe references to concrete recipe files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.infrastructure.pack_store import PackLibrary


@dataclass(frozen=True)
class ResolvedRecipe:
    """A recipe file resolved from an explicit path or installed pack."""

    path: Path
    ref: str
    local_hook_project: Path | None


def is_explicit_recipe_path(value: str) -> bool:
    """Classify a ref as an explicit filesystem path (never a library ref)."""
    return value.startswith(("/", "./", "../", "~")) or value.endswith((".yml", ".yaml"))


def resolve_apply_recipe(root: Path, ref_text: str, *, recipe_id: str | None) -> ResolvedRecipe:
    """Resolve an apply ref: explicit path, pack path + --recipe, or library ref."""
    if recipe_id is not None:
        if not is_explicit_recipe_path(ref_text):
            raise ValueError("--recipe requires an explicit pack path")
        return resolve_explicit_recipe(Path(ref_text).expanduser(), recipe_id=recipe_id)
    if is_explicit_recipe_path(ref_text):
        return resolve_explicit_recipe(Path(ref_text).expanduser(), recipe_id=None)
    ref = parse_ref(ref_text)
    pack, recipe = PackLibrary(library_root=root).find_recipe(ref)
    return ResolvedRecipe(
        path=pack.root / recipe.path,
        ref=f"{pack.name}/{ref.name}",
        local_hook_project=pack.root,
    )


def resolve_explicit_recipe(path: Path, *, recipe_id: str | None) -> ResolvedRecipe:
    """Resolve an explicit path to a recipe file, pack recipe, or bare recipe.yml."""
    if path.is_dir():
        if recipe_id is not None:
            manifest = PackManifest.from_pyproject(path)
            entry = manifest.recipes.get(recipe_id)
            if entry is None:
                raise ValueError(f"recipe not found: {recipe_id}")
            return ResolvedRecipe(
                path=path / entry.path,
                ref=f"{path.name}/{recipe_id}",
                local_hook_project=path,
            )
        recipe_path = path / "recipe.yml"
        if not recipe_path.is_file():
            raise ValueError(f"recipe file not found: {recipe_path}")
        return ResolvedRecipe(
            path=recipe_path,
            ref=path.name,
            local_hook_project=path if (path / "pyproject.toml").is_file() else None,
        )
    return ResolvedRecipe(
        path=path,
        ref=path.name,
        local_hook_project=None,
    )
