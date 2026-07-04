"""Backup bundle storage and restore."""

from __future__ import annotations

import builtins
import hashlib
import json
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.infrastructure.file_writer import flush_changes


@dataclass(frozen=True)
class BackupBundle:
    """One created backup bundle."""

    id: str
    path: Path


RestoreAction = Literal["restore", "create", "delete"]


@dataclass(frozen=True)
class RestoreItem:
    """One file-level restore action."""

    path: Path
    action: RestoreAction


@dataclass(frozen=True)
class _PlannedRestore:
    item: RestoreItem
    change: FileChange


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

    def stage(
        self,
        changes: tuple[FileChange, ...] | list[FileChange],
        *,
        inputs: Mapping[str, object] | None = None,
    ) -> BackupReservation:
        """Save before-content for changes without publishing metadata yet."""
        entries: list[dict[str, Any]] = []
        display_inputs = dict(inputs or {})
        for change in changes:
            entry: dict[str, Any] = {
                "target": str(change.target),
                "relative_path": str(change.relative_path),
                "before_hash": _hash_text(change.before),
                "after_hash": _hash_text(change.after),
                "backup_file": None,
                "inputs": display_inputs,
            }
            if change.before is not None:
                backup_file = self.files_dir / f"{self._next_file_index}"
                backup_file.write_text(change.before, encoding="utf-8", newline="")
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
        (self.path / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )

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
        draft.commit(draft.stage(changes, inputs=inputs))
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

    def restore(
        self,
        backup_id: str,
        *,
        force: bool = False,
        items: Sequence[RestoreItem] | None = None,
    ) -> None:
        """Restore a backup bundle."""
        if items is None:
            planned = self._restore_plan(backup_id, force=force)
        else:
            selected_paths = {item.path for item in items}
            planned = self._restore_plan(
                backup_id,
                force=force,
                selected_paths=selected_paths,
            )
            found_paths = {entry.item.path for entry in planned}
            missing = sorted(selected_paths - found_paths)
            if missing:
                raise ValueError(f"restore item not found: {missing[0]}")
        flush_changes(tuple(planned_item.change for planned_item in planned))

    def plan_restore(self, backup_id: str, *, force: bool = False) -> builtins.list[RestoreItem]:
        """Return the file-level restore actions for a backup bundle."""
        return [planned.item for planned in self._restore_plan(backup_id, force=force)]

    def _restore_plan(
        self,
        backup_id: str,
        *,
        force: bool,
        selected_paths: set[Path] | None = None,
    ) -> builtins.list[_PlannedRestore]:
        bundle = self._resolve(backup_id)
        bundle_dir = bundle.path
        metadata_path = bundle_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        planned: builtins.list[_PlannedRestore] = []
        for entry in metadata["files"]:
            target = Path(entry["target"])
            relative_path = Path(entry["relative_path"])
            path = confined_path(target, relative_path, field="relative_path")
            if selected_paths is not None and path not in selected_paths:
                continue
            current_hash = _current_hash(path)
            if not force and current_hash != entry["after_hash"]:
                raise ValueError(
                    f"{path} changed since backup {backup_id}; pass --force to restore"
                )
            backup_file = entry["backup_file"]
            before = path.read_text(encoding="utf-8", newline="") if path.is_file() else None
            after = (
                None
                if backup_file is None
                else confined_path(bundle_dir, Path(backup_file), field="backup_file").read_text(
                    encoding="utf-8",
                    newline="",
                )
            )
            planned.append(
                _PlannedRestore(
                    item=RestoreItem(path=path, action=_restore_action(path, backup_file)),
                    change=FileChange(
                        target=target,
                        relative_path=relative_path,
                        before=before,
                        after=after,
                    ),
                )
            )
        return planned

    def metadata(self, backup_id: str) -> dict[str, object]:
        """Read raw metadata for a backup bundle."""
        bundle = self._resolve(backup_id)
        metadata = json.loads((bundle.path / "metadata.json").read_text(encoding="utf-8"))
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
    return _hash_bytes(content.encode("utf-8"))


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _current_hash(path: Path) -> str | None:
    if path.is_file():
        return _hash_bytes(path.read_bytes())
    if path.exists():
        return "__untaped_recipe_non_file__"
    return None


def _restore_action(path: Path, backup_file: object) -> RestoreAction:
    if backup_file is None:
        return "delete"
    if path.exists():
        return "restore"
    return "create"
