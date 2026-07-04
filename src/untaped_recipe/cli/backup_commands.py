"""Backup library commands."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    FormatOption,
    batch_apply,
    create_app,
    echo,
    emit,
    finish,
    render_rows,
    ui_context,
)

from untaped_recipe.cli.common import library_root, report_config_errors
from untaped_recipe.infrastructure.backup import BackupStore, RestoreItem

app = create_app(name="backup", help="Manage recipe backups.")


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List backup bundles."""
    with report_config_errors():
        rows: list[dict[str, object]] = [
            {"id": bundle.id, "path": str(bundle.path)}
            for bundle in BackupStore(library_root() / "backups").list()
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.backup")
        if rendered:
            echo(rendered)


@app.command(name="show")
def show_command(
    backup_id: Annotated[str, Parameter(help="Backup id, prefix, or latest.")],
    /,
    *,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Show backup metadata."""
    with report_config_errors():
        metadata = BackupStore(library_root() / "backups").metadata(backup_id)
        emit(metadata, fmt=fmt, columns=columns, kind="recipe.backup")


@app.command(name="restore")
def restore_command(
    backup_id: Annotated[str, Parameter(help="Backup id, prefix, or latest.")],
    /,
    *,
    force: Annotated[
        bool,
        Parameter(name="--force", negative="", help="Overwrite files changed after backup."),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Restore a backup bundle."""
    with report_config_errors():
        store = BackupStore(library_root() / "backups")
        items = store.plan_restore(backup_id, force=force)
        ui = ui_context(strict=False)

        def _restore(item: RestoreItem) -> RestoreItem:
            store.restore(backup_id, items=[item], force=force)
            return item

        outcome = batch_apply(
            items,
            _restore,
            verb="restore",
            noun="file",
            label=lambda item: str(item.path),
            describe=lambda item: {"path": str(item.path), "action": item.action},
            ui=ui,
            destructive=True,
            assume_yes=yes,
        )
        if not outcome.any_failed:
            ui.message("success", f"restored {backup_id}")
        finish(outcome)
