"""Local recipe pack library storage."""

from __future__ import annotations

import builtins
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from untaped_recipe.domain.paths import is_explicit_path, safe_library_name
from untaped_recipe.domain.recipe_project import (
    append_recipe_metadata,
    read_recipe_project_metadata,
    remove_recipe_metadata,
)
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file
from untaped_recipe.infrastructure.uv_project import lock_project


@dataclass(frozen=True)
class PackEntry:
    """One installed recipe pack."""

    name: str
    path: Path
    recipes_count: int


@dataclass(frozen=True)
class PackRecipeEntry:
    """One recipe exposed by a pack."""

    name: str
    path: Path


class PackLibrary:
    """Manage uv recipe pack projects."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def packs_dir(self) -> Path:
        """Directory containing installed recipe packs."""
        return self._root / "packs"

    def init(
        self,
        name: str,
        *,
        base_dir: Path | None = None,
        library: bool = False,
    ) -> Path:
        """Scaffold an empty uv recipe pack project."""
        pack_id = safe_library_name(name, field="pack")
        parent = self.packs_dir if library else (base_dir or Path.cwd())
        project_root = parent / pack_id
        if project_root.exists():
            raise ValueError(f"pack already exists: {pack_id}")
        parent.mkdir(parents=True, exist_ok=True)
        temp_root = parent / f".{pack_id}.tmp"
        if temp_root.exists():
            raise ValueError(f"temporary pack scaffold already exists: {temp_root}")
        try:
            _scaffold_pack_project(project_root=temp_root, pack_id=pack_id)
            lock_project(temp_root)
            temp_root.rename(project_root)
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise
        return project_root

    def add(self, source: Path) -> Path:
        """Copy a uv recipe pack project into the pack library."""
        source = source.expanduser()
        self.packs_dir.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            raise ValueError(f"pack source not found: {source}")
        if not source.is_dir():
            raise ValueError("pack source must be a uv recipe pack project directory")
        metadata = read_recipe_project_metadata(source)
        if metadata.pack is None:
            raise ValueError("pack add requires a recipe pack project")
        if not (source / "uv.lock").is_file():
            raise ValueError(f"pack project is missing uv.lock: {source}")
        _validate_pack_recipes(source)
        dest = self.packs_dir / metadata.pack
        if dest.exists():
            raise ValueError(f"pack already exists: {metadata.pack}")
        shutil.copytree(source, dest)
        return dest

    def resolve(self, pack: str | Path) -> Path:
        """Resolve an installed pack id or explicit pack project path."""
        pack_ref = str(pack)
        explicit = Path(pack_ref).expanduser()
        if (
            is_explicit_path(pack_ref)
            and explicit.is_dir()
            and (explicit / "pyproject.toml").is_file()
        ):
            metadata = read_recipe_project_metadata(explicit)
            if metadata.pack is None:
                raise ValueError(f"not a recipe pack project: {pack_ref}")
            return explicit
        pack_id = safe_library_name(pack_ref, field="pack")
        path = self.packs_dir / pack_id
        if path.is_dir() and (path / "pyproject.toml").is_file():
            metadata = read_recipe_project_metadata(path)
            if metadata.pack != pack_id:
                raise ValueError(f"pack metadata mismatch: {pack_id}")
            return path
        raise ValueError(f"pack not found: {pack_ref}")

    def list(self) -> list[PackEntry]:
        """List installed recipe packs."""
        if not self.packs_dir.is_dir():
            return []
        entries: list[PackEntry] = []
        for child in sorted(self.packs_dir.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or not (child / "pyproject.toml").is_file():
                continue
            metadata = read_recipe_project_metadata(child)
            if metadata.pack is None:
                continue
            entries.append(
                PackEntry(
                    name=metadata.pack,
                    path=child,
                    recipes_count=len(metadata.recipes),
                )
            )
        return entries

    def remove(self, name: str) -> Path:
        """Remove an installed recipe pack project."""
        pack_id = safe_library_name(name, field="pack")
        path = self.packs_dir / pack_id
        if not path.is_dir():
            raise ValueError(f"pack not found: {name}")
        shutil.rmtree(path)
        return path

    def init_recipe(self, pack: str | Path, recipe_id: str) -> Path:
        """Add a new no-op recipe to a pack project."""
        project_root = self.resolve(str(pack))
        recipe_id = safe_library_name(recipe_id, field="recipe")
        metadata = read_recipe_project_metadata(project_root)
        if recipe_id in metadata.recipes:
            raise ValueError(f"pack recipe already exists: {recipe_id}")
        relative_path = Path("recipes") / recipe_id / "recipe.yml"
        recipe_path = project_root / relative_path
        if recipe_path.exists():
            raise ValueError(f"pack recipe already exists: {recipe_id}")
        if recipe_path.parent.exists():
            raise ValueError(f"pack recipe directory already exists: {recipe_path.parent}")
        pyproject = project_root / "pyproject.toml"
        before_pyproject = pyproject.read_text()
        try:
            recipe_path.parent.mkdir(parents=True)
            (recipe_path.parent / "templates").mkdir()
            (recipe_path.parent / "files").mkdir()
            recipe_path.write_text(
                "version: 1\n"
                "description: ''\n"
                "inputs: {}\n"
                "steps: []\n"
                "\n"
                "# Add template, copy, transform, validate, or remove steps here.\n"
            )
            append_recipe_metadata(project_root, recipe_id, relative_path)
            lock_project(project_root)
        except Exception:
            pyproject.write_text(before_pyproject)
            shutil.rmtree(recipe_path.parent, ignore_errors=True)
            raise
        return recipe_path

    def list_recipes(self, pack: str | Path) -> builtins.list[PackRecipeEntry]:
        """List recipes exposed by one pack."""
        project_root = self.resolve(pack)
        metadata = read_recipe_project_metadata(project_root)
        return [
            PackRecipeEntry(name=recipe_id, path=project_root / path)
            for recipe_id, path in sorted(metadata.recipe_paths().items())
        ]

    def recipe_path(self, pack: str | Path, recipe_id: str) -> Path:
        """Resolve one recipe file inside a pack."""
        recipe_id = safe_library_name(recipe_id, field="recipe")
        project_root = self.resolve(pack)
        metadata = read_recipe_project_metadata(project_root)
        relative_path = metadata.recipe_paths().get(recipe_id)
        if relative_path is None:
            raise ValueError(f"pack recipe not found: {recipe_id}")
        path = project_root / relative_path
        if not path.is_file():
            raise ValueError(f"pack recipe file not found: {relative_path}")
        return path

    def remove_recipe(self, pack: str | Path, recipe_id: str) -> Path:
        """Remove one generated recipe from a pack project."""
        recipe_id = safe_library_name(recipe_id, field="recipe")
        project_root = self.resolve(pack)
        metadata = read_recipe_project_metadata(project_root)
        relative_path = metadata.recipe_paths().get(recipe_id)
        if relative_path is None:
            raise ValueError(f"pack recipe not found: {recipe_id}")
        generated_path = Path("recipes") / recipe_id / "recipe.yml"
        if relative_path != generated_path:
            raise ValueError("only generated pack recipe layouts can be removed")
        path = project_root / relative_path
        if not path.is_file():
            raise ValueError(f"pack recipe file not found: {relative_path}")
        recipe_dir = path.parent
        backup_dir = recipe_dir.with_name(f".{recipe_id}.remove-tmp-{uuid.uuid4().hex}")
        if backup_dir.exists():
            raise ValueError(f"temporary pack recipe removal path already exists: {backup_dir}")
        pyproject = project_root / "pyproject.toml"
        before_pyproject = pyproject.read_text()
        moved = False
        try:
            remove_recipe_metadata(project_root, recipe_id)
            recipe_dir.rename(backup_dir)
            moved = True
            lock_project(project_root)
        except Exception as exc:
            pyproject.write_text(before_pyproject)
            if moved and backup_dir.exists():
                if recipe_dir.exists():
                    raise ValueError(
                        "pack recipe removal rollback incomplete; "
                        f"recipe directory already exists: {recipe_dir}; "
                        f"original recipe preserved at: {backup_dir}"
                    ) from exc
                backup_dir.rename(recipe_dir)
            raise
        shutil.rmtree(backup_dir)
        return recipe_dir


def _validate_pack_recipes(project_root: Path) -> None:
    metadata = read_recipe_project_metadata(project_root)
    for recipe_id, relative_path in metadata.recipe_paths().items():
        path = project_root / relative_path
        if not path.is_file():
            raise ValueError(f"pack recipe file not found: {recipe_id}")
        try:
            load_recipe_file(path)
        except ValueError as exc:
            raise ValueError(f"invalid pack recipe: {recipe_id}: {exc}") from exc


def _scaffold_pack_project(*, project_root: Path, pack_id: str) -> None:
    (project_root / "recipes").mkdir(parents=True)
    (project_root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-pack-{pack_id}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        f'pack = "{pack_id}"\n'
    )
