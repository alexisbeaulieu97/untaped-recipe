"""CLI test command: run golden-fixture cases from installed or local packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    echo,
    finish,
    render_rows,
    ui_context,
)

from untaped_recipe.application.harness import (
    CaseResult,
    DiscoveredCase,
    discover_cases,
    orphaned_test_dirs,
    run_case,
    update_case,
)
from untaped_recipe.cli.common import (
    hook_startup_notice,
    hook_timeout_seconds,
    library_root,
    report_config_errors,
    settings,
)
from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.infrastructure import HookExecutor, HookResolver
from untaped_recipe.infrastructure.diff import unified_diff
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.pack_store import InstalledPack, PackLibrary

MessageKind = Literal["success", "warning", "error", "info"]


@dataclass(frozen=True)
class _Selection:
    """Cases to run plus non-case rows the selection already produced."""

    cases: list[DiscoveredCase] = field(default_factory=list)
    static_results: list[CaseResult] = field(default_factory=list)
    packs_without_tests: list[str] = field(default_factory=list)


def test_command(
    ref_text: Annotated[
        str | None,
        Parameter(help="Installed pack, recipe ref, or pack path."),
    ] = None,
    /,
    *,
    update: Annotated[
        bool,
        Parameter(
            name="--update",
            negative="",
            help="Regenerate expected/ trees from the current plan.",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Run golden-fixture test cases for packs."""
    with report_config_errors():
        if update and ref_text is None:
            raise ConfigError("--update requires an explicit pack or recipe argument")
        root = library_root()
        selection = _select(root, ref_text)
        results = _execute(root, selection, update=update)
        rows = [_row(result) for result in results]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.test") if rows else ""
        if rendered:
            echo(rendered)
        if not update:
            _render_diffs(results)
        _render_summary(selection, results, update=update)
        if update:
            finish(any(result.status == "error" for result in results))
        else:
            finish(any(result.status in {"fail", "error"} for result in results))


def _select(root: Path, ref_text: str | None) -> _Selection:
    library = PackLibrary(library_root=root)
    if ref_text is None:
        selection = _Selection()
        for pack in library.packs():
            if not (pack.root / "tests").is_dir():
                selection.packs_without_tests.append(pack.name)
                continue
            _extend_for_pack(selection, pack)
        return selection
    if ref_text.endswith((".yml", ".yaml")):
        raise ConfigError("test requires a pack directory or ref, not a recipe file")
    if _is_pack_path(ref_text):
        path = Path(ref_text).expanduser()
        pack = InstalledPack.local(path, PackManifest.from_pyproject(path))
        return _explicit_selection(pack, recipe=None)
    pack_match = library.find_pack(ref_text)
    if pack_match is not None:
        return _explicit_selection(pack_match, recipe=None)
    ref = parse_ref(ref_text)
    recipe_pack, _entry = library.find_recipe(ref)
    return _explicit_selection(recipe_pack, recipe=ref.name)


def _is_pack_path(value: str) -> bool:
    """Return whether value should be interpreted as a local pack directory."""
    return value in {".", ".."} or value.startswith(("/", "./", "../", "~"))


def _explicit_selection(pack: InstalledPack, *, recipe: str | None) -> _Selection:
    selection = _Selection()
    if recipe is None:
        _extend_for_pack(selection, pack)
    else:
        selection.cases.extend(discover_cases(pack, recipe=recipe))
    if not selection.cases and not selection.static_results:
        selection.static_results.append(
            CaseResult(
                pack=pack.name,
                recipe=recipe or "",
                case="",
                status="error",
                detail="no test cases found",
            )
        )
    return selection


def _extend_for_pack(selection: _Selection, pack: InstalledPack) -> None:
    selection.cases.extend(discover_cases(pack))
    selection.static_results.extend(
        CaseResult(
            pack=pack.name,
            recipe=name,
            case="",
            status="error",
            detail="tests directory names no known recipe",
        )
        for name in orphaned_test_dirs(pack)
    )


def _execute(root: Path, selection: _Selection, *, update: bool) -> list[CaseResult]:
    results = list(selection.static_results)
    if not selection.cases:
        return results
    runner = update_case if update else run_case
    ui = ui_context(strict=False)
    with UvHookWorkerPool(
        max_workers_per_project=1,
        hook_timeout_seconds=hook_timeout_seconds(None),
        startup_timeout_seconds=settings().hook_startup_timeout_seconds,
        startup_notice=hook_startup_notice(ui),
    ) as workers:
        executor = HookExecutor(
            HookResolver(library_root=root),
            workers=workers,
            helpers=HookHelpers(),
        )
        with ui.progress("Running test cases") as progress:
            total = len(selection.cases)
            for index, case in enumerate(selection.cases, start=1):
                results.append(runner(case, executor=executor))
                progress.update(f"{index}/{total}", fraction=index / total)
    return results


def _row(result: CaseResult) -> dict[str, object]:
    return {
        "pack": result.pack,
        "recipe": result.recipe,
        "case": result.case,
        "status": result.status,
        "detail": result.detail,
    }


def _render_diffs(results: list[CaseResult]) -> None:
    for result in results:
        if not result.diffs:
            continue
        echo(f"# {result.pack}/{result.recipe}/{result.case}", err=True)
        for change in result.diffs:
            diff = unified_diff(change)
            if diff:
                echo(diff, err=True, nl=False)


def _render_summary(selection: _Selection, results: list[CaseResult], *, update: bool) -> None:
    ui = ui_context(strict=False)
    errors = sum(1 for result in results if result.status == "error")
    if update:
        updated = sum(1 for result in results if result.status == "updated")
        unchanged = sum(1 for result in results if result.status == "pass")
        update_kind: MessageKind = "warning" if errors else "info"
        ui.message(
            update_kind,
            f"Recipe test update: {updated} updated, {unchanged} unchanged, {errors} errored",
        )
        return
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    summary_kind: MessageKind = "warning" if failed or errors else "info"
    ui.message(summary_kind, f"Recipe tests: {passed} passed, {failed} failed, {errors} errored")
    if selection.packs_without_tests:
        ui.message(
            "info",
            "packs without tests: " + ", ".join(selection.packs_without_tests),
        )
