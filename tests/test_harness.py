"""Tests for golden-fixture harness discovery and specs."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.application.harness import (
    CaseResult,
    DiscoveredCase,
    RecordingHookExecutor,
    discover_cases,
    load_case_spec,
    orphaned_test_dirs,
    run_case,
    update_case,
)
from untaped_recipe.application.ports import HookDebugResult
from untaped_recipe.domain.pack import PackManifest
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.pack_store import InstalledPack


class _FakeExecutor:
    """In-process HookExecutorPort: uppercases content, replays queued verdicts."""

    def __init__(self, verdicts: tuple[Verdict, ...] = ()) -> None:
        self._verdicts = list(verdicts)

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
        return HookDebugResult(result=content.upper(), diagnostics="")

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
        verdict = self._verdicts.pop(0) if self._verdicts else Verdict(status="pass")
        return HookDebugResult(result=verdict, diagnostics="")


_COPY_RECIPE = (
    "version: 1\n"
    "steps:\n"
    "  - type: copy\n"
    "    source: assets/payload.txt\n"
    "    dest: out.txt\n"
)

_TRANSFORM_RECIPE = (
    "version: 1\n"
    "steps:\n"
    "  - type: transform\n"
    "    file: note.txt\n"
    "    hook: shout\n"
)

_VALIDATE_RECIPE = "version: 1\nsteps:\n  - type: validate\n    hook: probe\n"


def _write_pack(
    root: Path,
    *,
    recipes: dict[str, str],
    recipe_bodies: dict[str, str] | None = None,
) -> InstalledPack:
    """Write a minimal pack project and wrap it as a local InstalledPack."""
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, relative in recipes.items():
        recipe_path = root / relative
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        body = (recipe_bodies or {}).get(name, "version: 1\nsteps: []\n")
        recipe_path.write_text(body, encoding="utf-8")
        rows.append(f'"{name}" = {{ path = "{relative}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return InstalledPack.local(root, PackManifest.from_pyproject(root))


def _write_case(pack_root: Path, recipe: str, case: str, *, case_yml: str | None = None) -> Path:
    case_dir = pack_root / "tests" / recipe / case
    (case_dir / "given").mkdir(parents=True)
    if case_yml is not None:
        (case_dir / "case.yml").write_text(case_yml, encoding="utf-8")
    return case_dir


def _copy_pack(tmp_path: Path) -> InstalledPack:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"emit": "recipes/emit/recipe.yml"},
        recipe_bodies={"emit": _COPY_RECIPE},
    )
    asset = pack.root / "recipes" / "emit" / "assets" / "payload.txt"
    asset.parent.mkdir(parents=True)
    asset.write_text("payload\n", encoding="utf-8")
    return pack


def _write_target_name_pack(root: Path) -> InstalledPack:
    pack = _write_pack(
        root,
        recipes={"stamp": "recipes/stamp/recipe.yml"},
        recipe_bodies={
            "stamp": (
                "version: 1\n"
                "inputs:\n"
                "  case_name:\n"
                '    from: "{{ target.name }}"\n'
                "steps:\n"
                "  - type: template\n"
                "    template: templates/name.txt.j2\n"
                "    dest: name.txt\n"
            )
        },
    )
    template = pack.root / "recipes" / "stamp" / "templates" / "name.txt.j2"
    template.parent.mkdir(parents=True)
    template.write_text("{{ case_name }}\n", encoding="utf-8")
    return pack


def _case(pack: InstalledPack, recipe: str, case: str) -> DiscoveredCase:
    return next(
        found for found in discover_cases(pack, recipe=recipe) if found.case_name == case
    )


def test_discover_cases_lists_cases_per_manifest_recipe_sorted(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"},
    )
    _write_case(pack.root, "two", "beta")
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "one", "gamma")

    cases = discover_cases(pack)

    assert [(case.recipe_name, case.case_name) for case in cases] == [
        ("one", "alpha"),
        ("one", "gamma"),
        ("two", "beta"),
    ]
    assert cases[0].pack_name == "demo"
    assert cases[0].recipe_path == pack.root / "recipes/one.yml"
    assert cases[0].case_dir == pack.root / "tests" / "one" / "alpha"


def test_discover_cases_scopes_to_one_recipe_and_ignores_files_and_hidden_dirs(
    tmp_path: Path,
) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"},
    )
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "two", "beta")
    (pack.root / "tests" / "one" / "README.md").write_text("notes\n", encoding="utf-8")
    (pack.root / "tests" / "one" / ".hidden").mkdir()

    cases = discover_cases(pack, recipe="one")

    assert [(case.recipe_name, case.case_name) for case in cases] == [("one", "alpha")]


def test_orphaned_test_dirs_flags_dirs_naming_no_recipe(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml"})
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "renamed", "old")
    (pack.root / "tests" / ".cache").mkdir()

    assert orphaned_test_dirs(pack) == ["renamed"]


def test_orphaned_test_dirs_is_empty_without_tests_dir(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml"})

    assert orphaned_test_dirs(pack) == []


def test_load_case_spec_defaults_when_file_missing(tmp_path: Path) -> None:
    assert load_case_spec(tmp_path).expect == "success"


def test_load_case_spec_rejects_non_mapping_and_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "case.yml").write_text("- not\n- a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"case\.yml must contain a YAML mapping"):
        load_case_spec(tmp_path)

    (tmp_path / "case.yml").write_text("expect: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid case\.yml"):
        load_case_spec(tmp_path)


def test_load_case_spec_rejects_unknown_fields(tmp_path: Path) -> None:
    (tmp_path / "case.yml").write_text("targets: [a.yml]\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid case\.yml"):
        load_case_spec(tmp_path)


def test_run_case_passes_when_result_tree_matches_expected(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "given" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (case_dir / "expected" / "out.txt").write_text("payload\n", encoding="utf-8")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result == CaseResult(pack="demo", recipe="emit", case="basic", status="pass")


def test_run_case_fails_on_full_tree_mismatch_with_diffs(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "out.txt").write_text("different\n", encoding="utf-8")
    (case_dir / "expected" / "extra.txt").write_text("only-expected\n", encoding="utf-8")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "files differ: extra.txt, out.txt"
    assert {change.relative_path.as_posix() for change in result.diffs} == {
        "extra.txt",
        "out.txt",
    }
    extra = next(c for c in result.diffs if c.relative_path.as_posix() == "extra.txt")
    assert extra.before == "only-expected\n"
    assert extra.after is None


def test_run_case_omitted_expected_asserts_no_changes(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(pack.root, "emit", "basic")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "expected no changes; planned changes to: out.txt"


def test_run_case_expect_error_matches_message(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"shout": "recipes/shout.yml"},
        recipe_bodies={"shout": _TRANSFORM_RECIPE},
    )
    _write_case(
        pack.root,
        "shout",
        "missing-file",
        case_yml='expect: error\nerror_contains: "transform file not found"\n',
    )

    result = run_case(_case(pack, "shout", "missing-file"), executor=_FakeExecutor())

    assert result.status == "pass"


def test_run_case_expect_error_fails_on_unexpected_success(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(
        pack.root,
        "emit",
        "basic",
        case_yml='expect: error\nerror_contains: "boom"\n',
    )

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "expected planning to fail; it succeeded"


def test_run_case_expected_dir_forbidden_for_error_cases(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(
        pack.root,
        "emit",
        "basic",
        case_yml='expect: error\nerror_contains: "boom"\n',
    )
    (case_dir / "expected").mkdir()

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "error"
    assert result.detail == "expected/ is forbidden for expect: error cases"


def test_run_case_config_error_is_a_per_case_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(pack.root, "emit", "basic", case_yml='inputs:\n  bogus: "x"\n')

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "error"
    assert "unknown input" in result.detail


def test_run_case_temp_target_is_named_after_the_case(tmp_path: Path) -> None:
    pack = _write_target_name_pack(tmp_path / "demo")
    case_dir = _write_case(pack.root, "stamp", "my-case")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "name.txt").write_text("my-case\n", encoding="utf-8")

    result = run_case(_case(pack, "stamp", "my-case"), executor=_FakeExecutor())

    assert result.status == "pass"


def test_run_case_verdict_worst_of_and_message(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"lint": "recipes/lint.yml"},
        recipe_bodies={"lint": _VALIDATE_RECIPE},
    )
    _write_case(
        pack.root,
        "lint",
        "warns",
        case_yml="verdict:\n  status: warn\n  message_contains: tabs\n",
    )
    executor = _FakeExecutor(verdicts=(Verdict(status="warn", message="uses tabs"),))

    result = run_case(_case(pack, "lint", "warns"), executor=executor)

    assert result.status == "pass"


def test_run_case_verdict_mismatch_fails(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"lint": "recipes/lint.yml"},
        recipe_bodies={"lint": _VALIDATE_RECIPE},
    )
    _write_case(pack.root, "lint", "warns", case_yml="verdict:\n  status: warn\n")

    result = run_case(_case(pack, "lint", "warns"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "expected worst verdict status warn, got pass"


def test_run_case_verdict_with_no_verdicts_fails(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"noop": "recipes/noop.yml"},
        recipe_bodies={"noop": "version: 1\nsteps: []\n"},
    )
    _write_case(pack.root, "noop", "basic", case_yml="verdict:\n  status: pass\n")

    result = run_case(_case(pack, "noop", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "no verdicts produced"


def test_run_case_missing_given_is_an_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack.root / "tests" / "emit" / "broken").mkdir(parents=True)

    result = run_case(_case(pack, "emit", "broken"), executor=_FakeExecutor())

    assert result.status == "error"
    assert result.detail == "case is missing given/"


def test_run_case_never_mutates_fixtures(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "given" / "keep.txt").write_text("keep\n", encoding="utf-8")

    run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert sorted(p.name for p in (case_dir / "given").iterdir()) == ["keep.txt"]


def test_recording_executor_records_validate_verdicts_only(tmp_path: Path) -> None:
    recorder = RecordingHookExecutor(_FakeExecutor(verdicts=(Verdict(status="warn"),)))

    recorder.transform(
        "shout",
        "hi",
        local_hook_project=None,
        target=tmp_path,
        file=tmp_path / "f",
        inputs={},
        args={},
    )
    recorder.validate("probe", local_hook_project=None, target=tmp_path, inputs={}, args={})

    assert [verdict.status for verdict in recorder.verdicts] == ["warn"]


def test_update_case_writes_expected_tree_from_plan(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "given" / "keep.txt").write_text("keep\n", encoding="utf-8")

    result = update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "updated"
    assert (case_dir / "expected" / "out.txt").read_text(encoding="utf-8") == "payload\n"
    assert (case_dir / "expected" / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor()).status == "pass"


def test_update_case_reports_pass_when_golden_already_matches(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())
    mtime = (case_dir / "expected" / "out.txt").stat().st_mtime_ns

    result = update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "pass"
    assert (case_dir / "expected" / "out.txt").stat().st_mtime_ns == mtime


def test_update_case_deletes_expected_when_plan_is_empty(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"noop": "recipes/noop.yml"})
    case_dir = _write_case(pack.root, "noop", "basic")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "stale.txt").write_text("stale\n", encoding="utf-8")

    result = update_case(_case(pack, "noop", "basic"), executor=_FakeExecutor())

    assert result.status == "updated"
    assert not (case_dir / "expected").exists()


def test_update_case_rejects_error_cases(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(
        pack.root,
        "emit",
        "basic",
        case_yml='expect: error\nerror_contains: "boom"\n',
    )

    result = update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "error"
    assert result.detail == "cannot --update an expect: error case"
