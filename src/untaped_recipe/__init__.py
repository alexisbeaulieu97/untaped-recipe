"""untaped-recipe: apply reusable local recipes to plain directories."""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped_recipe.settings import RecipeSettings

if TYPE_CHECKING:
    from cyclopts import App

__all__ = ["RecipeSettings", "app"]


def __getattr__(name: str) -> App:
    """Lazily re-export the Cyclopts app without importing the CLI at package import."""
    if name == "app":
        from untaped_recipe.cli import app  # noqa: PLC0415

        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
