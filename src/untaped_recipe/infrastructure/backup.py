"""Backup bundle storage and restore."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from untaped_recipe.domain.plan import FileChange


@dataclass(frozen=True)
class BackupBundle:
    """One created backup bundle."""

    id: str
    path: Path


@dataclass
class BackupReservation:
    """Backup entries staged before a target write."""

    entries: list[dict[str, Any]]


@dataclass
class BackupDraft:
    """Incremental backup bundle for one apply invocation."""

    id: str
    path: Path
    recipe_name: str
    inputs: dict[str, object]
    entries: list[dict[str, Any]] = field(default_factory=list)
    _next_file_index: int = 0

    @property
    def files_dir(self) -> Path:
        """Directory containing saved before-content files."""
        return self.path / "files"

    def stage(self, changes: tuple[FileChange, ...] | list[FileChange]) -> BackupReservation:
        """Save before-content for changes without publishing metadata yet."""
        entries: list[dict[str, Any]] = []
        for change in changes:
            entry: dict[str, Any] = {
                "target": str(change.target),
                "relative_path": str(change.relative_path),
                "before_hash": _hash_text(change.before),
                "after_hash": _hash_text(change.after),
                "backup_file": None,
            }
            if change.before is not None:
                backup_file = self.files_dir / f"{self._next_file_index}"
                backup_file.write_text(change.before)
                self._next_file_index += 1
                entry["backup_file"] = str(backup_file.relative_to(self.path))
            entries.append(entry)
        return BackupReservation(entries=entries)

    def commit(self, reservation: BackupReservation) -> None:
        """Publish staged entries into the bundle metadata."""
        self.entries.extend(reservation.entries)
        self.write_metadata()

    def write_metadata(self) -> None:
        """Write the current metadata snapshot."""
        metadata = {
            "id": self.id,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "recipe": self.recipe_name,
            "inputs": self.inputs,
            "files": self.entries,
        }
        (self.path / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True))

    def discard_if_empty(self) -> None:
        """Remove an unused bundle directory."""
        if not self.entries and self.path.exists():
            shutil.rmtree(self.path)


class BackupStore:
    """Create and restore file backups."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def create(
        self,
        *,
        recipe_name: str,
        inputs: dict[str, object],
        changes: tuple[FileChange, ...] | list[FileChange],
    ) -> BackupBundle:
        """Create a backup for touched files."""
        draft = self.start(recipe_name=recipe_name, inputs=inputs)
        draft.commit(draft.stage(changes))
        return BackupBundle(id=draft.id, path=draft.path)

    def start(self, *, recipe_name: str, inputs: dict[str, object]) -> BackupDraft:
        """Start one invocation-level backup bundle."""
        backup_id = f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:8]}"
        bundle_dir = self._root / backup_id
        files_dir = bundle_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=False)
        draft = BackupDraft(
            id=backup_id,
            path=bundle_dir,
            recipe_name=recipe_name,
            inputs=inputs,
        )
        draft.write_metadata()
        return draft

    def list(self) -> list[BackupBundle]:
        """List backup bundles."""
        if not self._root.is_dir():
            return []
        return [
            BackupBundle(id=path.name, path=path)
            for path in sorted(self._root.iterdir(), key=lambda p: p.name)
            if (path / "metadata.json").is_file()
        ]

    def restore(self, backup_id: str, *, force: bool = False) -> None:
        """Restore a backup bundle."""
        bundle = self._resolve(backup_id)
        bundle_dir = bundle.path
        metadata_path = bundle_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        for entry in metadata["files"]:
            path = Path(entry["target"]) / Path(entry["relative_path"])
            current_hash = _hash_bytes(path.read_bytes()) if path.is_file() else None
            if not force and current_hash != entry["after_hash"]:
                raise ValueError(
                    f"{path} changed since backup {backup_id}; pass --force to restore"
                )
        for entry in metadata["files"]:
            path = Path(entry["target"]) / Path(entry["relative_path"])
            backup_file = entry["backup_file"]
            if backup_file is None:
                if path.exists():
                    path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundle_dir / backup_file, path)

    def metadata(self, backup_id: str) -> dict[str, object]:
        """Read raw metadata for a backup bundle."""
        bundle = self._resolve(backup_id)
        metadata = json.loads((bundle.path / "metadata.json").read_text())
        if not isinstance(metadata, dict):
            raise ValueError(f"invalid backup metadata: {backup_id}")
        return cast(dict[str, object], metadata)

    def _resolve(self, backup_id: str) -> BackupBundle:
        bundles = self.list()
        if backup_id == "latest":
            if not bundles:
                raise ValueError("backup not found: latest")
            return bundles[-1]
        exact = [bundle for bundle in bundles if bundle.id == backup_id]
        if exact:
            return exact[0]
        matches = [bundle for bundle in bundles if bundle.id.startswith(backup_id)]
        if not matches:
            raise ValueError(f"backup not found: {backup_id}")
        if len(matches) > 1:
            raise ValueError(f"backup id prefix is ambiguous: {backup_id}")
        return matches[0]


def _hash_text(content: str | None) -> str | None:
    if content is None:
        return None
    return _hash_bytes(content.encode())


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
