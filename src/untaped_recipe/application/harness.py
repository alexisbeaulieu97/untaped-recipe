"""Golden-fixture test harness for recipe packs."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import ValidationError
from untaped.api import ConfigError

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.ports import HookDebugResult, HookExecutorPort
from untaped_recipe.application.run_bulk import RunBulkApply
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.plan import FileChange, Verdict
from untaped_recipe.domain.testcase import CaseSpec, VerdictExpectation
from untaped_recipe.infrastructure.pack_store import InstalledPack
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

CaseStatus = Literal["pass", "fail", "error", "updated"]

_VERDICT_RANK = {"pass": 0, "warn": 1, "fail": 2}


class FixtureDecodeError(ValueError):
    """A fixture file is not valid UTF-8 text (the harness compares text trees)."""


@dataclass(frozen=True)
class DiscoveredCase:
    """One golden case directory resolved against a pack manifest."""

    pack_name: str
    pack_root: Path
    recipe_name: str
    recipe_path: Path
    case_name: str
    case_dir: Path


@dataclass(frozen=True)
class CaseResult:
    """Outcome of running (or updating) one golden case."""

    pack: str
    recipe: str
    case: str
    status: CaseStatus
    detail: str = ""
    diffs: tuple[FileChange, ...] = ()


class RecordingHookExecutor:
    """HookExecutorPort decorator that records every validate verdict."""

    def __init__(self, inner: HookExecutorPort) -> None:
        self._inner = inner
        self.verdicts: list[Verdict] = []

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        return self._inner.transform(
            hook,
            content,
            local_hook_project=local_hook_project,
            target=target,
            file=file,
            inputs=inputs,
            args=args,
            capture_diagnostics=capture_diagnostics,
        )

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        execution = self._inner.validate(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
            capture_diagnostics=capture_diagnostics,
        )
        self.verdicts.append(execution.result)
        return execution


@dataclass(frozen=True)
class _Trees:
    """Fixture tree before planning and materialized tree after."""

    base: dict[str, str]
    result: dict[str, str]


def discover_cases(pack: InstalledPack, *, recipe: str | None = None) -> list[DiscoveredCase]:
    """List golden cases for one pack, optionally scoped to one recipe."""
    tests_dir = pack.root / "tests"
    names = [recipe] if recipe is not None else sorted(pack.manifest.recipes)
    cases: list[DiscoveredCase] = []
    for name in names:
        entry = pack.manifest.recipes.get(name)
        if entry is None:
            continue
        recipe_tests = tests_dir / name
        if not recipe_tests.is_dir():
            continue
        for case_dir in sorted(recipe_tests.iterdir(), key=lambda path: path.name):
            if not case_dir.is_dir() or case_dir.name.startswith("."):
                continue
            cases.append(
                DiscoveredCase(
                    pack_name=pack.name,
                    pack_root=pack.root,
                    recipe_name=name,
                    recipe_path=pack.root / entry.path,
                    case_name=case_dir.name,
                    case_dir=case_dir,
                )
            )
    return cases


def orphaned_test_dirs(pack: InstalledPack) -> list[str]:
    """Return tests/ subdirectories that name no recipe in the manifest."""
    tests_dir = pack.root / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in tests_dir.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in pack.manifest.recipes
    )


def load_case_spec(case_dir: Path) -> CaseSpec:
    """Parse an optional case.yml; absent file means all defaults."""
    case_file = case_dir / "case.yml"
    if not case_file.is_file():
        return CaseSpec()
    try:
        loaded = yaml.safe_load(case_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid case.yml: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("case.yml must contain a YAML mapping")
    try:
        return CaseSpec.model_validate(loaded)
    except ValidationError as exc:
        raise ValueError(f"invalid case.yml: {exc}") from exc


def run_case(case: DiscoveredCase, *, executor: HookExecutorPort) -> CaseResult:
    """Run one golden case; fixtures and the pack are never written."""
    given = case.case_dir / "given"
    if not given.is_dir():
        return _result(case, "error", "case is missing given/")
    try:
        spec = load_case_spec(case.case_dir)
    except ValueError as exc:
        return _result(case, "error", str(exc))
    expected_dir = case.case_dir / "expected"
    if spec.expect == "error" and expected_dir.exists():
        return _result(case, "error", "expected/ is forbidden for expect: error cases")

    recorder = RecordingHookExecutor(executor)
    trees, error = _plan_case(case, spec, given, recorder)

    if spec.expect == "error":
        return _error_case_result(case, spec, error)

    if error is not None:
        return _result(case, "error", error)
    assert trees is not None
    verdict_problem = (
        _verdict_problem(spec.verdict, recorder.verdicts) if spec.verdict is not None else ""
    )
    try:
        return _success_case_result(case, expected_dir, trees, verdict_problem)
    except FixtureDecodeError as exc:
        return _result(case, "error", str(exc))


def update_case(case: DiscoveredCase, *, executor: HookExecutorPort) -> CaseResult:
    """Regenerate expected/ from the current plan; report what changed."""
    given = case.case_dir / "given"
    if not given.is_dir():
        return _result(case, "error", "case is missing given/")
    try:
        spec = load_case_spec(case.case_dir)
    except ValueError as exc:
        return _result(case, "error", str(exc))
    if spec.expect == "error":
        return _result(case, "error", "cannot --update an expect: error case")

    trees, error = _plan_case(case, spec, given, RecordingHookExecutor(executor))
    if error is not None:
        return _result(case, "error", error)
    assert trees is not None
    expected_dir = case.case_dir / "expected"
    if trees.result == trees.base:
        if expected_dir.is_dir():
            shutil.rmtree(expected_dir)
            return _result(case, "updated")
        return _result(case, "pass")
    if expected_dir.is_dir():
        try:
            existing = _read_tree(expected_dir)
        except FixtureDecodeError as exc:
            return _result(case, "error", str(exc))
        if existing == trees.result:
            return _result(case, "pass")
        shutil.rmtree(expected_dir)
    for key, content in trees.result.items():
        path = expected_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
    return _result(case, "updated")


def _error_case_result(
    case: DiscoveredCase,
    spec: CaseSpec,
    error: str | None,
) -> CaseResult:
    needle = spec.error_contains or ""
    if error is None:
        return _result(case, "fail", "expected planning to fail; it succeeded")
    if needle not in error:
        return _result(case, "fail", f"planning failed with different message: {error}")
    return _result(case, "pass")


def _success_case_result(
    case: DiscoveredCase,
    expected_dir: Path,
    trees: _Trees,
    verdict_problem: str,
) -> CaseResult:
    if expected_dir.is_dir():
        mismatches = _tree_mismatches(_read_tree(expected_dir), trees.result, case=case)
        if mismatches:
            return _result(
                case, "fail", _mismatch_detail("files differ", mismatches), diffs=mismatches
            )
    else:
        mismatches = _tree_mismatches(trees.base, trees.result, case=case)
        if mismatches:
            return _result(
                case,
                "fail",
                _mismatch_detail("expected no changes; planned changes to", mismatches),
                diffs=mismatches,
            )
    if verdict_problem:
        return _result(case, "fail", verdict_problem)
    return _result(case, "pass")


def _plan_case(
    case: DiscoveredCase,
    spec: CaseSpec,
    given: Path,
    recorder: RecordingHookExecutor,
) -> tuple[_Trees | None, str | None]:
    """Plan against a temp copy of given/ and return (trees, error)."""
    try:
        recipe = load_recipe_file(case.recipe_path)
    except (ConfigError, ValueError) as exc:
        return None, str(exc)
    with tempfile.TemporaryDirectory() as temp_root:
        target_dir = Path(temp_root) / case.case_name
        shutil.copytree(given, target_dir)
        runner = RunBulkApply(ApplyRecipe(recorder))
        try:
            base = _read_tree(target_dir)
            plans = runner.plan(
                recipe=recipe,
                recipe_dir=case.recipe_path.parent,
                local_hook_project=case.pack_root,
                targets=[Target(path=target_dir)],
                inputs=dict(spec.inputs),
            )
        except (ConfigError, ValueError) as exc:
            return None, str(exc)
        plan = plans[0]
        if plan.status == "error":
            return None, plan.error
        return _Trees(base=base, result=_materialize(base, plan.changes)), None


def _read_tree(root: Path) -> dict[str, str]:
    tree: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            tree[relative] = path.read_text(encoding="utf-8", newline="")
        except UnicodeDecodeError as exc:
            raise FixtureDecodeError(f"non-UTF-8 fixture file: {relative}") from exc
    return tree


def _materialize(base: dict[str, str], changes: Iterable[FileChange]) -> dict[str, str]:
    tree = dict(base)
    for change in changes:
        key = change.relative_path.as_posix()
        if change.after is None:
            tree.pop(key, None)
        else:
            tree[key] = change.after
    return tree


def _tree_mismatches(
    expected: dict[str, str],
    actual: dict[str, str],
    *,
    case: DiscoveredCase,
) -> tuple[FileChange, ...]:
    changes: list[FileChange] = []
    for key in sorted(expected.keys() | actual.keys()):
        before = expected.get(key)
        after = actual.get(key)
        if before != after:
            changes.append(
                FileChange(
                    target=case.case_dir,
                    relative_path=Path(key),
                    before=before,
                    after=after,
                )
            )
    return tuple(changes)


def _mismatch_detail(prefix: str, mismatches: tuple[FileChange, ...]) -> str:
    names = [change.relative_path.as_posix() for change in mismatches]
    listed = ", ".join(names[:5]) + (", …" if len(names) > 5 else "")
    return f"{prefix}: {listed}"


def _verdict_problem(expectation: VerdictExpectation, verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "no verdicts produced"
    if expectation.status is not None:
        worst = max(verdicts, key=lambda verdict: _VERDICT_RANK[verdict.status]).status
        if worst != expectation.status:
            return f"expected worst verdict status {expectation.status}, got {worst}"
    if expectation.message_contains is not None and not any(
        expectation.message_contains in verdict.message for verdict in verdicts
    ):
        return f"no verdict message contains {expectation.message_contains!r}"
    return ""


def _result(
    case: DiscoveredCase,
    status: CaseStatus,
    detail: str = "",
    *,
    diffs: tuple[FileChange, ...] = (),
) -> CaseResult:
    return CaseResult(
        pack=case.pack_name,
        recipe=case.recipe_name,
        case=case.case_name,
        status=status,
        detail=detail,
        diffs=diffs,
    )
