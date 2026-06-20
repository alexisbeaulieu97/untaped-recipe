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
