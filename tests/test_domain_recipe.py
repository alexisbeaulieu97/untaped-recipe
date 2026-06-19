"""Tests for recipe schema validation and input resolution."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped_recipe.domain.recipe import (
    CopyStep,
    InputSpec,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)


def test_recipe_schema_accepts_all_v1_step_types() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
            "description": "Demo recipe.",
            "inputs": {
                "service": {"type": "str", "required": True},
                "replicas": {"type": "int", "default": 2},
            },
            "steps": [
                {"type": "validate", "hook": "has_pyproject"},
                {
                    "type": "transform",
                    "file": "pyproject.toml",
                    "hook": "bump_version",
                    "args": {"version": "1.2.0"},
                },
                {"type": "template", "template": "templates/config.yml", "dest": "config.yml"},
                {"type": "copy", "source": "files/README.md", "dest": "README.md"},
                {"type": "remove", "file": "legacy.yml"},
            ],
        }
    )

    assert recipe.version == 1
    assert isinstance(recipe.steps[0], ValidateStep)
    assert isinstance(recipe.steps[1], TransformStep)
    assert isinstance(recipe.steps[2], TemplateStep)
    assert isinstance(recipe.steps[3], CopyStep)
    assert isinstance(recipe.steps[4], RemoveStep)
    assert recipe.inputs["replicas"].default == 2


def test_transform_files_normalize_to_single_file_steps() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
            "steps": [
                {
                    "type": "transform",
                    "files": ["local.yml", "site.yml"],
                    "hook": "add_collections",
                    "optional": True,
                    "args": {"collections": ["ansible.builtin"]},
                }
            ],
        }
    )

    assert len(recipe.steps) == 2
    assert all(isinstance(step, TransformStep) for step in recipe.steps)
    first, second = recipe.steps
    assert isinstance(first, TransformStep)
    assert isinstance(second, TransformStep)
    assert str(first.file) == "local.yml"
    assert str(second.file) == "site.yml"
    assert first.hook == "add_collections"
    assert second.args == {"collections": ["ansible.builtin"]}
    assert first.optional is True
    assert second.optional is True


def test_remove_files_normalize_to_single_file_steps() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "name": "demo",
            "steps": [
                {
                    "type": "remove",
                    "files": ["ansible.cfg", "group_vars/old.yml"],
                }
            ],
        }
    )

    assert len(recipe.steps) == 2
    assert all(isinstance(step, RemoveStep) for step in recipe.steps)
    first, second = recipe.steps
    assert isinstance(first, RemoveStep)
    assert isinstance(second, RemoveStep)
    assert str(first.file) == "ansible.cfg"
    assert str(second.file) == "group_vars/old.yml"


@pytest.mark.parametrize(
    "step",
    [
        {"type": "transform", "file": "local.yml", "files": ["site.yml"], "hook": "edit"},
        {"type": "transform", "hook": "edit"},
        {"type": "remove", "file": "ansible.cfg", "files": ["old.cfg"]},
        {"type": "remove"},
    ],
)
def test_file_fanout_steps_require_exactly_one_of_file_or_files(
    step: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="exactly one of file or files"):
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})


@pytest.mark.parametrize(
    "step",
    [
        {"type": "transform", "files": [], "hook": "edit"},
        {"type": "remove", "files": []},
    ],
)
def test_file_fanout_steps_reject_empty_files(step: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="files must not be empty"):
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})


def test_recipe_rejects_unknown_version_and_step_type() -> None:
    with pytest.raises(ValidationError, match="version"):
        Recipe.model_validate({"version": 2, "name": "bad", "steps": []})

    with pytest.raises(ValidationError, match="shell"):
        Recipe.model_validate(
            {"version": 1, "name": "bad", "steps": [{"type": "shell", "command": "echo no"}]}
        )


def test_input_spec_coerces_supported_types_and_requires_missing_values() -> None:
    specs = {
        "name": InputSpec(type="str", required=True),
        "count": InputSpec(type="int", default=1),
        "enabled": InputSpec(type="bool", default=True),
        "ratio": InputSpec(type="float", default=1.5),
    }

    values = InputSpec.resolve_all(
        specs,
        overrides={"name": "api", "count": "3", "enabled": "false", "ratio": "2.25"},
    )

    assert values == {"name": "api", "count": 3, "enabled": False, "ratio": 2.25}

    with pytest.raises(ValueError, match="missing required input: name"):
        InputSpec.resolve_all(specs, overrides={})

    with pytest.raises(ValueError, match="unknown input"):
        InputSpec.resolve_all(specs, overrides={"name": "api", "extra": "nope"})


@pytest.mark.parametrize(
    "step",
    [
        {"type": "template", "template": "../template.txt", "dest": "out.txt"},
        {"type": "template", "template": "template.txt", "dest": "../out.txt"},
        {"type": "copy", "source": "/tmp/source.txt", "dest": "out.txt"},
        {"type": "copy", "source": "source.txt", "dest": "/tmp/out.txt"},
        {"type": "transform", "file": "../config.yml", "hook": "edit"},
        {"type": "transform", "files": ["local.yml", "../config.yml"], "hook": "edit"},
        {"type": "remove", "file": "/tmp/config.yml"},
        {"type": "remove", "files": ["old.yml", "/tmp/config.yml"]},
    ],
)
def test_recipe_rejects_paths_that_escape_recipe_or_target(step: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="safe relative path"):
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})
