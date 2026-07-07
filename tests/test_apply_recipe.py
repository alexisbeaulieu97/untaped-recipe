"""Tests for planning recipes against target directories."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.ports import HookDebugResult
from untaped_recipe.application.run_bulk import RunBulkApply
from untaped_recipe.application.targets import Target
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
            HookResolver(),
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


def test_apply_recipe_if_absent_template_and_copy_follow_planned_state(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "templates").mkdir()
    (recipe_dir / "files").mkdir()
    (recipe_dir / "templates" / "created.txt").write_text("created from template\n")
    (recipe_dir / "templates" / "existing.txt").write_text("template overwrite\n")
    (recipe_dir / "templates" / "touch.txt").write_text("touched\n")
    (recipe_dir / "templates" / "removed.txt").write_text("recreated\n")
    (recipe_dir / "files" / "created-copy.txt").write_text("created from copy\n")
    (recipe_dir / "files" / "existing-copy.txt").write_text("copy overwrite\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "existing.txt").write_text("keep me\n")
    (target / "existing-copy.txt").write_text("keep copy\n")
    (target / "removed.txt").write_text("delete then recreate\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "template",
                    "template": "templates/created.txt",
                    "dest": "created.txt",
                    "if_absent": True,
                },
                {
                    "type": "template",
                    "template": "templates/existing.txt",
                    "dest": "existing.txt",
                    "if_absent": True,
                },
                {
                    "type": "copy",
                    "source": "files/created-copy.txt",
                    "dest": "created-copy.txt",
                    "if_absent": True,
                },
                {
                    "type": "copy",
                    "source": "files/existing-copy.txt",
                    "dest": "existing-copy.txt",
                    "if_absent": True,
                },
                {
                    "type": "template",
                    "template": "templates/touch.txt",
                    "dest": "planned.txt",
                },
                {
                    "type": "copy",
                    "source": "files/created-copy.txt",
                    "dest": "planned.txt",
                    "if_absent": True,
                },
                {"type": "remove", "file": "removed.txt"},
                {
                    "type": "template",
                    "template": "templates/removed.txt",
                    "dest": "removed.txt",
                    "if_absent": True,
                },
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    changes = {change.relative_path.as_posix(): change for change in plan.changes}
    assert sorted(changes) == ["created-copy.txt", "created.txt", "planned.txt", "removed.txt"]
    assert changes["created.txt"].after == "created from template\n"
    assert changes["created-copy.txt"].after == "created from copy\n"
    assert changes["planned.txt"].after == "touched\n"
    assert changes["removed.txt"].before == "delete then recreate\n"
    assert changes["removed.txt"].after == "recreated\n"


def test_apply_recipe_renders_template_source_and_dest_fields_per_target(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "templates").mkdir()
    (recipe_dir / "templates" / "api.txt").write_text("api={{ service }}\n")
    (recipe_dir / "templates" / "web.txt").write_text("web={{ service }}\n")
    api = tmp_path / "api"
    web = tmp_path / "web"
    api.mkdir()
    web.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": "{{ target.name }}"},
            },
            "steps": [
                {
                    "type": "template",
                    "template": "templates/{{ service }}.txt",
                    "dest": "{{ service }}.yml",
                },
            ],
        }
    )
    runner = RunBulkApply(
        ApplyRecipe(
            HookExecutor(
                HookResolver(),
                workers=InlineWorkers(),
                helpers=HookHelpers(),
            )
        )
    )

    plans = runner.plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=recipe_dir,
        targets=[Target(path=api), Target(path=web)],
        inputs={},
    )

    assert [plan.status for plan in plans] == ["planned", "planned"]
    assert [plans[0].changes[0].relative_path.as_posix()] == ["api.yml"]
    assert plans[0].changes[0].after == "api=api\n"
    assert [plans[1].changes[0].relative_path.as_posix()] == ["web.yml"]
    assert plans[1].changes[0].after == "web=web\n"


def test_apply_recipe_renders_globs_excludes_and_file_fanout_entries(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "stamp": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content + 'seen ' + file.relative_to(target).as_posix() + '\\n'\n"
            )
        },
    )
    target = tmp_path / "target"
    (target / "prod").mkdir(parents=True)
    (target / "prod" / "site.yml").write_text("site\n")
    (target / "prod" / "skip.yml").write_text("skip\n")
    (target / "generated.yml").write_text("generated\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "env": {"type": "str", "default": "prod"},
                "generated": {"type": "str", "default": "generated"},
            },
            "steps": [
                {
                    "type": "transform",
                    "globs": ["{{ env }}/*.yml"],
                    "exclude": ["{{ env }}/skip.yml"],
                    "hook": "stamp",
                },
                {
                    "type": "transform",
                    "files": ["{{ generated }}.yml"],
                    "hook": "stamp",
                },
            ],
        }
    )

    plan = _planner(tmp_path)(
        recipe=recipe,
        recipe_dir=recipe_dir,
        target=target,
        inputs={"env": "prod", "generated": "generated"},
    )

    assert [change.relative_path.as_posix() for change in plan.changes] == [
        "prod/site.yml",
        "generated.yml",
    ]
    assert [change.after for change in plan.changes] == [
        "site\nseen prod/site.yml\n",
        "generated\nseen generated.yml\n",
    ]


def test_apply_recipe_renders_copy_source_dest_and_remove_file_fields(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "files").mkdir()
    (recipe_dir / "files" / "api.txt").write_text("copied\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "old-api.txt").write_text("remove\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"service": {"type": "str", "default": "api"}},
            "steps": [
                {
                    "type": "copy",
                    "source": "files/{{ service }}.txt",
                    "dest": "copied/{{ service }}.txt",
                },
                {"type": "remove", "file": "old-{{ service }}.txt"},
            ],
        }
    )

    plan = _planner(tmp_path)(
        recipe=recipe,
        recipe_dir=recipe_dir,
        target=target,
        inputs={"service": "api"},
    )

    assert [(change.relative_path.as_posix(), change.after) for change in plan.changes] == [
        ("copied/api.txt", "copied\n"),
        ("old-api.txt", None),
    ]


def test_apply_recipe_remove_glob_warning_uses_rendered_pattern(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"suffix": {"type": "str", "default": "generated"}},
            "steps": [{"type": "remove", "globs": ["**/*.{{ suffix }}"]}],
        }
    )

    plan = _planner(tmp_path)(
        recipe=recipe,
        recipe_dir=recipe_dir,
        target=target,
        inputs={"suffix": "generated"},
    )

    assert plan.changes == ()
    assert plan.warnings == ("globs matched no files: **/*.generated",)


def test_apply_recipe_if_absent_uses_rendered_dest(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("new\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "api.yml").write_text("existing\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"service": {"type": "str", "default": "api"}},
            "steps": [
                {
                    "type": "template",
                    "template": "template.txt",
                    "dest": "{{ service }}.yml",
                    "if_absent": True,
                },
            ],
        }
    )

    plan = _planner(tmp_path)(
        recipe=recipe,
        recipe_dir=recipe_dir,
        target=target,
        inputs={"service": "api"},
    )

    assert plan.changes == ()


def test_apply_recipe_rechecks_rendered_dest_for_path_escape_security(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("unsafe\n")
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"name": {"type": "str", "required": True}},
            "steps": [
                {"type": "template", "template": "template.txt", "dest": "{{ name }}.yml"},
            ],
        }
    )

    with pytest.raises(ValueError, match="dest must be a safe relative path"):
        _planner(tmp_path)(
            recipe=recipe,
            recipe_dir=recipe_dir,
            target=target,
            inputs={"name": "../escape"},
        )


def test_apply_recipe_rechecks_rendered_dest_for_absolute_path_injection(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("unsafe\n")
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"name": {"type": "str", "required": True}},
            "steps": [
                {"type": "template", "template": "template.txt", "dest": "{{ name }}.yml"},
            ],
        }
    )

    with pytest.raises(ValueError, match="dest must be a safe relative path"):
        _planner(tmp_path)(
            recipe=recipe,
            recipe_dir=recipe_dir,
            target=target,
            inputs={"name": "/tmp/escape"},
        )


def test_apply_recipe_rejects_sensitive_input_in_path_field(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("secret\n")
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"token": {"type": "str", "sensitive": True}},
            "steps": [
                {"type": "template", "template": "template.txt", "dest": "{{ token }}.yml"},
            ],
        }
    )

    with pytest.raises(
        ValueError,
        match="sensitive input 'token' cannot be used in path field 'dest'",
    ):
        _planner(tmp_path)(
            recipe=recipe,
            recipe_dir=recipe_dir,
            target=target,
            inputs={"token": "secret"},
        )


def test_apply_recipe_rejects_structured_input_in_path_field(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("structured\n")
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {"cols": {"type": "list"}},
            "steps": [
                {"type": "template", "template": "template.txt", "dest": "{{ cols }}.yml"},
            ],
        }
    )

    with pytest.raises(
        ValueError,
        match="structured input 'cols' cannot be rendered; hooks receive it natively",
    ):
        _planner(tmp_path)(
            recipe=recipe,
            recipe_dir=recipe_dir,
            target=target,
            inputs={"cols": ["a"]},
        )


def test_apply_recipe_path_fields_are_strict_even_when_template_body_keeps_unknown_tokens(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("body=${{ github.ref }}\n")
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "template",
                    "template": "template.txt",
                    "dest": "{{ missing }}.yml",
                    "unknown_tokens": "keep",
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="template input 'missing' is not defined"):
        _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})


def test_apply_recipe_if_absent_false_keeps_overwrite_behavior(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "templates").mkdir()
    (recipe_dir / "files").mkdir()
    (recipe_dir / "templates" / "template.txt").write_text("template overwrite\n")
    (recipe_dir / "files" / "copy.txt").write_text("copy overwrite\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "template.txt").write_text("old template\n")
    (target / "copy.txt").write_text("old copy\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "template",
                    "template": "templates/template.txt",
                    "dest": "template.txt",
                    "if_absent": False,
                },
                {"type": "copy", "source": "files/copy.txt", "dest": "copy.txt"},
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    changes = {change.relative_path.as_posix(): change.after for change in plan.changes}
    assert changes == {
        "copy.txt": "copy overwrite\n",
        "template.txt": "template overwrite\n",
    }


def test_apply_recipe_transform_globs_expand_sorted_deduped_and_excluded(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    _write_hook_project(
        recipe_dir,
        {
            "stamp": (
                "def transform(content, *, inputs, target, file, args, helpers):\n"
                "    return content + 'seen: ' + file.relative_to(target).as_posix() + '\\n'\n"
            )
        },
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "playbooks").mkdir()
    (target / "generated").mkdir()
    (target / "z.yml").write_text("z\n")
    (target / "a.yml").write_text("a\n")
    (target / "skip.yml").write_text("skip\n")
    (target / "generated" / "drop.yml").write_text("drop\n")
    (target / "playbooks" / "site.yml").write_text("site\n")
    (target / "playbooks" / "skip.yml").write_text("play skip\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "transform",
                    "globs": ["**/*.yml", "playbooks/*.yml"],
                    "exclude": ["skip.yml", "generated/**", "playbooks/skip.yml"],
                    "hook": "stamp",
                }
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert [change.relative_path.as_posix() for change in plan.changes] == [
        "a.yml",
        "playbooks/site.yml",
        "z.yml",
    ]
    assert [change.after for change in plan.changes] == [
        "a\nseen: a.yml\n",
        "site\nseen: playbooks/site.yml\n",
        "z\nseen: z.yml\n",
    ]


def test_apply_recipe_remove_globs_plan_like_literal_files_and_include_dot_git(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / ".git").mkdir()
    (target / ".git" / "config").write_text("[core]\n")
    (target / "build.bak").write_text("remove\n")
    (target / "keep.txt").write_text("keep\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {"type": "remove", "globs": ["*.bak", ".git/**"]},
            ],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert [
        (change.relative_path.as_posix(), change.before, change.after) for change in plan.changes
    ] == [
        (".git/config", "[core]\n", None),
        ("build.bak", "remove\n", None),
    ]


def test_apply_recipe_glob_exclude_uses_glob_semantics_not_fnmatch(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    target = tmp_path / "target"
    (target / "sub").mkdir(parents=True)
    (target / "top.yml").write_text("top\n")
    (target / "sub" / "nested.yml").write_text("nested\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "remove", "globs": ["**/*.yml"], "exclude": ["*.yml"]}],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert [change.relative_path.as_posix() for change in plan.changes] == ["sub/nested.yml"]


def test_apply_recipe_glob_expansion_skips_directories_and_symlinks(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    target = tmp_path / "target"
    (target / "dir.yml").mkdir(parents=True)
    (target / "real.yml").write_text("real\n")
    outside = tmp_path / "outside.yml"
    outside.write_text("outside\n")
    (target / "link.yml").symlink_to(outside)
    recipe = Recipe.model_validate(
        {"version": 1, "steps": [{"type": "remove", "globs": ["*.yml"]}]}
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert [change.relative_path.as_posix() for change in plan.changes] == ["real.yml"]


def test_apply_recipe_globs_with_zero_matches_warn(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "remove", "globs": ["**/*.generated"]}],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert plan.changes == ()
    assert plan.warnings == ("globs matched no files: **/*.generated",)


def test_apply_recipe_glob_transform_binary_file_reports_target_error(tmp_path: Path) -> None:
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
    (target / "blob.bin").write_bytes(b"\xff\xfe\x00")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "transform", "globs": ["*.bin"], "hook": "noop"}],
        }
    )

    runner = RunBulkApply(
        ApplyRecipe(
            HookExecutor(
                HookResolver(),
                workers=InlineWorkers(),
                helpers=HookHelpers(),
            )
        )
    )
    plan = runner.plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=recipe_dir,
        targets=[Target(path=target)],
        inputs={},
    )[0]

    assert plan.status == "error"
    assert plan.error == (
        "file is not valid UTF-8: blob.bin "
        "(binary files are unsupported; for globs, exclude: skips it)"
    )


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
