"""Tests for planning recipes against target directories."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_loader import HookLoader


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

    plan = ApplyRecipe(
        HookLoader(global_hooks=tmp_path / "global", builtins=()),
        helpers=HookHelpers(),
    )(
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
        ApplyRecipe(
            HookLoader(global_hooks=tmp_path / "global", builtins=()),
            helpers=HookHelpers(),
        )(
            recipe=recipe,
            recipe_dir=recipe_dir,
            target=target,
            inputs={},
        )

    assert list(target.iterdir()) == []
