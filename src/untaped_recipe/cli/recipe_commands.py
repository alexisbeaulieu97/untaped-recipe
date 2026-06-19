"""Recipe library commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    render_rows,
    report_errors,
    ui_context,
)
from untaped.batch import batch_apply

from untaped_recipe.cli.common import edit_path, library_root
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary

app = create_app(name="recipe", help="Manage reusable recipes.")


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List recipes."""
    with report_errors():
        rows: list[dict[str, object]] = [
            {"name": entry.name, "kind": entry.kind, "path": str(entry.path)}
            for entry in RecipeLibrary(library_root()).list()
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.recipe")
        if rendered:
            echo(rendered)


@app.command(name="show")
def show_command(name: Annotated[str, Parameter(help="Recipe name or path.")], /) -> None:
    """Print a recipe file."""
    with report_errors():
        echo(RecipeLibrary(library_root()).resolve(name).read_text(), nl=False)


@app.command(name="add")
def add_command(
    source: Annotated[Path, Parameter(help="Recipe file or directory.")],
    /,
    *,
    name: Annotated[str | None, Parameter(name="--name", help="Library name.")] = None,
) -> None:
    """Copy a recipe into the library."""
    with report_errors():
        path = RecipeLibrary(library_root()).add(source, name=name)
        echo(str(path))


@app.command(name="remove")
def remove_command(
    name: Annotated[str, Parameter(help="Recipe name.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove a recipe from the library."""
    with report_errors():
        library = RecipeLibrary(library_root())

        def _remove(item: str) -> Path:
            return library.remove(item)

        outcome = batch_apply(
            [name],
            _remove,
            verb="remove",
            noun="recipe",
            label=str,
            describe=lambda item: {"name": item},
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
        )
        if outcome.results:
            echo(str(outcome.results[0][1]))


@app.command(name="edit")
def edit_command(name: Annotated[str, Parameter(help="Recipe name or path.")], /) -> None:
    """Open a recipe in $VISUAL or $EDITOR."""
    with report_errors():
        edit_path(RecipeLibrary(library_root()).resolve(name))
