"""Tests for planning recipes against target directories."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.ports import HookDebugResult
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.hook_worker import handle_request
from untaped_recipe.infrastructure.file_writer import flush_changes
from untaped_recipe.infrastructure.hook_executor import HookExecutor
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_resolver import HookResolver, UvHookRef
from untaped_recipe.infrastructure.hook_worker_client import HookWorkerCallResult


class InlineWorkers:
    """Execute worker requests in-process for planner unit tests."""

    def request(
        self,
        ref: UvHookRef,
        payload: dict[str, object],
        *,
        diagnostic_limit: int | None = 4000,
        settle_seconds: float = 0,
    ) -> HookWorkerCallResult:
        for name in tuple(sys.modules):
            if name == "recipe_hooks" or name.startswith("recipe_hooks."):
                del sys.modules[name]
        sys.path.insert(0, str(ref.project_root / "src"))
        try:
            response = handle_request({"id": "1", "module": ref.module, **payload})
        finally:
            sys.path.pop(0)
        if not response["ok"]:
            raise ValueError(str(response["error"]))
        return HookWorkerCallResult(result=response["result"], diagnostics="")


def _planner(tmp_path: Path):
    planner = ApplyRecipe(
        HookExecutor(
            HookResolver(global_hooks=tmp_path / "global"),
            workers=InlineWorkers(),
            helpers=HookHelpers(),
        )
    )

    def plan(
        *,
        recipe: Recipe,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
    ):
        return planner(
            recipe=recipe,
            recipe_dir=recipe_dir,
            local_hook_project=recipe_dir,
            target=target,
            inputs=inputs,
        )

    return plan


def _write_hook_project(recipe_dir: Path, hooks: dict[str, str]) -> None:
    package = "recipe_hooks"
    (recipe_dir / "src" / package / "hooks").mkdir(parents=True, exist_ok=True)
    (recipe_dir / "src" / package / "__init__.py").write_text("")
    (recipe_dir / "src" / package / "hooks" / "__init__.py").write_text("")
    hook_rows: list[str] = []
    for name, code in hooks.items():
        (recipe_dir / "src" / package / "hooks" / f"{name}.py").write_text(code)
        hook_rows.append(f'"{name}" = {{ module = "{package}.hooks.{name}" }}')
    (recipe_dir / "pyproject.toml").write_text(
        "[project]\n"
        'name = "recipe-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.hooks]\n" + "\n".join(hook_rows) + "\n"
    )
    (recipe_dir / "uv.lock").write_text("version = 1\n")


def test_apply_recipe_executor_calls_default_to_no_diagnostics(tmp_path: Path) -> None:
    class SpyExecutor:
        def __init__(self) -> None:
            self.transform_capture_flags: list[bool] = []
            self.validate_capture_flags: list[bool] = []

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
            self.transform_capture_flags.append(capture_diagnostics)
            return HookDebugResult(result=content + "!", diagnostics="ignored")

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
            self.validate_capture_flags.append(capture_diagnostics)
            return HookDebugResult(result=Verdict(status="pass"), diagnostics="ignored")

    spy = SpyExecutor()
    target = tmp_path / "target"
    target.mkdir()
    (target / "config.txt").write_text("before")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {"type": "validate", "hook": "check"},
                {"type": "transform", "file": "config.txt", "hook": "rewrite"},
            ],
        }
    )

    plan = ApplyRecipe(spy)(
        recipe=recipe,
        recipe_dir=tmp_path,
        local_hook_project=None,
        target=target,
        inputs={},
    )

    assert spy.validate_capture_flags == [False]
    assert spy.transform_capture_flags == [False]
    assert plan.changes[0].after == "before!"


def test_apply_recipe_plans_template_copy_remove_and_transform(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "templates").mkdir()
    (recipe_dir / "files").mkdir()
    (recipe_dir / "templates" / "config.yml").write_text("name: {{ service }}\n")
    (recipe_dir / "files" / "README.md").write_text("# Shared\n")
    _write_hook_project(
        recipe_dir,
        {
            "upper": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content.upper()\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "legacy.txt").write_text("delete me\n")
    (target / "name.txt").write_text("hello\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"service": {"type": "str", "required": True}},
            "steps": [
                {"type": "template", "template": "templates/config.yml", "dest": "config.yml"},
                {"type": "copy", "source": "files/README.md", "dest": "README.md"},
                {"type": "remove", "file": "legacy.txt"},
                {"type": "transform", "file": "name.txt", "hook": "upper"},
            ],
        }
    )

    plan = _planner(tmp_path)(
        recipe=recipe,
        recipe_dir=recipe_dir,
        target=target,
        inputs={"service": "api"},
    )

    assert plan.status == "planned"
    assert sorted(str(change.relative_path) for change in plan.changes) == [
        "README.md",
        "config.yml",
        "legacy.txt",
        "name.txt",
    ]
    assert (target / "legacy.txt").read_text() == "delete me\n"
    assert "name: api" in "\n".join(change.after or "" for change in plan.changes)


def test_apply_recipe_template_step_can_keep_non_bare_tokens(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "workflow.yml").write_text(
        "name: ci\non: push\nref: ${{ github.ref }}\nowner: {{ owner }}\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"owner": {"type": "str", "required": True}},
            "steps": [
                {
                    "type": "template",
                    "template": "workflow.yml",
                    "dest": ".github/workflows/ci.yml",
                    "unknown_tokens": "keep",
                }
            ],
        }
    )

    plan = _planner(tmp_path)(
        recipe=recipe,
        recipe_dir=recipe_dir,
        target=target,
        inputs={"owner": "platform"},
    )

    assert plan.status == "planned"
    assert plan.changes[0].after == (
        "name: ci\non: push\nref: ${{ github.ref }}\nowner: platform\n"
    )


def test_apply_recipe_failing_validate_aborts_target_without_changes(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "fail": (
                "def validate(*, inputs, target, args, helpers):\n"
                "    return helpers.fail('not ready')\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {"type": "validate", "hook": "fail"},
                {"type": "template", "template": "missing", "dest": "out.txt"},
            ],
        }
    )

    with pytest.raises(ValueError, match="not ready"):
        _planner(tmp_path)(
            recipe=recipe,
            recipe_dir=recipe_dir,
            target=target,
            inputs={},
        )

    assert list(target.iterdir()) == []


def test_apply_recipe_rejects_step_hook_kind_mismatch_before_worker_call(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "check": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "validate", "hook": "check"}],
        }
    )

    with pytest.raises(
        ValueError,
        match=r"validate step hook 'check' does not export a validate\(\) function",
    ):
        _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})


def test_optional_transform_skips_missing_disk_files_with_warning(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "mark": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content + file.name + '\\n'\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "transform",
                    "files": ["local.yml", "site.yml"],
                    "optional": True,
                    "hook": "mark",
                }
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert [str(change.relative_path) for change in plan.changes] == ["local.yml"]
    assert plan.changes[0].after == "---\nlocal.yml\n"
    assert plan.warnings == ("optional transform skipped missing file: site.yml",)


def test_crlf_file_survives_transform_round_trip(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "append": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content + 'line three\\r\\n'\n"
            )
        },
    )
    target = tmp_path / "repo"
    target.mkdir()
    original = "line one\r\nline two\r\n"
    expected = "line one\r\nline two\r\nline three\r\n"
    (target / "config.txt").write_bytes(original.encode("utf-8"))
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "transform",
                    "file": "config.txt",
                    "hook": "append",
                }
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})
    flush_changes(plan.changes)

    assert (target / "config.txt").read_bytes() == expected.encode("utf-8")


def test_optional_transform_still_errors_after_explicit_remove(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "noop": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {"type": "remove", "file": "local.yml"},
                {
                    "type": "transform",
                    "file": "local.yml",
                    "optional": True,
                    "hook": "noop",
                },
            ],
        }
    )

    with pytest.raises(ValueError, match=r"cannot transform deleted file: local\.yml"):
        _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})


def test_optional_transform_errors_when_target_path_is_not_a_file(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "noop": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "transform",
                    "file": "local.yml",
                    "optional": True,
                    "hook": "noop",
                },
            ],
        }
    )

    with pytest.raises(ValueError, match=r"transform path is not a file: local\.yml"):
        _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})


def test_optional_transform_uses_content_created_earlier_in_plan(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.yml").write_text("created\n")
    _write_hook_project(
        recipe_dir,
        {
            "suffix": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content + 'transformed\\n'\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {"type": "template", "template": "template.yml", "dest": "generated.yml"},
                {
                    "type": "transform",
                    "file": "generated.yml",
                    "optional": True,
                    "hook": "suffix",
                },
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert [str(change.relative_path) for change in plan.changes] == ["generated.yml"]
    assert plan.changes[0].after == "created\ntransformed\n"
    assert plan.warnings == ()


def test_remove_files_removes_multiple_files_and_skips_missing_files(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "ansible.cfg").write_text("delete\n")
    (target / "old.cfg").write_text("delete too\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "remove", "files": ["ansible.cfg", "old.cfg", "missing.cfg"]}],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert sorted(str(change.relative_path) for change in plan.changes) == [
        "ansible.cfg",
        "old.cfg",
    ]
    assert all(change.after is None for change in plan.changes)
