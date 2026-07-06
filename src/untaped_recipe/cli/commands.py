"""Cyclopts app composition root and apply command."""

from __future__ import annotations

import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter
from untaped.api import (
    BatchOutcome,
    ColumnsOption,
    ConfigError,
    FormatOption,
    UntapedError,
    batch_apply,
    clamp_parallel,
    create_app,
    echo,
    emit,
    finish,
    parse_kv_pairs,
    read_stdin,
    render_rows,
    ui_context,
)

from untaped_recipe.application import RunBulkApply
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.check_pack import check_library, check_ref
from untaped_recipe.application.inputs import PromptFunc
from untaped_recipe.application.resolution import resolve_apply_recipe
from untaped_recipe.application.run_bulk import ApplyWriteError, flush_changes
from untaped_recipe.application.targets import Target, resolve_target_lines
from untaped_recipe.cli.backup_commands import app as backup_app
from untaped_recipe.cli.common import (
    edit_path,
    hook_timeout_seconds,
    library_root,
    load_yaml_mapping_file,
    report_config_errors,
)
from untaped_recipe.cli.detail import hook_detail, pack_detail, recipe_detail
from untaped_recipe.cli.hook_commands import app as hook_app
from untaped_recipe.cli.preview import PreviewMode, render_preview
from untaped_recipe.cli.test_commands import test_command
from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.hook_project import (
    hook_module_file,
)
from untaped_recipe.domain.pack import HookEntry, PackManifest, RecipeEntry, parse_ref
from untaped_recipe.domain.paths import safe_library_name
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure import BackupStore, HookExecutor, HookResolver, pack_scaffold
from untaped_recipe.infrastructure.backup import BackupDraft
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.pack_store import InstalledPack, fetch_pack_source, is_git_url
from untaped_recipe.infrastructure.pack_store import PackLibrary as UnifiedPackLibrary
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

app = create_app(name="recipe", help="Apply reusable local recipes to plain directories.")
new_app = create_app(name="new", help="Scaffold recipe packs, recipes, and hooks.")
app.command(new_app, name="new")
app.command(hook_app, name="hook")
app.command(backup_app, name="backup")
app.command(test_command, name="test")

MessageKind = Literal["success", "warning", "error", "info"]


@dataclass(frozen=True)
class _ResolvedTarget:
    """A pack/recipe/hook ref resolved for `show` and `edit`."""

    pack: InstalledPack
    name: str | None = None
    recipe: RecipeEntry | None = None
    hook: HookEntry | None = None


@dataclass(frozen=True)
class ApplyContext:
    """Prepared apply state."""

    root: Path
    recipe: Recipe
    recipe_ref: str
    plans: list[TargetPlan]


@dataclass(frozen=True)
class ApplyExecution:
    """Executed apply state used for stable row rendering."""

    outcome: BatchOutcome[TargetPlan, TargetPlan]
    applied: frozenset[int]
    failed: dict[int, str]
    backup_id: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class TargetInput:
    """Resolved target records plus whether stdin supplied nonblank lines."""

    targets: list[Target]
    stdin_records: bool = False


@new_app.command(name="pack")
def new_pack_command(name: Annotated[str, Parameter(help="Pack name.")], /) -> None:
    """Scaffold a recipe pack."""
    with report_config_errors():
        pack_name = safe_library_name(name, field="pack")
        path = pack_scaffold.scaffold_pack(Path.cwd() / pack_name, pack_name)
        echo(str(path))


@new_app.command(name="recipe")
def new_recipe_command(ref: Annotated[str, Parameter(help="<pack>/<recipe>.")], /) -> None:
    """Scaffold a recipe inside a pack."""
    with report_config_errors():
        pack_dir, name = _new_pack_child(ref)
        path = pack_scaffold.scaffold_recipe(pack_dir, name)
        echo(str(path))


@new_app.command(name="hook")
def new_hook_command(
    ref: Annotated[str, Parameter(help="<pack>/<hook>.")],
    /,
    *,
    kind: Annotated[
        Literal["transform", "validate"],
        Parameter(name="--kind", help="Hook callable stub kind."),
    ] = "transform",
) -> None:
    """Scaffold a hook inside a pack."""
    with report_config_errors():
        pack_dir, name = _new_pack_child(ref)
        path = pack_scaffold.scaffold_hook(pack_dir, name, kind=kind)
        echo(str(path))


@app.command(name="apply")
def apply_command(
    recipe_ref: Annotated[str, Parameter(help="Recipe id, pack:recipe ref, or path.")],
    dirs: Annotated[list[Path] | None, Parameter(help="Target directories.")] = None,
    *,
    recipe_id: Annotated[
        str | None,
        Parameter(name="--recipe", help="Recipe id when applying a local pack path."),
    ] = None,
    stdin: Annotated[
        bool,
        Parameter(
            name="--stdin",
            negative="",
            help="Read target paths or pipe records from stdin.",
        ),
    ] = False,
    var: Annotated[
        list[str] | None,
        Parameter(name="--var", help="Input override as key=value.", consume_multiple=False),
    ] = None,
    vars_file: Annotated[
        Path | None,
        Parameter(name="--vars", help="YAML file containing input overrides."),
    ] = None,
    input_from: Annotated[
        list[str] | None,
        Parameter(
            name="--input-from",
            help="Derive one input from a per-target Jinja expression as key=template.",
            consume_multiple=False,
        ),
    ] = None,
    interactive: Annotated[
        bool,
        Parameter(name="--interactive", negative="", help="Prompt for unresolved inputs."),
    ] = False,
    dry_run: Annotated[
        bool,
        Parameter(name="--dry-run", negative="", help="Preview without writing."),
    ] = False,
    check: Annotated[
        bool,
        Parameter(
            name="--check",
            negative="",
            help="Preview and exit non-zero when changes would be made.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
    backup: Annotated[
        bool,
        Parameter(name="--backup", negative="--no-backup", help="Create backups before writing."),
    ] = True,
    parallel: Annotated[
        int,
        Parameter(name=["--parallel", "-j"], help="Target planning workers."),
    ] = 1,
    hook_timeout: Annotated[
        float | None,
        Parameter(name="--hook-timeout", help="Per-hook timeout in seconds; 0 disables."),
    ] = None,
    preview: Annotated[
        PreviewMode | None,
        Parameter(
            name="--preview",
            help=(
                "Preview style: table, diff, or none. "
                "Defaults to none with --check, table otherwise."
            ),
        ),
    ] = None,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Apply a recipe to target directories."""
    with report_config_errors():
        if interactive and check:
            raise ConfigError("--interactive cannot be used with --check")
        if stdin and not yes and not dry_run and not check:
            raise ConfigError(
                "apply requires --yes with --stdin unless --dry-run or --check is used"
            )
        with ExitStack() as stack:
            prompt = _interactive_prompt(stdin=stdin, interactive=interactive, stack=stack)
            context = _apply_context(
                recipe_ref,
                dirs=list(dirs or []),
                stdin=stdin,
                raw_vars=var or [],
                vars_file=vars_file,
                raw_input_from=input_from or [],
                interactive=interactive,
                prompt=prompt,
                parallel=parallel,
                hook_timeout_seconds=hook_timeout_seconds(hook_timeout),
                recipe_id=recipe_id,
            )
            effective_preview = _effective_preview(preview, check=check)
            render_preview(context.recipe, context.plans, preview=effective_preview)
            outcome = _execute_plans(
                context,
                backup=backup and not check,
                yes=yes or check,
                dry_run=dry_run or check,
            )
        rows = _outcome_rows(
            context.plans,
            outcome,
            recipe_ref=context.recipe_ref,
            preview_status=_preview_status(dry_run, check),
        )
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.outcome")
        if rendered:
            echo(rendered)
        _render_result_summary(context.plans, outcome, check=check, dry_run=dry_run)
        has_errors = any(plan.status == "error" for plan in context.plans)
        has_drift = check and any(plan.status != "error" and plan.changes for plan in context.plans)
        finish(has_errors or outcome.outcome.any_failed or has_drift)


@app.command(name="add")
def add_command(
    source: Annotated[str, Parameter(help="Pack project path or git URL.")],
    /,
    *,
    rev: Annotated[str | None, Parameter(name="--rev", help="Git revision to install.")] = None,
    name: Annotated[
        str | None,
        Parameter(name="--name", help="Installed pack identity override."),
    ] = None,
    force: Annotated[
        bool,
        Parameter(name="--force", negative="", help="Replace an existing installed pack."),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Install a recipe pack from a path or git URL."""
    with report_config_errors(), tempfile.TemporaryDirectory() as temp_root:
        source_dir = (
            fetch_pack_source(source, rev=rev, dest=Path(temp_root) / "pack")
            if is_git_url(source)
            else Path(source).expanduser()
        )
        manifest = PackManifest.from_pyproject(source_dir)
        installed_name = name or manifest.name
        _render_pack_add_preview(installed_name, manifest)
        library = UnifiedPackLibrary(library_root=library_root())

        def _install(item: str) -> PackManifest:
            del item
            return library.add(
                source_dir,
                source=source,
                rev=rev,
                name=name,
                force=force,
            )

        outcome = batch_apply(
            [source],
            _install,
            verb="add",
            noun="pack",
            label=lambda item: installed_name,
            describe=lambda item: {"name": installed_name, "source": item},
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
        )
        if outcome.results:
            echo(installed_name)


@app.command(name="list")
def list_command(
    *,
    hooks: Annotated[
        bool,
        Parameter(name="--hooks", negative="", help="List hooks instead of recipes."),
    ] = False,
    packs: Annotated[
        bool,
        Parameter(name="--packs", negative="", help="List installed packs."),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """List installed recipes, hooks, or packs."""
    with report_config_errors():
        if hooks and packs:
            raise ConfigError("choose one of --hooks or --packs")
        installed = UnifiedPackLibrary(library_root=library_root()).packs()
        if packs:
            rows = [_pack_row(pack) for pack in installed]
            kind = "recipe.pack"
        elif hooks:
            rows = [
                _hook_row(pack, name, entry) for pack in installed for name, entry in _hooks(pack)
            ]
            kind = "recipe.hook"
        else:
            rows = [
                _recipe_row(pack, name, entry)
                for pack in installed
                for name, entry in _recipes(pack)
            ]
            kind = "recipe.recipe"
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind=kind)
        if rendered:
            echo(rendered)


@app.command(name="show")
def show_command(
    ref_text: Annotated[str, Parameter(help="Pack, recipe, or hook ref.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show an installed pack, recipe, or hook."""
    with report_config_errors():
        library = UnifiedPackLibrary(library_root=library_root())
        target = _resolve_target(library, ref_text)
        if target.recipe is not None:
            recipe_path = target.pack.root / target.recipe.path
            emit(
                recipe_detail(
                    f"{target.pack.name}/{target.name}",
                    _load_recipe(recipe_path),
                    recipe_path,
                ),
                fmt=fmt,
                columns=columns,
                kind="recipe.recipe",
            )
        elif target.hook is not None:
            module_file = hook_module_file(target.pack.root, target.hook.module)
            emit(
                hook_detail(
                    f"{target.pack.name}/{target.name}",
                    target.hook,
                    hook_exports(module_file),
                    module_file,
                ),
                fmt=fmt,
                columns=columns,
                kind="recipe.hook",
            )
        else:
            emit(
                pack_detail(target.pack.name, target.pack.manifest, target.pack.root),
                fmt=fmt,
                columns=columns,
                kind="recipe.pack",
            )


@app.command(name="check")
def check_command(
    ref_text: Annotated[
        str | None,
        Parameter(help="Installed pack, recipe ref, or explicit path."),
    ] = None,
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Validate a pack, recipe, or the whole installed library."""
    with report_config_errors():
        root = library_root()
        rows = check_library(root) if ref_text is None else [check_ref(root, ref_text)]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.check")
        if rendered:
            echo(rendered)
        finish(any(row["status"] == "error" for row in rows))


@app.command(name="remove")
def remove_command(
    name: Annotated[str, Parameter(help="Installed pack identity.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove an installed pack."""
    with report_config_errors():
        library = UnifiedPackLibrary(library_root=library_root())

        def _remove(item: str) -> str:
            library.remove(item)
            return item

        batch_apply(
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


@app.command(name="edit")
def edit_command(ref_text: Annotated[str, Parameter(help="Pack, recipe, or hook ref.")], /) -> None:
    """Open a pack pyproject, recipe file, or hook module in $VISUAL or $EDITOR."""
    with report_config_errors():
        library = UnifiedPackLibrary(library_root=library_root())
        target = _resolve_target(library, ref_text)
        if target.recipe is not None:
            edit_path(target.pack.root / target.recipe.path)
        elif target.hook is not None:
            edit_path(hook_module_file(target.pack.root, target.hook.module))
        else:
            edit_path(target.pack.root / "pyproject.toml")


def _apply_context(
    recipe: str,
    *,
    dirs: list[Path],
    stdin: bool,
    raw_vars: list[str],
    vars_file: Path | None,
    raw_input_from: list[str],
    interactive: bool,
    prompt: PromptFunc | None,
    parallel: int,
    hook_timeout_seconds: float,
    recipe_id: str | None = None,
) -> ApplyContext:
    root = library_root()
    recipe_resolution = resolve_apply_recipe(root, recipe, recipe_id=recipe_id)
    recipe_path = recipe_resolution.path
    loaded = _load_recipe(recipe_path)
    target_input = _targets(dirs, stdin=stdin)
    targets = target_input.targets
    if not targets:
        if target_input.stdin_records:
            return ApplyContext(
                root=root,
                recipe=loaded,
                recipe_ref=recipe_resolution.ref,
                plans=[],
            )
        raise ConfigError("at least one target directory is required (or use --stdin)")
    inputs = _input_values(raw_vars, vars_file)
    input_from = _input_sources(raw_input_from)
    workers = clamp_parallel(max(parallel, 1), cap=32, policy="recipe planning cap")
    with UvHookWorkerPool(
        max_workers_per_project=workers,
        hook_timeout_seconds=hook_timeout_seconds,
    ) as hook_workers:
        runner = RunBulkApply(
            ApplyRecipe(
                HookExecutor(
                    HookResolver(library_root=root),
                    workers=hook_workers,
                    helpers=HookHelpers(),
                )
            )
        )
        ui = ui_context(strict=False)
        with ui.progress("Planning targets") as progress:
            try:
                plans = runner.plan(
                    recipe=loaded,
                    recipe_dir=recipe_path.parent,
                    local_hook_project=recipe_resolution.local_hook_project,
                    targets=targets,
                    inputs=inputs,
                    input_from=input_from,
                    interactive=interactive,
                    prompt=prompt,
                    parallel=workers,
                    on_progress=lambda done, total: progress.update(
                        f"{done}/{total}",
                        fraction=done / total if total else None,
                    ),
                )
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
    return ApplyContext(
        root=root,
        recipe=loaded,
        recipe_ref=recipe_resolution.ref,
        plans=plans,
    )


def _resolve_target(library: UnifiedPackLibrary, ref_text: str) -> _ResolvedTarget:
    """Resolve a ref to a pack, recipe, or hook, preferring recipes' not-found error."""
    pack = library.find_pack(ref_text)
    if pack is not None:
        return _ResolvedTarget(pack=pack)
    ref = parse_ref(ref_text)
    try:
        recipe_pack, recipe = library.find_recipe(ref)
    except ValueError as recipe_error:
        try:
            hook_pack, hook = library.find_hook(ref)
        except ValueError:
            raise recipe_error from None
        return _ResolvedTarget(pack=hook_pack, name=ref.name, hook=hook)
    return _ResolvedTarget(pack=recipe_pack, name=ref.name, recipe=recipe)


def _recipes(pack: InstalledPack) -> list[tuple[str, RecipeEntry]]:
    return sorted(pack.manifest.recipes.items())


def _hooks(pack: InstalledPack) -> list[tuple[str, HookEntry]]:
    return sorted(pack.manifest.hooks.items())


def _pack_row(pack: InstalledPack) -> dict[str, object]:
    return {
        "name": pack.name,
        "version": pack.installed_version,
        "path": str(pack.root),
        "source": pack.source,
        "rev": pack.rev,
        "recipes": len(pack.manifest.recipes),
        "hooks": len(pack.manifest.hooks),
    }


def _recipe_row(pack: InstalledPack, name: str, entry: RecipeEntry) -> dict[str, object]:
    return {
        "pack": pack.name,
        "name": name,
        "ref": f"{pack.name}/{name}",
        "path": str(pack.root / entry.path),
    }


def _hook_row(pack: InstalledPack, name: str, entry: HookEntry) -> dict[str, object]:
    return {
        "pack": pack.name,
        "name": name,
        "ref": f"{pack.name}/{name}",
        "module": entry.module,
        "path": str(hook_module_file(pack.root, entry.module)),
    }


def _render_pack_add_preview(installed_name: str, manifest: PackManifest) -> None:
    echo(f"Pack: {installed_name}", err=True)
    recipes = ", ".join(sorted(manifest.recipes)) or "(none)"
    hooks = ", ".join(sorted(manifest.hooks)) or "(none)"
    echo(f"Recipes: {recipes}", err=True)
    echo(f"Hooks: {hooks}", err=True)


def _new_pack_child(ref_text: str) -> tuple[Path, str]:
    if _is_explicit_new_path(ref_text):
        path = Path(ref_text).expanduser()
        if not path.name or path.parent == Path("."):
            raise ValueError("qualified refs must use <pack>/<name>")
        return path.parent, path.name
    ref = parse_ref(ref_text)
    if ref.pack is None:
        raise ValueError("qualified refs must use <pack>/<name>")
    installed_name = safe_library_name(ref.pack, field="pack")
    for installed in UnifiedPackLibrary(library_root=library_root()).packs():
        if installed.name == installed_name:
            return installed.root, ref.name
    raise ValueError(f"pack not found: {ref.pack}")


def _is_explicit_new_path(value: str) -> bool:
    return value.startswith(("/", "./", "../", "~"))


def _load_recipe(recipe_path: Path) -> Recipe:
    try:
        return load_recipe_file(recipe_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _effective_preview(preview: PreviewMode | None, *, check: bool) -> PreviewMode:
    if preview is not None:
        return preview
    if check:
        return "none"
    return "table"


def _execute_plans(
    context: ApplyContext,
    *,
    backup: bool,
    yes: bool,
    dry_run: bool,
) -> ApplyExecution:
    actionable = [plan for plan in context.plans if plan.status != "error" and plan.changes]
    store = BackupStore(context.root / "backups")
    draft: BackupDraft | None = None
    applied: set[int] = set()
    failed: dict[int, str] = {}

    def _apply(plan: TargetPlan) -> TargetPlan:
        nonlocal draft
        reservation = None
        try:
            if backup:
                if draft is None:
                    draft = store.start(
                        recipe_name=context.recipe_ref,
                        inputs={},
                    )
                reservation = draft.stage(plan.changes, inputs=plan.display_inputs)
            flush_changes(plan.changes)
            if reservation is not None and draft is not None:
                draft.commit(reservation)
            applied.add(id(plan))
            return plan
        except UntapedError as exc:
            if (
                reservation is not None
                and draft is not None
                and isinstance(exc, ApplyWriteError)
                and exc.rollback_incomplete
            ):
                draft.commit(reservation)
            failed[id(plan)] = str(exc)
            raise

    outcome = batch_apply(
        actionable,
        _apply,
        verb="apply",
        noun="target",
        label=lambda plan: str(plan.target),
        describe=_row,
        ui=ui_context(strict=False),
        destructive=True,
        assume_yes=yes,
        preview_only=dry_run,
        render_generic_preview=False,
    )
    backup_id = draft.id if draft is not None and draft.entries else None
    if draft is not None:
        draft.discard_if_empty()
    cancelled = bool(actionable) and not dry_run and not outcome.results and not outcome.failed
    return ApplyExecution(
        outcome=outcome,
        applied=frozenset(applied),
        failed=failed,
        backup_id=backup_id,
        cancelled=cancelled,
    )


def _outcome_rows(
    plans: list[TargetPlan],
    execution: ApplyExecution,
    *,
    recipe_ref: str,
    preview_status: str | None,
) -> list[dict[str, object]]:
    rows = [{**_row(plan), "recipe": recipe_ref} for plan in plans]
    if preview_status is not None:
        return [
            {**row, "status": preview_status} if row["status"] == "planned" else row for row in rows
        ]
    if not execution.outcome.results and not execution.failed:
        return rows
    rendered: list[dict[str, object]] = []
    for plan, row in zip(plans, rows, strict=True):
        plan_id = id(plan)
        if plan_id in execution.failed:
            rendered.append({**row, "status": "error", "error": execution.failed[plan_id]})
        elif plan_id in execution.applied:
            rendered.append({**row, "status": "applied"})
        else:
            rendered.append(row)
    return rendered


def _targets(positional: list[Path], *, stdin: bool) -> TargetInput:
    if stdin and positional:
        raise ConfigError("provide targets as positional args or via --stdin, not both")
    if not stdin:
        return TargetInput([Target(path=path) for path in positional])
    lines = read_stdin()
    if not lines:
        raise ConfigError("no targets received on stdin")
    try:
        return TargetInput(
            resolve_target_lines(list(enumerate(lines, start=1))),
            stdin_records=True,
        )
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _input_values(raw_vars: list[str], vars_file: Path | None) -> dict[str, object]:
    values: dict[str, object] = {}
    if vars_file is not None:
        values.update(load_yaml_mapping_file(vars_file, flag="--vars"))
    values.update(parse_kv_pairs(raw_vars, flag="--var"))
    return values


def _input_sources(raw_sources: list[str]) -> dict[str, str]:
    parsed = parse_kv_pairs(raw_sources, flag="--input-from")
    return {name: str(template) for name, template in parsed.items()}


def _interactive_prompt(
    *,
    stdin: bool,
    interactive: bool,
    stack: ExitStack,
) -> PromptFunc | None:
    if not interactive:
        return None
    if stdin:
        try:
            tty = stack.enter_context(
                Path("/dev/tty").open("r+", encoding="utf-8")  # noqa: SIM115
            )
        except OSError as exc:
            raise ConfigError("interactive input requires a terminal") from exc
        ui = ui_context(stdin=tty, stderr=tty, strict=True)
    else:
        ui = ui_context(strict=True)

    def ask(
        message: str,
        *,
        sensitive: bool,
        default: object | None = None,
        required: bool = True,
    ) -> object:
        if sensitive:
            return ui.secret(message, required=required)
        text_default = None if default is None else str(default)
        return ui.text(message, default=text_default, required=required)

    return ask


def _preview_status(dry_run: bool, check: bool) -> str | None:
    if check:
        return "check"
    if dry_run:
        return "dry-run"
    return None


def _render_result_summary(
    plans: list[TargetPlan],
    execution: ApplyExecution,
    *,
    check: bool,
    dry_run: bool,
) -> None:
    failed = sum(1 for plan in plans if plan.status == "error") + len(execution.failed)
    changed = sum(1 for plan in plans if plan.status != "error" and plan.changes)
    unchanged = sum(1 for plan in plans if plan.status != "error" and not plan.changes)
    applied = len(execution.applied)
    ui = ui_context(strict=False)
    if check:
        kind: MessageKind = "warning" if failed or changed else "info"
        ui.message(
            kind,
            f"Recipe check: {changed} would change, {unchanged} unchanged, {failed} failed",
        )
        return
    if dry_run:
        kind = "warning" if failed else "info"
        ui.message(
            kind,
            f"Recipe dry run: {changed} would change, {unchanged} unchanged, {failed} failed",
        )
        return
    if execution.cancelled:
        ui.message(
            "warning",
            "Recipe apply cancelled: "
            f"{_plural(changed, 'changing target')} not applied, "
            f"{unchanged} unchanged, {failed} failed",
        )
        return
    kind = "warning" if failed else "info"
    backup = f", backup {execution.backup_id}" if execution.backup_id else ""
    ui.message(
        kind,
        f"Recipe apply: {applied} applied, {unchanged} unchanged, {failed} failed{backup}",
    )


def _plural(count: int, noun: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _row(plan: TargetPlan) -> dict[str, object]:
    return {
        "target": str(plan.target),
        "status": plan.status,
        "files_changed": plan.files_changed,
        "warnings": "; ".join(plan.warnings),
        "error": plan.error,
        "inputs": plan.display_inputs,
    }
