"""Console-script entrypoint for the ``untaped-recipe`` CLI."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from untaped.api import SkillAsset, ToolSpec, run_tool

from untaped_recipe.cli import app
from untaped_recipe.settings import RecipeSettings

SPEC = ToolSpec(
    command="untaped-recipe",
    section="recipe",
    profile_model=RecipeSettings,
    skills=(
        SkillAsset(
            name="untaped-recipe",
            source=Path(str(files("untaped_recipe").joinpath("skills", "untaped-recipe"))),
            description="Use the untaped-recipe CLI to apply local recipe projects and packs.",
        ),
    ),
)


def main() -> object:
    """Run the ``untaped-recipe`` CLI."""
    return run_tool(app, SPEC)


if __name__ == "__main__":
    main()
