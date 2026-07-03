"""Recipe pack library commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from cyclopts import Parameter
from pydantic import ValidationError
from untaped.api import (
    ColumnsOption,
    FormatOption,
    batch_apply,
    create_app,
    echo,
    finish,
    render_rows,
    ui_context,
)

from untaped_recipe.cli.common import edit_path, library_root, report_config_errors
from untaped_recipe.cli.recipe_commands import (
    _check_local_hook_project,
    _check_project_lock,
    _check_recipe,
)
from untaped_recipe.domain.recipe_project import read_recipe_project_metadata
from untaped_recipe.infrastructure.hook_library import add_hook_to_project
from untaped_recipe.infrastructure.pack_library import PackLibrary
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary, RecipeResolution

app = create_app(name="pack", help="Manage reusable recipe packs.")
recipe_app = create_app(name="recipe", help="Manage recipes inside a pack.")
hook_app = create_app(name="hook", help="Manage hooks local to a pack.")
app.command(recipe_app, name="recipe")
app.command(hook_app, name="hook")


@app.command(name="init")
def init_command(
    name: Annotated[str, Parameter(help="Pack id.")],
    /,
    *,
    library: Annotated[
        bool,
        Parameter(name="--library", negative="", help="Create inside the configured library."),
    ] = False,
) -> None:
    """Scaffold an empty uv recipe pack project."""
    with report_config_errors():
        path = PackLibrary(library_root()).init(name, library=library)
        echo(str(path))


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List recipe packs."""
    with report_config_errors():
        rows = [
            {"name": entry.name, "recipes": entry.recipes_count, "path": str(entry.path)}
            for entry in PackLibrary(library_root()).list()
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.pack")
        if rendered:
            echo(rendered)


@app.command(name="show")
def show_command(name: Annotated[str, Parameter(help="Pack id or path.")], /) -> None:
    """Print a pack pyproject file."""
    with report_config_errors():
        echo(
            (PackLibrary(library_root()).resolve(name) / "pyproject.toml").read_text(
                encoding="utf-8"
            ),
            nl=False,
        )


@app.command(name="add")
def add_command(
    source: Annotated[Path, Parameter(help="Recipe pack project directory.")], /
) -> None:
    """Copy a recipe pack into the library."""
    with report_config_errors():
        path = PackLibrary(library_root()).add(source)
        echo(str(path))


@app.command(name="check")
def check_command(
    name: Annotated[str, Parameter(help="Pack id or path.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Validate a recipe pack without target directories or hook execution."""
    with report_config_errors():
        row = _check_pack(name)
        rendered = render_rows([row], fmt=fmt, columns=columns, kind="recipe.pack_check")
        if rendered:
            echo(rendered)
        finish(row["status"] == "error")


@app.command(name="remove")
def remove_command(
    name: Annotated[str, Parameter(help="Pack id.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove a recipe pack from the library."""
    with report_config_errors():
        library = PackLibrary(library_root())

        def _remove(item: str) -> Path:
            return library.remove(item)

        outcome = batch_apply(
            [name],
            _remove,
            verb="remove",
            noun="pack",
            label=str,
            describe=lambda item: {"name": item},
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
        )
        if outcome.results:
            echo(str(outcome.results[0][1]))


@app.command(name="edit")
def edit_command(name: Annotated[str, Parameter(help="Pack id or path.")], /) -> None:
    """Open a pack pyproject in $VISUAL or $EDITOR."""
    with report_config_errors():
        edit_path(PackLibrary(library_root()).resolve(name) / "pyproject.toml")


@recipe_app.command(name="init")
def recipe_init_command(
    pack: Annotated[str, Parameter(help="Pack id or project path.")],
    recipe: Annotated[str, Parameter(help="Recipe id.")],
    /,
) -> None:
    """Scaffold a recipe inside a pack."""
    with report_config_errors():
        path = PackLibrary(library_root()).init_recipe(pack, recipe)
        echo(str(path))


@recipe_app.command(name="list")
def recipe_list_command(
    pack: Annotated[str, Parameter(help="Pack id or project path.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List recipes inside a pack."""
    with report_config_errors():
        rows: list[dict[str, object]] = [
            {"name": entry.name, "path": str(entry.path)}
            for entry in PackLibrary(library_root()).list_recipes(pack)
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.pack_recipe")
        if rendered:
            echo(rendered)


@recipe_app.command(name="show")
def recipe_show_command(
    pack: Annotated[str, Parameter(help="Pack id or project path.")],
    recipe: Annotated[str, Parameter(help="Recipe id.")],
    /,
) -> None:
    """Print a recipe from a pack."""
    with report_config_errors():
        echo(
            PackLibrary(library_root()).recipe_path(pack, recipe).read_text(encoding="utf-8"),
            nl=False,
        )


@recipe_app.command(name="edit")
def recipe_edit_command(
    pack: Annotated[str, Parameter(help="Pack id or project path.")],
    recipe: Annotated[str, Parameter(help="Recipe id.")],
    /,
) -> None:
    """Open a pack recipe in $VISUAL or $EDITOR."""
    with report_config_errors():
        edit_path(PackLibrary(library_root()).recipe_path(pack, recipe))


@recipe_app.command(name="remove")
def recipe_remove_command(
    pack: Annotated[str, Parameter(help="Pack id or project path.")],
    recipe: Annotated[str, Parameter(help="Recipe id.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove a recipe from a pack."""
    with report_config_errors():
        library = PackLibrary(library_root())

        def _remove(item: str) -> Path:
            return library.remove_recipe(pack, item)

        outcome = batch_apply(
            [recipe],
            _remove,
            verb="remove",
            noun="pack recipe",
            label=str,
            describe=lambda item: {"pack": pack, "recipe": item},
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
        )
        if outcome.results:
            echo(str(outcome.results[0][1]))


@hook_app.command(name="init")
def hook_init_command(
    pack: Annotated[str, Parameter(help="Pack id or project path.")],
    hook: Annotated[str, Parameter(help="Hook name.")],
    /,
    *,
    kind: Annotated[
        Literal["transform", "validate"],
        Parameter(name="--kind", help="Hook callable kind."),
    ] = "transform",
) -> None:
    """Scaffold a hook inside a pack project."""
    with report_config_errors():
        project_root = PackLibrary(library_root()).resolve(pack)
        path = add_hook_to_project(project_root, hook, kind=kind)
        echo(str(path))


def _check_pack(pack: str) -> dict[str, object]:
    root = library_root()
    try:
        project_root = PackLibrary(root).resolve(pack)
        metadata = read_recipe_project_metadata(project_root)
        if metadata.pack is None:
            raise ValueError("pack metadata is missing pack id")
        _check_project_lock(project_root)
        _check_local_hook_project(project_root)
        for recipe_id in metadata.recipe_paths():
            resolution = RecipeLibrary(root).resolve_detail(str(project_root), recipe_id=recipe_id)
            row = _check_recipe_assets_and_hooks(root, resolution)
            if row["status"] == "error":
                raise ValueError(f"{recipe_id}: {row['error']}")
    except (ValueError, OSError, yaml.YAMLError, ValidationError) as exc:
        return {
            "pack": pack,
            "status": "error",
            "path": "",
            "recipes": 0,
            "error": str(exc),
        }
    return {
        "pack": metadata.pack,
        "status": "ok",
        "path": str(project_root),
        "recipes": len(metadata.recipes),
        "error": "",
    }


def _check_recipe_assets_and_hooks(root: Path, resolution: RecipeResolution) -> dict[str, object]:
    return _check_recipe(root, resolution.path, resolution.ref, resolution.local_hook_project)
