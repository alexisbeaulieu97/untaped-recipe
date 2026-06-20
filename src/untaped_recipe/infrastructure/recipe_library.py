"""Local standalone recipe and pack recipe resolution."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from untaped_recipe.domain.paths import is_explicit_path, safe_library_name
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.domain.recipe_project import (
    RecipeProjectMetadata,
    read_recipe_project_metadata,
)
from untaped_recipe.infrastructure.uv_project import lock_project


@dataclass(frozen=True)
class RecipeEntry:
    """One standalone recipe library entry."""

    name: str
    path: Path
    kind: str


@dataclass(frozen=True)
class RecipeResolution:
    """Resolved recipe path plus layout information."""

    path: Path
    kind: str
    ref: str
    project_root: Path | None = None

    @property
    def local_hook_project(self) -> Path | None:
        """Return the uv project root whose local hooks are in scope."""
        return self.project_root


class RecipeLibrary:
    """Manage standalone recipe projects and resolve runnable recipes."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def recipes_dir(self) -> Path:
        """Directory containing standalone recipe projects."""
        return self._root / "recipes"

    @property
    def packs_dir(self) -> Path:
        """Directory containing recipe pack projects."""
        return self._root / "packs"

    def init(
        self,
        name: str,
        *,
        base_dir: Path | None = None,
        library: bool = False,
    ) -> Path:
        """Scaffold a standalone uv recipe project."""
        recipe_id = safe_library_name(name, field="recipe")
        parent = self.recipes_dir if library else (base_dir or Path.cwd())
        project_root = parent / recipe_id
        if project_root.exists():
            raise ValueError(f"recipe already exists: {recipe_id}")
        parent.mkdir(parents=True, exist_ok=True)
        temp_root = parent / f".{recipe_id}.tmp"
        if temp_root.exists():
            raise ValueError(f"temporary recipe scaffold already exists: {temp_root}")
        try:
            _scaffold_recipe_project(project_root=temp_root, recipe_id=recipe_id)
            lock_project(temp_root)
            temp_root.rename(project_root)
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise
        return project_root

    def resolve(self, recipe: str, *, recipe_id: str | None = None) -> Path:
        """Resolve a recipe id, pack reference, or path."""
        return self.resolve_detail(recipe, recipe_id=recipe_id).path

    def resolve_detail(self, recipe: str, *, recipe_id: str | None = None) -> RecipeResolution:
        """Resolve a recipe id, pack reference, or path with metadata."""
        if recipe_id is not None:
            return self._resolve_path_recipe(recipe, recipe_id=recipe_id)
        if ":" in recipe and not is_explicit_path(recipe):
            pack_id, pack_recipe_id = _split_pack_ref(recipe)
            return self._resolve_pack_recipe(pack_id, pack_recipe_id)
        if not is_explicit_path(recipe):
            recipe_name = safe_library_name(recipe, field="recipe")
            return self._resolve_standalone_library_recipe(recipe_name)
        return self._resolve_path_recipe(recipe, recipe_id=None)

    def _resolve_path_recipe(self, recipe: str, *, recipe_id: str | None) -> RecipeResolution:
        path = Path(recipe).expanduser()
        if path.is_file():
            if recipe_id is not None:
                raise ValueError("--recipe is only valid for pack projects")
            _validate_recipe_file(path)
            file_recipe_id = safe_library_name(path.stem, field="recipe")
            return RecipeResolution(
                path=path,
                kind="file",
                ref=file_recipe_id,
            )
        if path.is_dir() and (path / "pyproject.toml").is_file():
            metadata = read_recipe_project_metadata(path)
            if metadata.pack is not None:
                if recipe_id is None:
                    raise ValueError("pack recipe path requires --recipe")
                return self._resolution_from_pack_project(path, metadata, recipe_id)
            if recipe_id is not None:
                raise ValueError("--recipe is only valid for pack projects")
            standalone_id, recipe_path = _single_recipe(metadata)
            full_path = _project_recipe_path(path, recipe_path)
            _validate_recipe_file(full_path)
            return RecipeResolution(
                path=full_path,
                kind="recipe",
                ref=standalone_id,
                project_root=path,
            )
        raise ValueError(f"recipe not found: {recipe}")

    def _resolve_standalone_library_recipe(self, recipe_id: str) -> RecipeResolution:
        project_root = self.recipes_dir / recipe_id
        if not project_root.is_dir():
            raise ValueError(f"recipe not found: {recipe_id}")
        metadata = read_recipe_project_metadata(project_root)
        if metadata.pack is not None:
            raise ValueError(f"recipe not found: {recipe_id}")
        declared_id, recipe_path = _single_recipe(metadata)
        if declared_id != recipe_id:
            raise ValueError(
                f"recipe library directory {recipe_id!r} does not match metadata {declared_id!r}"
            )
        full_path = _project_recipe_path(project_root, recipe_path)
        _validate_recipe_file(full_path)
        return RecipeResolution(
            path=full_path,
            kind="recipe",
            ref=recipe_id,
            project_root=project_root,
        )

    def _resolve_pack_recipe(self, pack_id: str, recipe_id: str) -> RecipeResolution:
        project_root = self.packs_dir / pack_id
        if not project_root.is_dir():
            raise ValueError(f"pack not found: {pack_id}")
        metadata = read_recipe_project_metadata(project_root)
        if metadata.pack != pack_id:
            raise ValueError(f"pack metadata mismatch: {pack_id}")
        return self._resolution_from_pack_project(project_root, metadata, recipe_id)

    def _resolution_from_pack_project(
        self,
        project_root: Path,
        metadata: RecipeProjectMetadata,
        recipe_id: str,
    ) -> RecipeResolution:
        recipe_id = safe_library_name(recipe_id, field="recipe")
        if metadata.pack is None:
            raise ValueError("recipe project is not a pack")
        recipe_path = metadata.recipe_paths().get(recipe_id)
        if recipe_path is None:
            raise ValueError(f"pack recipe not found: {metadata.pack}:{recipe_id}")
        full_path = _project_recipe_path(project_root, recipe_path)
        _validate_recipe_file(full_path)
        return RecipeResolution(
            path=full_path,
            kind="pack",
            ref=f"{metadata.pack}:{recipe_id}",
            project_root=project_root,
        )

    def add(self, source: Path) -> Path:
        """Copy a standalone uv recipe project into the library."""
        source = source.expanduser()
        self.recipes_dir.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            raise ValueError(f"recipe source not found: {source}")
        if not source.is_dir():
            raise ValueError("recipe source must be a uv recipe project directory")
        metadata = read_recipe_project_metadata(source)
        if metadata.pack is not None:
            raise ValueError("recipe add requires a standalone recipe project, not a pack")
        recipe_id, recipe_path = _single_recipe(metadata)
        if not (source / "uv.lock").is_file():
            raise ValueError(f"recipe project is missing uv.lock: {source}")
        _validate_recipe_file(_project_recipe_path(source, recipe_path))
        dest = self.recipes_dir / recipe_id
        if dest.exists():
            raise ValueError(f"recipe already exists: {recipe_id}")
        shutil.copytree(source, dest)
        return dest

    def remove(self, name: str) -> Path:
        """Remove a standalone recipe project from the library."""
        recipe_name = safe_library_name(name, field="recipe")
        package_dir = self.recipes_dir / recipe_name
        if package_dir.is_dir():
            shutil.rmtree(package_dir)
            return package_dir
        raise ValueError(f"recipe not found: {name}")

    def list(self) -> list[RecipeEntry]:
        """List standalone recipe projects in the library."""
        if not self.recipes_dir.is_dir():
            return []
        entries: list[RecipeEntry] = []
        for child in sorted(self.recipes_dir.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or not (child / "pyproject.toml").is_file():
                continue
            metadata = read_recipe_project_metadata(child)
            if metadata.pack is not None:
                continue
            recipe_id, recipe_path = _single_recipe(metadata)
            entries.append(RecipeEntry(name=recipe_id, path=child / recipe_path, kind="recipe"))
        return entries


def _validate_recipe_file(path: Path) -> None:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid recipe YAML: {exc}") from exc
    try:
        Recipe.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid recipe: {exc}") from exc


def _single_recipe(metadata: RecipeProjectMetadata) -> tuple[str, Path]:
    recipes = metadata.recipe_paths()
    if len(recipes) != 1:
        raise ValueError("standalone recipe project must expose exactly one recipe")
    return next(iter(recipes.items()))


def _project_recipe_path(project_root: Path, relative_path: Path) -> Path:
    path = project_root / relative_path
    if not path.is_file():
        raise ValueError(f"recipe file not found: {relative_path}")
    return path


def _split_pack_ref(recipe: str) -> tuple[str, str]:
    if recipe.count(":") != 1:
        raise ValueError("pack recipe refs must use <pack>:<recipe>")
    pack_id, recipe_id = recipe.split(":", maxsplit=1)
    if not pack_id or not recipe_id:
        raise ValueError("pack recipe refs must use <pack>:<recipe>")
    return (
        safe_library_name(pack_id, field="pack"),
        safe_library_name(recipe_id, field="recipe"),
    )


def _scaffold_recipe_project(*, project_root: Path, recipe_id: str) -> None:
    (project_root / "templates").mkdir(parents=True)
    (project_root / "files").mkdir()
    (project_root / "recipe.yml").write_text(
        "version: 1\n"
        "description: ''\n"
        "inputs: {}\n"
        "steps: []\n"
        "\n"
        "# Add template, copy, transform, validate, or remove steps here.\n"
    )
    (project_root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{recipe_id}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        f'"{recipe_id}" = {{ path = "recipe.yml" }}\n'
    )
