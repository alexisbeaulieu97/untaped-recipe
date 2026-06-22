"""Human preview rendering for recipe apply."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import Literal

from untaped.api import echo, ui_context

from untaped_recipe.application.inputs import has_sensitive_inputs
from untaped_recipe.domain.plan import FileChange, TargetPlan
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.diff import unified_diff

PreviewMode = Literal["table", "diff", "none"]


def render_preview(
    recipe: Recipe,
    plans: list[TargetPlan],
    *,
    preview: PreviewMode,
) -> None:
    """Render the selected stderr preview for planned targets."""
    echo(preview_summary(plans), err=True)
    if preview == "none":
        return
    if preview == "diff":
        _render_diff_preview(recipe, plans)
        return
    _render_table_preview(recipe, plans)


def preview_summary(plans: list[TargetPlan]) -> str:
    """Render the pre-run aggregate preview summary."""
    total = len(plans)
    failed = sum(1 for plan in plans if plan.status == "error")
    changing = sum(1 for plan in plans if plan.status != "error" and plan.changes)
    unchanged = sum(1 for plan in plans if plan.status != "error" and not plan.changes)
    files_changed = sum(plan.files_changed for plan in plans if plan.status != "error")
    return (
        "Recipe preview: "
        f"{_plural(total, 'target')}, "
        f"{changing} changing, "
        f"{unchanged} unchanged, "
        f"{failed} failed, "
        f"{_plural(files_changed, 'file')} changed"
    )


def _plural(count: int, noun: str) -> str:
    """Render a simple English count."""
    suffix = "" if count == 1 else "s"
    return f"{count} {noun}{suffix}"


def _render_diff_preview(recipe: Recipe, plans: list[TargetPlan]) -> None:
    diffable_plans, suppressed_rows, error_rows = _preview_groups(recipe, plans)
    for plan in diffable_plans:
        target = _display_target(plan)
        for change in plan.changes:
            diff = unified_diff(change)
            if diff:
                echo(f"# {target}", err=True)
                echo(diff, err=True, nl=False)
    _render_stderr_table(suppressed_rows, columns=["target", "files_changed"])
    _render_stderr_table(error_rows, columns=["target", "error"])


def _render_table_preview(recipe: Recipe, plans: list[TargetPlan]) -> None:
    diffable_plans, suppressed_rows, error_rows = _preview_groups(recipe, plans)
    normal_rows: list[dict[str, object]] = []
    for plan in diffable_plans:
        if not plan.changes:
            continue
        normal_rows.extend(
            {
                "path": str(_display_change_path(change)),
                "action": change.kind,
                "changes": _change_counts(change),
            }
            for change in plan.changes
        )
    _render_stderr_table(normal_rows, columns=["path", "action", "changes"])
    _render_stderr_table(suppressed_rows, columns=["target", "files_changed"])
    _render_stderr_table(error_rows, columns=["target", "error"])


def _preview_groups(
    recipe: Recipe,
    plans: list[TargetPlan],
) -> tuple[list[TargetPlan], list[dict[str, object]], list[dict[str, object]]]:
    diffable_plans: list[TargetPlan] = []
    suppressed_rows: list[dict[str, object]] = []
    error_rows: list[dict[str, object]] = []
    for plan in plans:
        if plan.status == "error":
            error_rows.append({"target": str(_display_target(plan)), "error": plan.error})
            continue
        if not plan.changes:
            continue
        if has_sensitive_inputs(recipe.inputs, plan.display_inputs):
            suppressed_rows.append(
                {
                    "target": str(_display_target(plan)),
                    "files_changed": plan.files_changed,
                }
            )
            continue
        diffable_plans.append(plan)
    return diffable_plans, suppressed_rows, error_rows


def _render_stderr_table(rows: list[dict[str, object]], *, columns: list[str]) -> None:
    if not rows:
        return
    base_ui = ui_context(stdout=sys.stderr, stderr=sys.stderr, strict=False)
    table_theme = base_ui.theme.model_copy(update={"collection_view": "table"})
    rendered = ui_context(
        theme=table_theme,
        stdout=sys.stderr,
        stderr=sys.stderr,
        strict=False,
    ).collection(
        rows,
        fmt="table",
        columns=columns,
    )
    if rendered:
        echo(rendered, err=True)


def _display_change_path(change: FileChange) -> Path:
    return _absolute_display_path(change.target / change.relative_path)


def _display_target(plan: TargetPlan) -> Path:
    return _absolute_display_path(plan.target)


def _absolute_display_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _change_counts(change: FileChange) -> str:
    before = [] if change.before is None else change.before.splitlines(keepends=True)
    after = [] if change.after is None else change.after.splitlines(keepends=True)
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for tag, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if tag == "replace":
            deletions += before_end - before_start
            additions += after_end - after_start
        elif tag == "delete":
            deletions += before_end - before_start
        elif tag == "insert":
            additions += after_end - after_start
    return f"+{additions} -{deletions}"
