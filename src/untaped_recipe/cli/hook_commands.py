"""Hook library commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Literal

import yaml
from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    batch_apply,
    create_app,
    echo,
    emit,
    parse_kv_pairs,
    render_rows,
    ui_context,
)

from untaped_recipe.cli.common import edit_path, library_root, report_config_errors, settings
from untaped_recipe.domain.hook_project import HookKind, read_hook_metadata
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.plan import FileChange, Verdict
from untaped_recipe.infrastructure.diff import unified_diff
from untaped_recipe.infrastructure.hook_executor import HookExecutionError, HookExecutor
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_library import HookLibrary
from untaped_recipe.infrastructure.hook_resolver import HookResolver
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool

app = create_app(name="hook", help="Manage reusable hooks.")
HookRunFormat = Literal["json", "yaml", "table", "pipe"]


@app.command(name="list")
def list_command(*, fmt: FormatOption = "table", columns: ColumnsOption = None) -> None:
    """List hooks."""
    with report_config_errors():
        rows: list[dict[str, object]] = [
            {"name": entry.name, "hooks": ", ".join(entry.hooks), "path": str(entry.path)}
            for entry in HookLibrary(library_root()).list()
        ]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.hook")
        if rendered:
            echo(rendered)


@app.command(name="init")
def init_command(
    name: Annotated[str, Parameter(help="Global hook name.")],
    /,
    *,
    kind: Annotated[
        Literal["transform", "validate"],
        Parameter(name="--kind", help="Hook callable kind."),
    ] = "transform",
) -> None:
    """Scaffold a uv hook project."""
    with report_config_errors():
        path = HookLibrary(library_root()).init(name, kind=kind)
        echo(str(path))


@app.command(name="run")
def run_command(
    name: Annotated[str, Parameter(help="Hook name.")],
    /,
    *,
    target: Annotated[Path, Parameter(name="--target", help="Target directory.")],
    project: Annotated[
        Path | None,
        Parameter(name="--project", help="Hook project to search before global hooks."),
    ] = None,
    file: Annotated[
        Path | None,
        Parameter(name="--file", help="Target-relative file path for transform hooks."),
    ] = None,
    content: Annotated[
        str | None,
        Parameter(
            name="--content",
            help="Transform fixture content, or '-' for stdin.",
            allow_leading_hyphen=True,
        ),
    ] = None,
    content_file: Annotated[
        Path | None,
        Parameter(name="--content-file", help="Read transform fixture content from a file."),
    ] = None,
    inputs_file: Annotated[
        Path | None,
        Parameter(name="--inputs", help="YAML mapping of hook inputs."),
    ] = None,
    args_file: Annotated[
        Path | None,
        Parameter(name="--args", help="YAML mapping of hook args."),
    ] = None,
    raw_inputs: Annotated[
        list[str] | None,
        Parameter(name="--input", help="Input override as key=YAML.", consume_multiple=False),
    ] = None,
    raw_args: Annotated[
        list[str] | None,
        Parameter(name="--arg", help="Arg override as key=YAML.", consume_multiple=False),
    ] = None,
    diff: Annotated[
        bool,
        Parameter(name="--diff", negative="", help="Emit a unified diff for transform hooks."),
    ] = False,
    hook_timeout: Annotated[
        float | None,
        Parameter(name="--hook-timeout", help="Per-hook timeout in seconds; 0 disables."),
    ] = None,
    fmt: Annotated[
        HookRunFormat | None,
        Parameter(name=("--format", "-f"), help="Structured output format."),
    ] = None,
    columns: ColumnsOption = None,
) -> None:
    """Run one hook once against explicit fixture context without writing files."""
    with report_config_errors():
        root = library_root()
        target = _target_dir(target)
        local_hook_project = _local_hook_project(project)
        resolver = HookResolver(global_hooks=root / "hooks")
        ref = resolver.resolve(name, local_hook_project)
        _validate_context_options(
            ref.kind,
            file=file,
            content=content,
            content_file=content_file,
            diff=diff,
        )
        inputs = _fixture_mapping(
            inputs_file,
            raw_inputs or [],
            file_flag="--inputs",
            kv_flag="--input",
        )
        args = _fixture_mapping(args_file, raw_args or [], file_flag="--args", kv_flag="--arg")
        _render_hook_run_context(
            name,
            kind=ref.kind,
            target=target,
            file=file,
            inputs=inputs,
            args=args,
        )
        with UvHookWorkerPool(hook_timeout_seconds=_hook_timeout_seconds(hook_timeout)) as workers:
            executor = HookExecutor(
                resolver,
                workers=workers,
                helpers=HookHelpers(),
            )
            if ref.kind == "transform":
                _run_transform(
                    executor,
                    name,
                    local_hook_project=local_hook_project,
                    target=target,
                    file=file,
                    content=content,
                    content_file=content_file,
                    inputs=inputs,
                    args=args,
                    diff=diff,
                    fmt=fmt,
                    columns=columns,
                )
            else:
                _run_validate(
                    executor,
                    name,
                    local_hook_project=local_hook_project,
                    target=target,
                    file=file,
                    content=content,
                    content_file=content_file,
                    inputs=inputs,
                    args=args,
                    diff=diff,
                    fmt=fmt,
                    columns=columns,
                )


@app.command(name="show")
def show_command(name: Annotated[str, Parameter(help="Hook name or path.")], /) -> None:
    """Print a hook project file."""
    with report_config_errors():
        echo(HookLibrary(library_root()).resolve_editable(name).read_text(), nl=False)


@app.command(name="add")
def add_command(
    source: Annotated[Path, Parameter(help="Hook project directory.")],
    /,
    *,
    name: Annotated[str | None, Parameter(name="--name", help="Library name.")] = None,
) -> None:
    """Copy a hook project into the library."""
    with report_config_errors():
        path = HookLibrary(library_root()).add(source, name=name)
        echo(str(path))


@app.command(name="remove")
def remove_command(
    name: Annotated[str, Parameter(help="Hook name.")],
    /,
    *,
    yes: Annotated[
        bool,
        Parameter(name=["--yes", "-y"], negative="", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Remove a hook project from the library."""
    with report_config_errors():
        library = HookLibrary(library_root())

        def _remove(item: str) -> Path:
            return library.remove(item)

        outcome = batch_apply(
            [name],
            _remove,
            verb="remove",
            noun="hook",
            label=str,
            describe=lambda item: {"name": item},
            ui=ui_context(strict=False),
            destructive=True,
            assume_yes=yes,
        )
        if outcome.results:
            echo(str(outcome.results[0][1]))


@app.command(name="edit")
def edit_command(name: Annotated[str, Parameter(help="Hook name or path.")], /) -> None:
    """Open a hook project file in $VISUAL or $EDITOR."""
    with report_config_errors():
        edit_path(HookLibrary(library_root()).resolve_editable(name))


def _run_transform(
    executor: HookExecutor,
    hook: str,
    *,
    local_hook_project: Path | None,
    target: Path,
    file: Path | None,
    content: str | None,
    content_file: Path | None,
    inputs: dict[str, object],
    args: dict[str, object],
    diff: bool,
    fmt: HookRunFormat | None,
    columns: list[str] | None,
) -> None:
    if file is None:
        raise ConfigError("transform hooks require --file")
    before, resolved_file = _transform_content(
        target,
        file,
        content=content,
        content_file=content_file,
    )
    try:
        execution = executor.transform_for_debug(
            hook,
            before,
            local_hook_project=local_hook_project,
            target=target,
            file=resolved_file,
            inputs=inputs,
            args=args,
        )
    except HookExecutionError as exc:
        _print_hook_failure(str(exc))
        raise SystemExit(1) from exc
    _print_hook_diagnostics(execution.diagnostics)
    diff_text = (
        unified_diff(
            FileChange(
                target=target,
                relative_path=file,
                before=before,
                after=execution.result,
            )
        )
        if diff
        else None
    )
    if fmt is not None:
        record: dict[str, object] = {
            "hook": hook,
            "kind": "transform",
            "target": str(target),
            "file": str(file),
            "status": "ok",
            "content": execution.result,
        }
        if diff_text is not None:
            record["diff"] = diff_text
        emit(record, fmt=fmt, columns=columns, kind="recipe.hook_run")
        return
    echo(diff_text if diff_text is not None else execution.result, nl=False)


def _run_validate(
    executor: HookExecutor,
    hook: str,
    *,
    local_hook_project: Path | None,
    target: Path,
    file: Path | None,
    content: str | None,
    content_file: Path | None,
    inputs: dict[str, object],
    args: dict[str, object],
    diff: bool,
    fmt: HookRunFormat | None,
    columns: list[str] | None,
) -> None:
    try:
        execution = executor.validate_for_debug(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
        )
    except HookExecutionError as exc:
        _print_hook_failure(str(exc))
        raise SystemExit(1) from exc
    _print_hook_diagnostics(execution.diagnostics)
    record = _validate_record(hook, target=target, verdict=execution.result)
    emit(record, fmt=fmt or "table", columns=columns, kind="recipe.hook_run")
    if execution.result.failed:
        raise SystemExit(1)


def _target_dir(target: Path) -> Path:
    resolved = target.expanduser().resolve()
    if not resolved.is_dir():
        raise ConfigError(f"target is not a directory: {target}")
    return resolved


def _validate_context_options(
    kind: HookKind,
    *,
    file: Path | None,
    content: str | None,
    content_file: Path | None,
    diff: bool,
) -> None:
    if kind == "transform":
        if file is None:
            raise ConfigError("transform hooks require --file")
        return
    if file is not None or content is not None or content_file is not None or diff:
        raise ConfigError("validate hooks do not accept --file or content options")


def _local_hook_project(project: Path | None) -> Path | None:
    if project is not None:
        return project.expanduser().resolve()
    cwd = Path.cwd()
    if not (cwd / "pyproject.toml").is_file():
        return None
    metadata = read_hook_metadata(cwd)
    if not metadata.hooks:
        return None
    return cwd


def _transform_content(
    target: Path,
    file: Path,
    *,
    content: str | None,
    content_file: Path | None,
) -> tuple[str, Path]:
    if content is not None and content_file is not None:
        raise ConfigError("provide --content or --content-file, not both")
    resolved_file = confined_path(target, file, field="file")
    if content_file is not None:
        return content_file.expanduser().read_text(), resolved_file
    if content is not None:
        return (sys.stdin.read() if content == "-" else content), resolved_file
    if not resolved_file.exists():
        raise ConfigError(f"transform file not found: {file}")
    if not resolved_file.is_file():
        raise ConfigError(f"transform path is not a file: {file}")
    return resolved_file.read_text(), resolved_file


def _fixture_mapping(
    path: Path | None,
    raw_pairs: list[str],
    *,
    file_flag: str,
    kv_flag: str,
) -> dict[str, object]:
    values: dict[str, object] = {}
    if path is not None:
        values.update(_yaml_mapping_file(path, flag=file_flag))
    values.update(_yaml_kv_pairs(raw_pairs, flag=kv_flag))
    return values


def _yaml_mapping_file(path: Path, *, flag: str) -> dict[str, object]:
    try:
        loaded = yaml.safe_load(path.expanduser().read_text()) or {}
    except OSError as exc:
        raise ConfigError(f"{flag} file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{flag} file is invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{flag} file must contain a YAML mapping")
    return {str(key): value for key, value in loaded.items()}


def _yaml_kv_pairs(raw_pairs: list[str], *, flag: str) -> dict[str, object]:
    parsed = parse_kv_pairs(raw_pairs, flag=flag)
    return {key: yaml.safe_load(str(value)) for key, value in parsed.items()}


def _render_hook_run_context(
    hook: str,
    *,
    kind: HookKind,
    target: Path,
    file: Path | None,
    inputs: dict[str, object],
    args: dict[str, object],
) -> None:
    ui = ui_context(strict=False)
    ui.message("info", f"Hook run: {hook} ({kind})")
    ui.message("info", f"target: {target}")
    if file is not None:
        ui.message("info", f"file: {file}")
    ui.message("info", f"inputs: {_json_context(inputs)}")
    ui.message("info", f"args: {_json_context(args)}")


def _validate_record(hook: str, *, target: Path, verdict: Verdict) -> dict[str, object]:
    return {
        "hook": hook,
        "kind": "validate",
        "target": str(target),
        "status": verdict.status,
        "message": verdict.message,
    }


def _json_context(value: dict[str, object]) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _print_hook_diagnostics(diagnostics: str) -> None:
    if diagnostics:
        sys.stderr.write(diagnostics.rstrip() + "\n")


def _print_hook_failure(message: str) -> None:
    sys.stderr.write(message.rstrip() + "\n")


def _hook_timeout_seconds(override: float | None) -> float:
    timeout = settings().hook_timeout_seconds if override is None else override
    if timeout < 0:
        raise ConfigError("--hook-timeout must be greater than or equal to 0")
    return timeout
