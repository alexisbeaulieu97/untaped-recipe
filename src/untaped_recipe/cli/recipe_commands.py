"""Recipe library commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    batch_apply,
    create_app,
    echo,
    render_rows,
    ui_context,
)

from untaped_recipe.application.inputs import validate_recipe_input_sources
from untaped_recipe.cli.common import edit_path, library_root, report_config_errors
from untaped_recipe.domain.hook_project import HookKind, read_hook_metadata, validate_hook_modules
from untaped_recipe.domain.paths import confined_path, is_explicit_path
from untaped_recipe.domain.recipe import CopyStep, Recipe, TemplateStep, TransformStep, ValidateStep
from untaped_recipe.infrastructure.hook_library import add_hook_to_project
from untaped_recipe.infrastructure.hook_resolver import HookResolver
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary, RecipeResolution
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

app = create_app(name="recipe", help="Manage reusable recipes.")
hook_app = create_app(name="hook", help="Manage hooks local to a standalone recipe.")
app.command(hook_app, name="hook")

_PACK_RECIPE_COMMAND_ERROR = (
    "pack recipes are managed with pack recipe commands; use pack recipe show/edit or pack check"
)


@app.command(name="init")
def init_command(
    name: Annotated[str, Parameter(help="Recipe id.")],
    /,
    *,
    library: Annotated[
        bool,
        Parameter(name="--library", negative="", help="Create inside the configured library."),
    ] = False,
) -> None:
    """Scaffold a standalone uv recipe project."""
    with report_config_errors():
        path = RecipeLibrary(library_root()).init(name, library=library)
        echo(str(path))


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
def show_command(name: Annotated[str, Parameter(help="Recipe id or path.")], /) -> None:
    """Print a recipe file."""
    with report_config_errors():
        resolution = _resolve_recipe_command(name)
        echo(resolution.path.read_text(), nl=False)


@app.command(name="add")
def add_command(
    source: Annotated[Path, Parameter(help="Standalone uv recipe project directory.")],
    /,
) -> None:
    """Install a standalone recipe project into the library."""
    with report_config_errors():
        path = RecipeLibrary(library_root()).add(source)
        echo(str(path))


@app.command(name="check")
def check_command(
    name: Annotated[str, Parameter(help="Recipe id or path.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Validate a recipe project or explicit recipe file."""
    with report_config_errors():
        root = library_root()
        try:
            resolution = _resolve_recipe_command(name, root=root)
        except ValueError as exc:
            row = _check_error_row(name, exc)
        else:
            row = _check_recipe(
                root, resolution.path, resolution.ref, resolution.local_hook_project
            )
        rendered = render_rows([row], fmt=fmt, columns=columns, kind="recipe.check")
        if rendered:
            echo(rendered)
        if row["status"] == "error":
            raise SystemExit(1)


@app.command(name="remove")
def remove_command(
    name: Annotated[str, Parameter(help="Recipe id.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove a recipe from the library."""
    with report_config_errors():
        _reject_pack_ref_for_recipe_command(name)
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
def edit_command(name: Annotated[str, Parameter(help="Recipe id or path.")], /) -> None:
    """Open a recipe in $VISUAL or $EDITOR."""
    with report_config_errors():
        resolution = _resolve_recipe_command(name)
        edit_path(resolution.path)


@hook_app.command(name="init")
def hook_init_command(
    recipe: Annotated[str, Parameter(help="Standalone recipe id or project path.")],
    hook: Annotated[str, Parameter(help="Hook name.")],
    /,
    *,
    kind: Annotated[
        Literal["transform", "validate"],
        Parameter(name="--kind", help="Hook callable kind."),
    ] = "transform",
) -> None:
    """Scaffold a hook inside a standalone recipe project."""
    with report_config_errors():
        resolution = RecipeLibrary(library_root()).resolve_detail(recipe)
        if resolution.kind != "recipe" or resolution.project_root is None:
            raise ValueError("recipe hook init requires a standalone recipe project")
        path = add_hook_to_project(resolution.project_root, hook, kind=kind)
        echo(str(path))


def _check_recipe(
    root: Path,
    recipe_path: Path,
    recipe_ref: str,
    local_hook_project: Path | None,
) -> dict[str, object]:
    try:
        recipe = _load_recipe(recipe_path)
        validate_recipe_input_sources(recipe)
        _check_project_lock(local_hook_project)
        _check_assets(recipe, recipe_path.parent)
        _check_local_hook_project(local_hook_project)
        _check_hooks(recipe, root, local_hook_project)
    except (ConfigError, ValueError, OSError) as exc:
        return {
            "recipe": recipe_ref,
            "status": "error",
            "path": str(recipe_path),
            "error": str(exc),
        }
    return {
        "recipe": recipe_ref,
        "status": "ok",
        "path": str(recipe_path),
        "error": "",
    }


def _check_error_row(name: str, exc: Exception) -> dict[str, object]:
    path = Path(name).expanduser()
    display_path = str(path) if path.exists() else ""
    return {
        "recipe": name,
        "status": "error",
        "path": display_path,
        "error": str(exc),
    }


def _resolve_recipe_command(name: str, *, root: Path | None = None) -> RecipeResolution:
    _reject_pack_ref_for_recipe_command(name)
    resolution = RecipeLibrary(root or library_root()).resolve_detail(name)
    if resolution.kind == "pack":
        raise ValueError(_PACK_RECIPE_COMMAND_ERROR)
    return resolution


def _load_recipe(path: Path) -> Recipe:
    return load_recipe_file(path)


def _reject_pack_ref_for_recipe_command(name: str) -> None:
    if ":" in name and not is_explicit_path(name):
        raise ValueError(_PACK_RECIPE_COMMAND_ERROR)


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


def _check_project_lock(local_hook_project: Path | None) -> None:
    if local_hook_project is not None and not (local_hook_project / "uv.lock").is_file():
        raise ValueError(f"recipe project is missing uv.lock: {local_hook_project}")


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
        if isinstance(step, TransformStep):
            _check_hook_kind(resolver, step.hook, local_hook_project, expected="transform")
        elif isinstance(step, ValidateStep):
            _check_hook_kind(resolver, step.hook, local_hook_project, expected="validate")


def _check_hook_kind(
    resolver: HookResolver,
    hook: str,
    local_hook_project: Path | None,
    *,
    expected: HookKind,
) -> None:
    ref = resolver.resolve(hook, local_hook_project)
    if ref.kind != expected:
        raise ValueError(f"{expected} step hook {hook!r} resolves to {ref.kind} hook")
