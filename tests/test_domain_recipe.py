"""Tests for recipe schema validation and input resolution."""

from __future__ import annotations

import traceback

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
    assert recipe.steps[2].unknown_tokens == "error"
    assert recipe.steps[2].if_absent is False
    assert recipe.steps[3].if_absent is False


def test_template_step_unknown_tokens_accepts_keep_and_rejects_other_values() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
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

    assert isinstance(recipe.steps[0], TemplateStep)
    assert recipe.steps[0].unknown_tokens == "keep"
    with pytest.raises(ValidationError):
        Recipe.model_validate(
            {
                "version": 1,
                "steps": [
                    {
                        "type": "template",
                        "template": "workflow.yml",
                        "dest": ".github/workflows/ci.yml",
                        "unknown_tokens": "passthrough",
                    }
                ],
            }
        )


def test_template_and_copy_steps_accept_if_absent() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "template",
                    "template": "workflow.yml",
                    "dest": ".github/workflows/ci.yml",
                    "if_absent": True,
                },
                {
                    "type": "copy",
                    "source": "files/README.md",
                    "dest": "README.md",
                    "if_absent": True,
                },
            ],
        }
    )

    template, copy = recipe.steps
    assert isinstance(template, TemplateStep)
    assert isinstance(copy, CopyStep)
    assert template.if_absent is True
    assert copy.if_absent is True


def test_transform_files_normalize_to_single_file_steps() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
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


def test_transform_globs_remain_single_planning_time_step() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "transform",
                    "globs": ["**/*.yml", "playbooks/*.yaml"],
                    "exclude": [".git/**", "skip.yml"],
                    "hook": "add_collections",
                    "args": {"collections": ["ansible.builtin"]},
                }
            ],
        }
    )

    assert len(recipe.steps) == 1
    step = recipe.steps[0]
    assert isinstance(step, TransformStep)
    assert step.file is None
    assert step.globs == ("**/*.yml", "playbooks/*.yaml")
    assert step.exclude == (".git/**", "skip.yml")


def test_remove_globs_remain_single_planning_time_step() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [
                {
                    "type": "remove",
                    "globs": ["**/*.bak"],
                    "exclude": ["keep.bak"],
                }
            ],
        }
    )

    assert len(recipe.steps) == 1
    step = recipe.steps[0]
    assert isinstance(step, RemoveStep)
    assert step.file is None
    assert step.globs == ("**/*.bak",)
    assert step.exclude == ("keep.bak",)


def test_remove_files_normalize_to_single_file_steps() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
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
        {"type": "transform", "file": "local.yml", "globs": ["*.yml"], "hook": "edit"},
        {"type": "transform", "files": ["local.yml"], "globs": ["*.yml"], "hook": "edit"},
        {"type": "transform", "hook": "edit"},
        {"type": "remove", "file": "ansible.cfg", "files": ["old.cfg"]},
        {"type": "remove", "file": "ansible.cfg", "globs": ["*.cfg"]},
        {"type": "remove", "files": ["ansible.cfg"], "globs": ["*.cfg"]},
        {"type": "remove"},
    ],
)
def test_file_fanout_steps_require_exactly_one_of_file_files_or_globs(
    step: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="exactly one of file, files, or globs"):
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


@pytest.mark.parametrize(
    "step",
    [
        {"type": "transform", "globs": [], "hook": "edit"},
        {"type": "remove", "globs": []},
    ],
)
def test_file_fanout_steps_reject_empty_globs(step: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="globs must not be empty"):
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (
            {"type": "transform", "file": "local.yml", "exclude": ["skip.yml"], "hook": "edit"},
            "exclude is only valid with globs",
        ),
        (
            {"type": "remove", "file": "old.yml", "exclude": ["skip.yml"]},
            "exclude is only valid with globs",
        ),
        (
            {"type": "transform", "globs": ["*.yml"], "optional": True, "hook": "edit"},
            "optional is not valid with globs",
        ),
        (
            {"type": "transform", "globs": ["*.yml"], "optional": False, "hook": "edit"},
            "optional is not valid with globs",
        ),
    ],
)
def test_glob_steps_reject_invalid_options(step: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})


def test_recipe_rejects_unknown_version_and_step_type() -> None:
    with pytest.raises(ValidationError, match="version"):
        Recipe.model_validate({"version": 2, "name": "bad", "steps": []})

    with pytest.raises(ValidationError, match="shell"):
        Recipe.model_validate(
            {"version": 1, "name": "bad", "steps": [{"type": "shell", "command": "echo no"}]}
        )


def test_input_spec_coerces_supported_types() -> None:
    assert InputSpec(type="str").coerce("api") == "api"
    assert InputSpec(type="int").coerce("3") == 3
    assert InputSpec(type="bool").coerce("false") is False
    assert InputSpec(type="float").coerce("2.25") == 2.25


def test_input_spec_accepts_structured_shapes_and_coerces_elements() -> None:
    list_spec = InputSpec.model_validate({"type": "list", "items": "int"})
    dict_spec = InputSpec.model_validate({"type": "dict", "values": "bool"})

    assert list_spec.items == "int"
    assert list_spec.values is None
    assert list_spec.coerce(["1", 2]) == [1, 2]
    assert list_spec.coerce(()) == []
    assert dict_spec.values == "bool"
    assert dict_spec.items is None
    assert dict_spec.coerce({"enabled": "true", "disabled": False}) == {
        "enabled": True,
        "disabled": False,
    }
    assert dict_spec.coerce({}) == {}


def test_input_spec_structured_shapes_default_to_string_elements() -> None:
    list_spec = InputSpec.model_validate({"type": "list"})
    dict_spec = InputSpec.model_validate({"type": "dict"})

    assert list_spec.items is None
    assert list_spec.coerce([1, "api"]) == ["1", "api"]
    assert dict_spec.values is None
    assert dict_spec.coerce({"replicas": 3}) == {"replicas": "3"}


def test_input_spec_rejects_invalid_structured_shape_metadata() -> None:
    with pytest.raises(ValidationError, match="items is only valid with type list"):
        InputSpec.model_validate({"type": "str", "items": "int"})

    with pytest.raises(ValidationError, match="values is only valid with type dict"):
        InputSpec.model_validate({"type": "str", "values": "int"})


def test_input_spec_structured_coercion_errors_use_pinned_messages() -> None:
    list_spec = InputSpec.model_validate({"type": "list", "items": "int"})
    dict_spec = InputSpec.model_validate({"type": "dict", "values": "int"})

    with pytest.raises(ValueError, match="cannot coerce value to list"):
        list_spec.coerce("not-a-list")

    with pytest.raises(ValueError, match="cannot coerce value to list"):
        list_spec.coerce(["not-an-int"])

    with pytest.raises(ValueError, match="cannot coerce value to list"):
        list_spec.coerce([["nested"]])

    with pytest.raises(ValueError, match="cannot coerce value to dict"):
        dict_spec.coerce("not-a-dict")

    with pytest.raises(ValueError, match="dict input keys must be strings"):
        dict_spec.coerce({1: "2"})

    with pytest.raises(ValueError, match="cannot coerce value to dict"):
        dict_spec.coerce({"replicas": "not-an-int"})


@pytest.mark.parametrize("input_type", ["int", "float", "bool"])
def test_input_spec_coercion_errors_do_not_echo_values(input_type: str) -> None:
    secret = "TOP-SECRET-9000"

    with pytest.raises(ValueError, match=f"cannot coerce value to {input_type}") as excinfo:
        InputSpec(type=input_type).coerce(secret)  # type: ignore[arg-type]

    assert secret not in str(excinfo.value)
    assert secret not in "".join(
        traceback.format_exception(
            type(excinfo.value),
            excinfo.value,
            excinfo.value.__traceback__,
        )
    )


def test_input_spec_supports_metadata_scope_and_from_fallbacks() -> None:
    spec = InputSpec.model_validate(
        {
            "type": "str",
            "description": "Service name.",
            "required": True,
            "from": ["{{ record.repo }}", "{{ target.name }}"],
            "sensitive": True,
        }
    )

    assert spec.description == "Service name."
    assert spec.scope == "target"
    assert spec.from_ == ("{{ record.repo }}", "{{ target.name }}")
    assert spec.sensitive is True


def test_input_spec_infers_global_scope_without_from() -> None:
    assert InputSpec.model_validate({"type": "str"}).scope == "global"


def test_input_spec_rejects_from_on_global_scope_and_unknown_fields() -> None:
    with pytest.raises(ValidationError, match=r"scope.*global.*from"):
        InputSpec.model_validate({"scope": "global", "from": "{{ target.name }}"})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        InputSpec.model_validate({"type": "str", "form": "{{ target.name }}"})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        InputSpec.model_validate({"type": "str", "from_": "{{ target.name }}"})


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
