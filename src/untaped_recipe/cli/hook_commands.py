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
    finish,
    parse_kv_pairs,
    render_rows,
    ui_context,
)

from untaped_recipe.application.run_hook import RunHook, TransformHookRun, ValidateHookRun
from untaped_recipe.cli.common import (
    edit_path,
    library_root,
    load_yaml_mapping_file,
    report_config_errors,
    settings,
)
from untaped_recipe.domain.hook_project import HookKind, read_hook_metadata
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
        local_hook_project = _local_hook_project(project)
        resolver = HookResolver(global_hooks=root / "hooks")
        ref = resolver.resolve(name, local_hook_project)
        kind = _default_run_kind(ref.exports)
        if kind == "validate" and diff:
            raise ConfigError("validate hooks do not accept --file or content options")
        RunHook.validate_context(
            kind=kind,
            target=target,
            file=file,
            content=content,
            content_file=content_file,
        )
        inputs = _fixture_mapping(
            inputs_file,
            raw_inputs or [],
            file_flag="--inputs",
            kv_flag="--input",
        )
        args = _fixture_mapping(args_file, raw_args or [], file_flag="--args", kv_flag="--arg")
        prepared_content = _content_value(content)
        with UvHookWorkerPool(hook_timeout_seconds=_hook_timeout_seconds(hook_timeout)) as workers:
            executor = HookExecutor(
                resolver,
                workers=workers,
                helpers=HookHelpers(),
            )
            try:
                execution = RunHook(executor).run(
                    name,
                    kind=kind,
                    local_hook_project=local_hook_project,
                    target=target,
                    file=file,
                    content=prepared_content,
                    content_file=content_file,
                    inputs=inputs,
                    args=args,
                )
            except HookExecutionError as exc:
                _print_hook_failure(str(exc))
                raise SystemExit(1) from exc
            if isinstance(execution, TransformHookRun):
                _render_hook_run_context(
                    execution.hook,
                    kind=execution.kind,
                    target=execution.target,
                    file=execution.relative_file,
                    inputs=inputs,
                    args=args,
                )
                _run_transform(
                    execution,
                    diff=diff,
                    fmt=fmt,
                    columns=columns,
                )
            else:
                _render_hook_run_context(
                    execution.hook,
                    kind=execution.kind,
                    target=execution.target,
                    file=None,
                    inputs=inputs,
                    args=args,
                )
                _run_validate(
                    execution,
                    fmt=fmt,
                    columns=columns,
                )


@app.command(name="show")
def show_command(name: Annotated[str, Parameter(help="Hook name or path.")], /) -> None:
    """Print a hook project file."""
    with report_config_errors():
        echo(
            HookLibrary(library_root()).resolve_editable(name).read_text(encoding="utf-8"),
            nl=False,
        )


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
    execution: TransformHookRun,
    *,
    diff: bool,
    fmt: HookRunFormat | None,
    columns: list[str] | None,
) -> None:
    _print_hook_diagnostics(execution.diagnostics)
    diff_text = (
        unified_diff(
            FileChange(
                target=execution.target,
                relative_path=execution.relative_file,
                before=execution.before,
                after=execution.content,
            )
        )
        if diff
        else None
    )
    if fmt is not None:
        record: dict[str, object] = {
            "hook": execution.hook,
            "kind": "transform",
            "target": str(execution.target),
            "file": str(execution.relative_file),
            "status": "ok",
            "content": execution.content,
        }
        if diff_text is not None:
            record["diff"] = diff_text
        emit(record, fmt=fmt, columns=columns, kind="recipe.hook_run")
        return
    echo(diff_text if diff_text is not None else execution.content, nl=False)


def _run_validate(
    execution: ValidateHookRun,
    *,
    fmt: HookRunFormat | None,
    columns: list[str] | None,
) -> None:
    _print_hook_diagnostics(execution.diagnostics)
    record = _validate_record(execution.hook, target=execution.target, verdict=execution.verdict)
    emit(record, fmt=fmt or "table", columns=columns, kind="recipe.hook_run")
    finish(execution.verdict.failed)


def _local_hook_project(project: Path | None) -> Path | None:
    if project is not None:
        resolved = project.expanduser().resolve()
        if not resolved.is_dir():
            raise ConfigError(f"hook project not found: {project}")
        if not (resolved / "pyproject.toml").is_file():
            raise ConfigError(f"hook project has no pyproject.toml: {project}")
        metadata = read_hook_metadata(resolved)
        if not metadata.hooks:
            raise ConfigError(f"hook project has no hook metadata: {project}")
        return resolved
    cwd = Path.cwd()
    if not (cwd / "pyproject.toml").is_file():
        return None
    metadata = read_hook_metadata(cwd)
    if not metadata.hooks:
        return None
    return cwd


def _default_run_kind(exports: frozenset[str]) -> HookKind:
    if exports == frozenset({"validate"}):
        return "validate"
    if "transform" in exports:
        return "transform"
    if "validate" in exports:
        return "validate"
    raise ValueError("hook exports neither transform() nor validate()")


def _fixture_mapping(
    path: Path | None,
    raw_pairs: list[str],
    *,
    file_flag: str,
    kv_flag: str,
) -> dict[str, object]:
    values: dict[str, object] = {}
    if path is not None:
        values.update(load_yaml_mapping_file(path, flag=file_flag))
    values.update(_yaml_kv_pairs(raw_pairs, flag=kv_flag))
    return values


def _content_value(content: str | None) -> str | None:
    if content == "-":
        return sys.stdin.read()
    return content


def _yaml_kv_pairs(raw_pairs: list[str], *, flag: str) -> dict[str, object]:
    parsed = parse_kv_pairs(raw_pairs, flag=flag)
    values: dict[str, object] = {}
    for key, value in parsed.items():
        try:
            values[key] = yaml.safe_load(str(value))
        except yaml.YAMLError as exc:
            raise ConfigError(f"{flag} value for {key!r} is invalid YAML: {exc}") from exc
    return values


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
