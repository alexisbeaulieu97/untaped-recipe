"""Tests for planning recipes against target directories."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_loader import HookLoader


def _planner(tmp_path: Path) -> ApplyRecipe:
    return ApplyRecipe(
        HookLoader(global_hooks=tmp_path / "global", builtins=()),
        helpers=HookHelpers(),
    )


def test_apply_recipe_plans_template_copy_remove_and_transform(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "templates").mkdir()
    (recipe_dir / "files").mkdir()
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "templates" / "config.yml").write_text("name: {{ service }}\n")
    (recipe_dir / "files" / "README.md").write_text("# Shared\n")
    (recipe_dir / "hooks" / "upper.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content.upper()\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "legacy.txt").write_text("delete me\n")
    (target / "name.txt").write_text("hello\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
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


def test_apply_recipe_failing_validate_aborts_target_without_changes(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "hooks" / "fail.py").write_text(
        "def validate(*, inputs, target, args, helpers):\n    return helpers.fail('not ready')\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
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


def test_optional_transform_skips_missing_disk_files_with_warning(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "hooks" / "mark.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content + file.name + '\\n'\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
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


def test_optional_transform_still_errors_after_explicit_remove(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "hooks" / "noop.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
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
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "hooks" / "noop.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
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
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "template.yml").write_text("created\n")
    (recipe_dir / "hooks" / "suffix.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content + 'transformed\\n'\n"
    )
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
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
            "name": "demo",
            "steps": [{"type": "remove", "files": ["ansible.cfg", "old.cfg", "missing.cfg"]}],
        }
    )

    plan = _planner(tmp_path)(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})

    assert sorted(str(change.relative_path) for change in plan.changes) == [
        "ansible.cfg",
        "old.cfg",
    ]
    assert all(change.after is None for change in plan.changes)
