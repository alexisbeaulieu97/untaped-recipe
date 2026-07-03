"""Tests for the hook-run application use case."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.application.ports import HookDebugResult
from untaped_recipe.application.run_hook import RunHook, TransformHookRun, select_verb
from untaped_recipe.domain.plan import Verdict


class _DebugExecutor:
    def __init__(self) -> None:
        self.transform_calls: list[dict[str, object]] = []

    def transform_for_debug(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> HookDebugResult[str]:
        self.transform_calls.append(
            {
                "hook": hook,
                "content": content,
                "local_hook_project": local_hook_project,
                "target": target,
                "file": file,
                "inputs": inputs,
                "args": args,
            }
        )
        return HookDebugResult(result=content + "!", diagnostics="diagnostic\n")

    def validate_for_debug(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> HookDebugResult[Verdict]:
        return HookDebugResult(result=Verdict(status="pass"), diagnostics="")


def test_run_hook_transform_reads_target_file_and_invokes_executor(tmp_path: Path) -> None:
    executor = _DebugExecutor()
    target = tmp_path / "target"
    target.mkdir()
    (target / "config.txt").write_text("before")

    result = RunHook(executor).run(
        "sample",
        kind="transform",
        local_hook_project=None,
        target=target,
        file=Path("config.txt"),
        content=None,
        content_file=None,
        inputs={"enabled": True},
        args={"count": 3},
    )

    assert isinstance(result, TransformHookRun)
    assert result.before == "before"
    assert result.content == "before!"
    assert result.diagnostics == "diagnostic\n"
    assert executor.transform_calls == [
        {
            "hook": "sample",
            "content": "before",
            "local_hook_project": None,
            "target": target.resolve(),
            "file": target.resolve() / "config.txt",
            "inputs": {"enabled": True},
            "args": {"count": 3},
        }
    ]


def test_run_hook_transform_requires_file(tmp_path: Path) -> None:
    executor = _DebugExecutor()
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ValueError, match="transform hooks require --file"):
        RunHook(executor).run(
            "sample",
            kind="transform",
            local_hook_project=None,
            target=target,
            file=None,
            content="before",
            content_file=None,
            inputs={},
            args={},
        )


def test_run_hook_validate_rejects_file_and_content_options(tmp_path: Path) -> None:
    executor = _DebugExecutor()
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ValueError, match="validate hooks do not accept --file or content options"):
        RunHook(executor).run(
            "sample",
            kind="validate",
            local_hook_project=None,
            target=target,
            file=Path("config.txt"),
            content=None,
            content_file=None,
            inputs={},
            args={},
        )


def test_run_hook_missing_content_file_is_clean_value_error(tmp_path: Path) -> None:
    executor = _DebugExecutor()
    target = tmp_path / "target"
    target.mkdir()

    with pytest.raises(ValueError, match="--content-file file not found"):
        RunHook(executor).run(
            "sample",
            kind="transform",
            local_hook_project=None,
            target=target,
            file=Path("config.txt"),
            content=None,
            content_file=tmp_path / "missing.txt",
            inputs={},
            args={},
        )


def test_select_verb_uses_single_export_without_kind() -> None:
    assert select_verb(frozenset({"transform"}), file_given=False, kind=None) == "transform"
    assert select_verb(frozenset({"validate"}), file_given=False, kind=None) == "validate"


def test_select_verb_uses_file_to_disambiguate_dual_export() -> None:
    assert select_verb(frozenset({"transform", "validate"}), file_given=True, kind=None) == (
        "transform"
    )


def test_select_verb_requires_kind_or_file_for_dual_export() -> None:
    with pytest.raises(ValueError, match="ambiguous hook verb"):
        select_verb(frozenset({"transform", "validate"}), file_given=False, kind=None)


def test_select_verb_uses_kind_to_disambiguate_dual_export() -> None:
    assert select_verb(frozenset({"transform", "validate"}), file_given=False, kind="validate") == (
        "validate"
    )
