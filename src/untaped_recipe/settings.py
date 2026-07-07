"""Settings for the recipe tool."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class RecipeSettings(BaseModel):
    """Profile settings for local recipe storage."""

    library_root: Path = Field(
        default_factory=lambda: Path("~/.untaped/untaped-recipes").expanduser()
    )
    hook_timeout_seconds: float = Field(default=60, ge=0)
    hook_startup_timeout_seconds: float = Field(default=300, ge=0)
    backup_keep: int | None = Field(default=None, ge=1)
    backup_max_age_days: int | None = Field(default=None, ge=1)
    preview_max_rows: int = Field(default=50, ge=0)
