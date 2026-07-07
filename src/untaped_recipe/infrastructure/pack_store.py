"""Unified installed pack storage and resolution."""

from __future__ import annotations

import fnmatch
import hashlib
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.hook_project import (
    hook_module_file,
    validate_hook_modules,
    validate_hook_project_contract,
)
from untaped_recipe.domain.pack import HookEntry, PackManifest, PackRef, RecipeEntry
from untaped_recipe.domain.paths import safe_library_name
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

_GIT_URL_PREFIXES = ("https://", "git@", "ssh://")

# Dev/build junk excluded from library installs; pack_content_hash prunes the
# same names so the recorded install hash and the copied tree always agree.
PACK_COPY_IGNORE = (
    ".git",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".uv-cache",
    "*.egg-info",
)


@dataclass(frozen=True)
class InstalledPack:
    """One installed pack plus library bookkeeping."""

    name: str
    root: Path
    manifest: PackManifest
    source: str
    rev: str
    installed_version: str

    @classmethod
    def local(cls, path: Path, manifest: PackManifest) -> InstalledPack:
        """Wrap an explicit-path pack that is not tracked by the library index."""
        return cls(
            name=manifest.name,
            root=path,
            manifest=manifest,
            source=str(path),
            rev="",
            installed_version=manifest.version,
        )


@dataclass(frozen=True)
class _IndexEntry:
    source: str = ""
    rev: str = ""
    version: str = ""
    content_hash: str = ""


def _is_ignored(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in PACK_COPY_IGNORE)


def pack_content_hash(root: Path) -> str:
    """Digest a pack tree's install-relevant content (ignore set pruned)."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(_is_ignored(part) for part in relative.parts):
            continue
        if not path.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


class PackLibrary:
    """Manage installed recipe packs under one library root."""

    def __init__(self, *, library_root: Path) -> None:
        self._library_root = library_root
        self._packs_cache: list[InstalledPack] | None = None

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
        discard_edits: bool = False,
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
        if force and not discard_edits and self.local_edits(installed_name):
            raise ValueError(local_edits_message(installed_name))

        self.packs_dir.mkdir(parents=True, exist_ok=True)
        if force and dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest, ignore=shutil.ignore_patterns(*PACK_COPY_IGNORE))
        index = self._read_index()
        index[installed_name] = _IndexEntry(
            source=source,
            rev=rev or "",
            version=manifest.version,
            content_hash=pack_content_hash(dest),
        )
        self._write_index(index)
        self._packs_cache = None
        return manifest

    def local_edits(self, name: str) -> bool:
        """Return true when the installed copy diverged from its install hash.

        Absent packs and legacy index rows without a recorded hash report
        False (unguarded).
        """
        installed_name = safe_library_name(name, field="pack")
        dest = self.packs_dir / installed_name
        if not dest.is_dir():
            return False
        recorded = self._read_index().get(installed_name, _IndexEntry()).content_hash
        if not recorded:
            return False
        return pack_content_hash(dest) != recorded

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
        self._packs_cache = None

    def packs(self) -> list[InstalledPack]:
        """Return installed packs keyed by their library identity.

        The parsed list is cached for the library's lifetime (one CLI command);
        ``add``/``remove`` invalidate it.
        """
        if self._packs_cache is not None:
            return self._packs_cache
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
        self._packs_cache = installed
        return installed

    def reconcile(self) -> list[str]:
        """Return index/directory consistency problems for the pack library."""
        index = self._read_index()
        problems: list[str] = []
        for name in sorted(index):
            if not (self.packs_dir / name).is_dir():
                problems.append(f"pack '{name}' is in packs.toml but missing from packs/")
        if not self.packs_dir.is_dir():
            return problems
        for root in sorted(self.packs_dir.iterdir(), key=lambda path: path.name):
            if root.is_dir() and root.name not in index:
                problems.append(f"pack directory '{root.name}' is not recorded in packs.toml")
        return problems

    def find_pack(self, name: str) -> InstalledPack | None:
        """Return the installed pack whose library identity is ``name``, if any."""
        if "/" in name:
            return None
        installed_name = safe_library_name(name, field="pack")
        for pack in self.packs():
            if pack.name == installed_name:
                return pack
        return None

    def find_recipe(self, ref: PackRef) -> tuple[InstalledPack, RecipeEntry]:
        """Resolve a bare or qualified recipe reference."""
        return self._find_entry(ref, table=lambda manifest: manifest.recipes, noun="recipe")

    def find_hook(self, ref: PackRef) -> tuple[InstalledPack, HookEntry]:
        """Resolve a bare or qualified hook reference."""
        return self._find_entry(ref, table=lambda manifest: manifest.hooks, noun="hook")

    def _find_entry[EntryT](
        self,
        ref: PackRef,
        *,
        table: Callable[[PackManifest], Mapping[str, EntryT]],
        noun: str,
    ) -> tuple[InstalledPack, EntryT]:
        matches = [
            (pack, entry)
            for pack in self._candidate_packs(ref.pack)
            if (entry := table(pack.manifest).get(ref.name)) is not None
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            candidates = ", ".join(f"{pack.name}/{ref.name}" for pack, _ in matches)
            raise ValueError(f"ambiguous {noun} ref {ref.name!r}; candidates: {candidates}")
        raise ValueError(f"{noun} not found: {_ref_text(ref)}")

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
                content_hash=str(raw_entry.get("content_hash", "")),
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
            table.add("content_hash", entry.content_hash)
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
    validate_hook_modules(source_dir, manifest)
    for hook_name, hook_entry in manifest.hooks.items():
        if not hook_exports(hook_module_file(source_dir, hook_entry.module)):
            raise ValueError(
                f"hook module for {hook_name!r} exports neither transform() nor validate()"
            )


def local_edits_message(installed_name: str) -> str:
    """Pinned guard message shared by the store and the CLI fail-fast."""
    return (
        f"pack '{installed_name}' has local edits in the library (via edit or "
        "new recipe/hook); re-run with --discard-edits to overwrite them"
    )


def _ref_text(ref: PackRef) -> str:
    return f"{ref.pack}/{ref.name}" if ref.pack is not None else ref.name


def is_git_url(value: str) -> bool:
    """Return true when the CLI should treat ``value`` as a git URL."""
    return value.startswith(_GIT_URL_PREFIXES)


def fetch_pack_source(url: str, *, rev: str | None, dest: Path) -> Path:
    """Clone a pack source URL into ``dest`` and return the checkout path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    clone_args = ["git", "clone", "--depth", "1"]
    if rev is not None:
        clone_args.extend(["--branch", rev])
    clone_args.extend([url, str(dest)])
    try:
        _run_git(clone_args)
    except ValueError:
        if rev is None:
            raise
        if dest.exists():
            shutil.rmtree(dest)
        _run_git(["git", "clone", url, str(dest)])
        _run_git(["git", "checkout", rev], cwd=dest)
    return dest


def _run_git(args: list[str], *, cwd: Path | None = None) -> None:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ValueError(message)
