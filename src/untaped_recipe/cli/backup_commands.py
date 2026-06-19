"""Backup library commands."""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    FormatOption,
    create_app,
    echo,
    emit,
    render_rows,
    report_errors,
)

from untaped_recipe.cli.common import library_root
from untaped_recipe.infrastructure.backup import BackupStore

app = create_app(name="backup", help="Manage recipe backups.")


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List backup bundles."""
    with report_errors():
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
    with report_errors():
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
) -> None:
    """Restore a backup bundle."""
    with report_errors():
        BackupStore(library_root() / "backups").restore(backup_id, force=force)
        echo(f"restored {backup_id}", err=True)
