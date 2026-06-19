"""Local reusable hook library storage."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from untaped_recipe.domain.paths import safe_library_name


@dataclass(frozen=True)
class HookEntry:
    """One hook library entry."""

    name: str
    path: Path


class HookLibrary:
    """Manage global reusable hook files."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def hooks_dir(self) -> Path:
        """Directory containing reusable hook files."""
        return self._root / "hooks"

    def add(self, source: Path, *, name: str | None = None) -> Path:
        """Copy a hook file into the library."""
        if not source.is_file():
            raise ValueError(f"hook source not found: {source}")
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_name = safe_library_name(name or source.stem)
        dest = self.hooks_dir / f"{hook_name}.py"
        if dest.exists():
            raise ValueError(f"hook already exists: {hook_name}")
        shutil.copy2(source, dest)
        return dest

    def resolve(self, name: str) -> Path:
        """Resolve a reusable hook name."""
        try:
            hook_name = safe_library_name(name, field="hook")
        except ValueError:
            hook_name = ""
        if hook_name:
            path = self.hooks_dir / f"{hook_name}.py"
            if path.is_file():
                return path
        explicit = Path(name).expanduser()
        if explicit.is_file():
            return explicit
        raise ValueError(f"hook not found: {name}")

    def remove(self, name: str) -> Path:
        """Remove a reusable hook."""
        hook_name = safe_library_name(name, field="hook")
        path = self.hooks_dir / f"{hook_name}.py"
        if not path.is_file():
            raise ValueError(f"hook not found: {name}")
        path.unlink()
        return path

    def list(self) -> list[HookEntry]:
        """List global reusable hooks."""
        if not self.hooks_dir.is_dir():
            return []
        return [
            HookEntry(name=path.stem, path=path)
            for path in sorted(self.hooks_dir.glob("*.py"), key=lambda p: p.name)
        ]
