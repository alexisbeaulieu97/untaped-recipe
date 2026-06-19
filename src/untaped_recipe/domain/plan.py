"""Pure result models for recipe planning and application."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from untaped_recipe.domain.paths import safe_relative_path

ApplyStatus = Literal["planned", "applied", "dry-run", "error"]


class Verdict(BaseModel):
    """Read-only hook verdict."""

    model_config = ConfigDict(frozen=True)

    status: Literal["pass", "warn", "fail"]
    message: str = ""

    @property
    def failed(self) -> bool:
        """Whether this verdict must abort the target plan."""
        return self.status == "fail"


class FileChange(BaseModel):
    """One planned file mutation for one target directory."""

    model_config = ConfigDict(frozen=True)

    target: Path
    relative_path: Path
    before: str | None
    after: str | None

    @field_validator("relative_path")
    @classmethod
    def _safe_relative_path(cls, value: Path) -> Path:
        return safe_relative_path(value, field="relative_path")

    @property
    def path(self) -> Path:
        """Absolute path of the changed file."""
        return self.target / self.relative_path

    @property
    def kind(self) -> str:
        """Human-readable mutation kind."""
        if self.before is None and self.after is not None:
            return "create"
        if self.before is not None and self.after is None:
            return "remove"
        return "modify"


class TargetPlan(BaseModel):
    """Planned changes for one target directory."""

    model_config = ConfigDict(frozen=True)

    target: Path
    status: ApplyStatus
    changes: tuple[FileChange, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str = ""

    @property
    def files_changed(self) -> int:
        """Count of files whose content or existence changes."""
        return len(self.changes)
