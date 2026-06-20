"""Recipe library commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from cyclopts import Parameter
from pydantic import ValidationError
from untaped.api import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    render_rows,
    ui_context,
)
from untaped.batch import batch_apply

from untaped_recipe.cli.common import edit_path, library_root, report_config_errors
from untaped_recipe.domain.hook_project import read_hook_metadata, validate_hook_modules
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.recipe import CopyStep, Recipe, TemplateStep, TransformStep, ValidateStep
from untaped_recipe.infrastructure.hook_resolver import HookResolver
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary

app = create_app(name="recipe", help="Manage reusable recipes.")


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List recipes."""
    with report_config_errors():
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
    with report_config_errors():
        echo(RecipeLibrary(library_root()).resolve(name).read_text(), nl=False)


@app.command(name="add")
def add_command(
    source: Annotated[Path, Parameter(help="Recipe file or directory.")],
    /,
    *,
    name: Annotated[str | None, Parameter(name="--name", help="Library name.")] = None,
) -> None:
    """Copy a recipe into the library."""
    with report_config_errors():
        path = RecipeLibrary(library_root()).add(source, name=name)
        echo(str(path))


@app.command(name="check")
def check_command(
    name: Annotated[str, Parameter(help="Recipe name or path.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Validate a recipe package without target directories or hook execution."""
    with report_config_errors():
        root = library_root()
        resolution = RecipeLibrary(root).resolve_detail(name)
        row = _check_recipe(root, resolution.path, resolution.local_hook_project)
        rendered = render_rows([row], fmt=fmt, columns=columns, kind="recipe.check")
        if rendered:
            echo(rendered)
        if row["status"] == "error":
            raise SystemExit(1)


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
    with report_config_errors():
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
    with report_config_errors():
        edit_path(RecipeLibrary(library_root()).resolve(name))


def _check_recipe(
    root: Path,
    recipe_path: Path,
    local_hook_project: Path | None,
) -> dict[str, object]:
    recipe_name = recipe_path.stem
    try:
        recipe = _load_recipe(recipe_path)
        recipe_name = recipe.name
        _check_assets(recipe, recipe_path.parent)
        _check_local_hook_project(local_hook_project)
        _check_hooks(recipe, root, local_hook_project)
    except (ValueError, OSError, yaml.YAMLError, ValidationError) as exc:
        return {
            "recipe": recipe_name,
            "status": "error",
            "path": str(recipe_path),
            "error": str(exc),
        }
    return {
        "recipe": recipe_name,
        "status": "ok",
        "path": str(recipe_path),
        "error": "",
    }


def _load_recipe(path: Path) -> Recipe:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid recipe YAML: {exc}") from exc
    try:
        return Recipe.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid recipe: {exc}") from exc


def _check_assets(recipe: Recipe, recipe_dir: Path) -> None:
    for step in recipe.steps:
        if isinstance(step, TemplateStep):
            source = confined_path(recipe_dir, step.template, field="template")
            if not source.is_file():
                raise ValueError(f"template not found: {step.template}")
        elif isinstance(step, CopyStep):
            source = confined_path(recipe_dir, step.source, field="source")
            if not source.is_file():
                raise ValueError(f"copy source not found: {step.source}")


def _check_local_hook_project(local_hook_project: Path | None) -> None:
    if local_hook_project is None:
        return
    if not (local_hook_project / "pyproject.toml").is_file():
        return
    metadata = read_hook_metadata(local_hook_project)
    if not metadata.hooks:
        return
    if not (local_hook_project / "uv.lock").is_file():
        raise ValueError(f"hook project is missing uv.lock: {local_hook_project}")
    validate_hook_modules(local_hook_project, metadata)


def _check_hooks(recipe: Recipe, root: Path, local_hook_project: Path | None) -> None:
    resolver = HookResolver(global_hooks=root / "hooks")
    for step in recipe.steps:
        if isinstance(step, TransformStep | ValidateStep):
            resolver.resolve(step.hook, local_hook_project)
