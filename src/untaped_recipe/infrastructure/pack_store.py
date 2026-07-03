"""Unified installed pack storage and resolution."""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.hook_project import (
    hook_module_file,
    validate_hook_project_contract,
)
from untaped_recipe.domain.pack import HookEntry, PackManifest, PackRef, RecipeEntry
from untaped_recipe.domain.paths import safe_library_name
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file


@dataclass(frozen=True)
class InstalledPack:
    """One installed pack plus library bookkeeping."""

    name: str
    root: Path
    manifest: PackManifest
    source: str
    rev: str
    installed_version: str


@dataclass(frozen=True)
class _IndexEntry:
    source: str = ""
    rev: str = ""
    version: str = ""


class PackLibrary:
    """Manage installed recipe packs under one library root."""

    def __init__(self, *, library_root: Path) -> None:
        self._library_root = library_root

    @property
    def packs_dir(self) -> Path:
        """Directory containing installed pack copies."""
        return self._library_root / "packs"

    @property
    def index_path(self) -> Path:
        """Path to the library pack source index."""
        return self._library_root / "packs.toml"

    def add(
        self,
        source_dir: Path,
        *,
        source: str,
        rev: str | None,
        name: str | None,
        force: bool,
    ) -> PackManifest:
        """Install a validated pack directory into the library."""
        source_dir = source_dir.expanduser()
        manifest = PackManifest.from_pyproject(source_dir)
        _validate_pack(source_dir, manifest)
        installed_name = safe_library_name(name or manifest.name, field="pack")
        dest = self.packs_dir / installed_name
        if dest.exists() and not force:
            raise ValueError(
                f"pack already installed: {installed_name}; use --force to replace "
                "or --name to install under another name"
            )

        self.packs_dir.mkdir(parents=True, exist_ok=True)
        if force and dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest, ignore=shutil.ignore_patterns(".git"))
        index = self._read_index()
        index[installed_name] = _IndexEntry(
            source=source,
            rev=rev or "",
            version=manifest.version,
        )
        self._write_index(index)
        return manifest

    def remove(self, name: str) -> None:
        """Remove an installed pack and its index row."""
        installed_name = safe_library_name(name, field="pack")
        dest = self.packs_dir / installed_name
        if not dest.is_dir():
            raise ValueError(f"pack not found: {name}")
        shutil.rmtree(dest)
        index = self._read_index()
        index.pop(installed_name, None)
        self._write_index(index)

    def packs(self) -> list[InstalledPack]:
        """Return installed packs keyed by their library identity."""
        if not self.packs_dir.is_dir():
            return []
        index = self._read_index()
        installed: list[InstalledPack] = []
        for root in sorted(self.packs_dir.iterdir(), key=lambda path: path.name):
            if not root.is_dir() or not (root / "pyproject.toml").is_file():
                continue
            manifest = PackManifest.from_pyproject(root)
            index_entry = index.get(root.name, _IndexEntry(version=manifest.version))
            installed.append(
                InstalledPack(
                    name=root.name,
                    root=root,
                    manifest=manifest,
                    source=index_entry.source,
                    rev=index_entry.rev,
                    installed_version=index_entry.version or manifest.version,
                )
            )
        return installed

    def find_recipe(self, ref: PackRef) -> tuple[InstalledPack, RecipeEntry]:
        """Resolve a bare or qualified recipe reference."""
        matches = [
            (pack, recipe)
            for pack in self._candidate_packs(ref.pack)
            if (recipe := pack.manifest.recipes.get(ref.name)) is not None
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            candidates = ", ".join(f"{pack.name}/{ref.name}" for pack, _ in matches)
            raise ValueError(f"ambiguous recipe ref {ref.name!r}; candidates: {candidates}")
        raise ValueError(f"recipe not found: {_ref_text(ref)}")

    def find_hook(self, ref: PackRef) -> tuple[InstalledPack, HookEntry]:
        """Resolve a bare or qualified hook reference."""
        matches = [
            (pack, hook)
            for pack in self._candidate_packs(ref.pack)
            if (hook := pack.manifest.hooks.get(ref.name)) is not None
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            candidates = ", ".join(f"{pack.name}/{ref.name}" for pack, _ in matches)
            raise ValueError(f"ambiguous hook ref {ref.name!r}; candidates: {candidates}")
        raise ValueError(f"hook not found: {_ref_text(ref)}")

    def _candidate_packs(self, pack: str | None) -> list[InstalledPack]:
        installed = self.packs()
        if pack is None:
            return installed
        installed_name = safe_library_name(pack, field="pack")
        return [candidate for candidate in installed if candidate.name == installed_name]

    def _read_index(self) -> dict[str, _IndexEntry]:
        if not self.index_path.is_file():
            return {}
        data = tomllib.loads(self.index_path.read_text(encoding="utf-8"))
        index: dict[str, _IndexEntry] = {}
        for name, raw_entry in data.items():
            if not isinstance(raw_entry, dict):
                continue
            index[name] = _IndexEntry(
                source=str(raw_entry.get("source", "")),
                rev=str(raw_entry.get("rev", "")),
                version=str(raw_entry.get("version", "")),
            )
        return index

    def _write_index(self, index: dict[str, _IndexEntry]) -> None:
        self._library_root.mkdir(parents=True, exist_ok=True)
        doc = tomlkit.document()
        for name, entry in sorted(index.items()):
            table = tomlkit.table()
            table.add("source", entry.source)
            table.add("rev", entry.rev)
            table.add("version", entry.version)
            doc.add(name, table)
        self.index_path.write_text(tomlkit.dumps(doc), encoding="utf-8")


def _validate_pack(source_dir: Path, manifest: PackManifest) -> None:
    if not (source_dir / "uv.lock").is_file():
        raise ValueError(f"pack project is missing uv.lock: {source_dir}")
    validate_hook_project_contract(source_dir, manifest)
    for recipe_name, recipe_entry in manifest.recipes.items():
        recipe_file = source_dir / recipe_entry.path
        if not recipe_file.is_file():
            raise ValueError(f"pack recipe file not found: {recipe_name}")
        try:
            load_recipe_file(recipe_file)
        except ValueError as exc:
            raise ValueError(f"invalid pack recipe: {recipe_name}: {exc}") from exc
    for hook_name, hook_entry in manifest.hooks.items():
        module_file = hook_module_file(source_dir, hook_entry.module)
        if not module_file.is_file():
            raise ValueError(f"hook module file not found: {module_file}")
        if not hook_exports(module_file):
            raise ValueError(
                f"hook module for {hook_name!r} exports neither transform() nor validate()"
            )


def _ref_text(ref: PackRef) -> str:
    return f"{ref.pack}/{ref.name}" if ref.pack is not None else ref.name
