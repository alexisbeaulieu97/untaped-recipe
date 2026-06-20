"""Cyclopts app composition root and apply command."""

from __future__ import annotations

import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import yaml
from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    FormatOption,
    clamp_parallel,
    create_app,
    echo,
    parse_kv_pairs,
    render_rows,
    ui_context,
)
from untaped.batch import BatchOutcome, batch_apply
from untaped.errors import ConfigError, UntapedError

from untaped_recipe.application import RunBulkApply
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.inputs import PromptFunc
from untaped_recipe.application.run_bulk import ApplyWriteError, flush_changes
from untaped_recipe.application.targets import Target, resolve_target_lines
from untaped_recipe.cli.backup_commands import app as backup_app
from untaped_recipe.cli.common import library_root, report_config_errors, settings
from untaped_recipe.cli.hook_commands import app as hook_app
from untaped_recipe.cli.pack_commands import app as pack_app
from untaped_recipe.cli.recipe_commands import app as recipe_app
from untaped_recipe.domain.plan import TargetPlan
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure import BackupStore, HookExecutor, HookResolver, RecipeLibrary
from untaped_recipe.infrastructure.backup import BackupDraft
from untaped_recipe.infrastructure.diff import unified_diff
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

app = create_app(name="recipe", help="Apply reusable local recipes to plain directories.")
app.command(recipe_app, name="recipe")
app.command(pack_app, name="pack")
app.command(hook_app, name="hook")
app.command(backup_app, name="backup")


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
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Apply a recipe to target directories."""
    with report_config_errors():
        if interactive and check:
            raise ConfigError("--interactive cannot be used with --check")
        if stdin and not yes and not dry_run and not check:
            raise ConfigError("apply requires --yes when stdin is not interactive")
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
                hook_timeout_seconds=_hook_timeout_seconds(hook_timeout),
                recipe_id=recipe_id,
            )
            _render_diffs(context.plans)
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
        has_errors = any(plan.status == "error" for plan in context.plans)
        has_drift = check and any(plan.status != "error" and plan.changes for plan in context.plans)
        if has_errors or outcome.outcome.any_failed or has_drift:
            raise SystemExit(1)


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
    recipe_resolution = RecipeLibrary(root).resolve_detail(recipe, recipe_id=recipe_id)
    recipe_path = recipe_resolution.path
    loaded = _load_recipe(recipe_path)
    targets = _targets(dirs, stdin=stdin)
    if not targets:
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
                    HookResolver(global_hooks=root / "hooks"),
                    workers=hook_workers,
                    helpers=HookHelpers(),
                )
            )
        )
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
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    return ApplyContext(
        root=root,
        recipe=loaded,
        recipe_ref=recipe_resolution.ref,
        plans=plans,
    )


def _load_recipe(recipe_path: Path) -> Recipe:
    try:
        return load_recipe_file(recipe_path)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _render_diffs(plans: list[TargetPlan]) -> None:
    for plan in plans:
        for change in plan.changes:
            diff = unified_diff(change)
            if diff:
                echo(f"# {plan.target}", err=True)
                echo(diff, err=True, nl=False)


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
    )
    if draft is not None:
        draft.discard_if_empty()
    return ApplyExecution(outcome=outcome, applied=frozenset(applied), failed=failed)


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


def _targets(positional: list[Path], *, stdin: bool) -> list[Target]:
    if stdin and positional:
        raise ConfigError("provide targets as positional args or via --stdin, not both")
    if not stdin:
        return [Target(path=path) for path in positional]
    pairs: list[tuple[int, str]] = []
    for lineno, line in enumerate(sys.stdin, start=1):
        stripped = line.strip()
        if stripped:
            pairs.append((lineno, stripped))
    if not pairs:
        raise ConfigError("no targets received on stdin")
    try:
        return resolve_target_lines(pairs)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def _input_values(raw_vars: list[str], vars_file: Path | None) -> dict[str, object]:
    values: dict[str, object] = {}
    if vars_file is not None:
        loaded = yaml.safe_load(vars_file.read_text()) or {}
        if not isinstance(loaded, dict):
            raise ConfigError("--vars file must contain a YAML mapping")
        values.update(loaded)
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
            tty = stack.enter_context(Path("/dev/tty").open("r+"))  # noqa: SIM115
        except OSError as exc:
            raise ConfigError("interactive input requires a terminal") from exc
        ui = ui_context(stdin=tty, stderr=tty, strict=True)
    else:
        ui = ui_context(strict=True)

    def ask(message: str, *, sensitive: bool) -> str:
        if sensitive:
            return ui.secret(message)
        return ui.text(message)

    return ask


def _hook_timeout_seconds(override: float | None) -> float:
    timeout = settings().hook_timeout_seconds if override is None else override
    if timeout < 0:
        raise ConfigError("--hook-timeout must be greater than or equal to 0")
    return timeout


def _preview_status(dry_run: bool, check: bool) -> str | None:
    if check:
        return "check"
    if dry_run:
        return "dry-run"
    return None


def _row(plan: TargetPlan) -> dict[str, object]:
    return {
        "target": str(plan.target),
        "status": plan.status,
        "files_changed": plan.files_changed,
        "warnings": "; ".join(plan.warnings),
        "error": plan.error,
        "inputs": plan.display_inputs,
    }
