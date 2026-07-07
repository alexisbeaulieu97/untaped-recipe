"""Tests for per-target recipe input resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped.api import ConfigError

from untaped_recipe.application.inputs import (
    InputResolutionConfig,
    InputResolutionResult,
    NoPromptAvailable,
    has_sensitive_inputs,
    prepare_input_resolution,
    redact_inputs,
    resolve_global_values,
    resolve_target_inputs,
)
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure import input_jinja


class PromptRecorder:
    """Test prompt backend for input-resolution unit tests."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.messages: list[str] = []

    def ask(
        self,
        message: str,
        *,
        sensitive: bool,
        default: object | None = None,
        required: bool = True,
    ) -> str:
        self.messages.append(
            f"{message}|sensitive={sensitive}|default={default}|required={required}"
        )
        try:
            return self.answers[message]
        except KeyError as exc:
            raise AssertionError(f"unexpected prompt: {message}") from exc


def _recipe() -> Recipe:
    return Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {
                    "type": "str",
                    "required": True,
                    "description": "Service name.",
                    "from": ["{{ record.repo }}", "{{ target.name }}"],
                },
                "replicas": {
                    "type": "int",
                    "from": "{{ record.replicas }}",
                    "default": 2,
                },
                "enabled": {
                    "type": "bool",
                    "from": "{{ record.enabled }}",
                    "default": True,
                },
                "token": {
                    "type": "str",
                    "scope": "global",
                    "required": True,
                    "description": "API token.",
                    "sensitive": True,
                },
                "label": {
                    "type": "str",
                    "from": "{{ record.label }}",
                    "default": "fallback",
                },
            },
        }
    )


def _config(
    recipe: Recipe,
    *,
    fixed_values: dict[str, object] | None = None,
    input_from: dict[str, str] | None = None,
    interactive: bool = False,
    prompt: PromptRecorder | None = None,
) -> InputResolutionConfig:
    return prepare_input_resolution(
        recipe,
        fixed_values=fixed_values or {},
        input_from=input_from or {},
        interactive=interactive,
        prompt=None if prompt is None else prompt.ask,
    )


def _resolve(
    recipe: Recipe,
    target: Target,
    *,
    fixed_values: dict[str, object] | None = None,
    input_from: dict[str, str] | None = None,
    interactive: bool = False,
    prompt: PromptRecorder | None = None,
) -> InputResolutionResult:
    config = _config(
        recipe,
        fixed_values=fixed_values,
        input_from=input_from,
        interactive=interactive,
        prompt=prompt,
    )
    return resolve_target_inputs(
        recipe,
        target,
        config=config,
        global_values=resolve_global_values(recipe, config),
    )


def test_resolve_target_inputs_uses_record_target_fallbacks_and_native_values() -> None:
    target = Target(
        path=Path("/work/acme/api"),
        record={"repo": "inventory", "replicas": 0, "enabled": False, "label": ""},
    )

    result = _resolve(
        _recipe(),
        target,
        fixed_values={"token": "secret"},
    )

    assert result == InputResolutionResult(
        values={
            "service": "inventory",
            "replicas": 0,
            "enabled": False,
            "token": "secret",
            "label": "",
        },
        display_values={
            "service": "inventory",
            "replicas": 0,
            "enabled": False,
            "token": "***",
            "label": "",
        },
    )


@pytest.mark.parametrize("record", [None, {"repo": None}])
def test_resolve_target_inputs_falls_back_to_target_name_without_record(
    record: dict[str, object] | None,
) -> None:
    result = _resolve(
        _recipe(),
        Target(path=Path("/work/acme/api"), record=record),
        fixed_values={"token": "secret"},
    )

    assert result.values["service"] == "api"
    assert result.values["replicas"] == 2
    assert result.values["label"] == "fallback"


def test_input_resolution_rejects_unsafe_template_access() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {
                    "type": "str",
                    "required": True,
                    "from": "{{ target.name.__class__.__mro__ }}",
                },
            },
        }
    )

    with pytest.raises(ValueError, match="invalid input source expression"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api")),
        )


def test_resolve_target_inputs_treats_missing_required_target_input_as_target_error() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "team": {"type": "str", "required": True, "from": "{{ record.team }}"},
            },
        }
    )

    with pytest.raises(ValueError, match="missing required input: team"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api")),
        )


def test_cli_input_from_that_does_not_resolve_is_an_error() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "owner": {
                    "type": "str",
                    "default": "platform",
                    "from": "{{ target.name }}",
                },
            },
        }
    )

    with pytest.raises(ValueError, match="--input-from for input 'owner' did not resolve"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api")),
            input_from={"owner": "{{ record.owner }}"},
        )


def test_recipe_from_still_falls_back_to_default() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "owner": {
                    "type": "str",
                    "default": "platform",
                    "from": "{{ record.owner }}",
                },
            },
        }
    )

    result = _resolve(recipe, Target(path=Path("/work/acme/api")))

    assert result.values["owner"] == "platform"


def test_input_resolution_rejects_cli_value_and_source_conflicts() -> None:
    with pytest.raises(ConfigError, match="cannot combine --var/--vars and --input-from"):
        _config(
            _recipe(),
            fixed_values={"service": "api", "token": "secret"},
            input_from={"service": "{{ target.name }}"},
        )


def test_input_resolution_rejects_input_from_for_explicit_global_scope() -> None:
    with pytest.raises(ConfigError, match="scope global"):
        _config(
            _recipe(),
            fixed_values={"token": "secret"},
            input_from={"token": "{{ target.name }}"},
        )


def test_interactive_prompts_for_global_and_target_inputs() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "owner": {
                    "type": "str",
                    "scope": "global",
                    "required": True,
                    "description": "Owning team.",
                },
                "service": {
                    "type": "str",
                    "required": True,
                    "description": "Service name.",
                    "from": "{{ record.repo }}",
                },
            },
        }
    )
    prompt = PromptRecorder(
        {
            "owner (Owning team.)": "platform",
            "service for /work/acme/api (Service name.)": "api",
        }
    )

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api")),
        interactive=True,
        prompt=prompt,
    )

    assert result.values == {"owner": "platform", "service": "api"}
    assert prompt.messages == [
        "owner (Owning team.)|sensitive=False|default=None|required=True",
        "service for /work/acme/api (Service name.)|sensitive=False|default=None|required=True",
    ]


def test_interactive_prompt_runs_before_default_and_empty_uses_default() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "replicas": {"type": "int", "default": 2},
                "owner": {"type": "str", "default": "platform"},
            },
        }
    )
    prompt = PromptRecorder(
        {
            "replicas (default: 2)": "5",
            "owner (default: platform)": "",
        }
    )

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api")),
        interactive=True,
        prompt=prompt,
    )

    assert result.values == {"replicas": 5, "owner": "platform"}
    assert prompt.messages == [
        "replicas (default: 2)|sensitive=False|default=2|required=False",
        "owner (default: platform)|sensitive=False|default=platform|required=False",
    ]


def test_interactive_prompts_optional_inputs_and_empty_optional_stays_unset() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "owner": {"type": "str"},
                "label": {"type": "str", "scope": "target"},
            },
        }
    )
    prompt = PromptRecorder(
        {
            "owner": "platform",
            "label for /work/acme/api": "",
        }
    )

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api")),
        interactive=True,
        prompt=prompt,
    )

    assert result.values == {"owner": "platform"}
    assert prompt.messages == [
        "owner|sensitive=False|default=None|required=False",
        "label for /work/acme/api|sensitive=False|default=None|required=False",
    ]


def test_interactive_prompting_rejects_structured_inputs() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "cols": {"type": "list", "required": True},
            },
        }
    )
    prompt = PromptRecorder({"cols": "[]"})

    with pytest.raises(
        ConfigError,
        match=r"interactive prompting is not supported for structured input 'cols'; "
        r"pass --var or --vars",
    ):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api")),
            interactive=True,
            prompt=prompt,
        )

    assert prompt.messages == []


def test_interactive_resolution_uses_structured_default_without_prompting() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "cols": {"type": "list", "default": ["name", "path"]},
            },
        }
    )
    prompt = PromptRecorder({})

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api")),
        interactive=True,
        prompt=prompt,
    )

    assert result.values == {"cols": ["name", "path"]}
    assert prompt.messages == []


def test_interactive_resolution_skips_optional_structured_input() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "cols": {"type": "list", "required": False},
            },
        }
    )
    prompt = PromptRecorder({})

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api")),
        interactive=True,
        prompt=prompt,
    )

    assert "cols" not in result.values
    assert prompt.messages == []


def test_sensitive_default_is_not_shown_or_passed_to_prompt_backend() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "token": {
                    "type": "str",
                    "scope": "global",
                    "sensitive": True,
                    "default": "TOP-SECRET-9000",
                },
            },
        }
    )
    prompt = PromptRecorder({"token": ""})

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api")),
        interactive=True,
        prompt=prompt,
    )

    assert result.values == {"token": "TOP-SECRET-9000"}
    assert prompt.messages == ["token|sensitive=True|default=None|required=False"]


def test_interactive_without_prompt_backend_fails_clearly() -> None:
    recipe = Recipe.model_validate(
        {"version": 1, "inputs": {"service": {"type": "str", "required": True}}}
    )

    with pytest.raises(NoPromptAvailable, match="interactive input requires a terminal"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api")),
            interactive=True,
        )


def test_invalid_jinja_syntax_fails_during_input_preparation() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": "{{ target.name"},
            },
        }
    )

    with pytest.raises(ConfigError, match="invalid input source expression for service"):
        _config(recipe)


def test_jinja_control_blocks_fail_during_input_preparation() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {
                    "type": "str",
                    "from": "{% for item in [target.name] %}{{ item }}{% endfor %}",
                },
            },
        }
    )

    with pytest.raises(ConfigError, match="invalid input source expression for service"):
        _config(recipe)


@pytest.mark.parametrize(
    "expression",
    [
        "{{ target.name | upper }}",
        "{{ target.name is string }}",
        "{{ target.name.upper() }}",
        "{{ target.name ~ '-api' }}",
        "{{ 2 + 2 }}",
        "{{ {'service': target.name} }}",
        "{{ [target.name] }}",
    ],
)
def test_jinja_rejects_non_field_derivation_during_input_preparation(
    expression: str,
) -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": expression},
            },
        }
    )

    with pytest.raises(ConfigError, match="invalid input source expression for service"):
        _config(recipe)


def test_jinja_allows_scalar_literals_and_target_record_field_access() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": "svc-{{ target.name }}"},
                "replicas": {"type": "int", "from": "{{ record.replicas }}"},
                "enabled": {"type": "bool", "from": "{{ false }}"},
                "zero": {"type": "int", "from": "{{ 0 }}"},
                "empty": {"type": "str", "from": "{{ '' }}"},
            },
        }
    )

    result = _resolve(
        recipe,
        Target(path=Path("/work/acme/api"), record={"replicas": 3}),
    )

    assert result.values == {
        "service": "svc-api",
        "replicas": 3,
        "enabled": False,
        "zero": 0,
        "empty": "",
    }


@pytest.mark.parametrize("expression", ["{{ range(3) }}", "{{ dict(service=target.name) }}"])
def test_jinja_has_no_ambient_globals(expression: str) -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "required": True, "from": expression},
            },
        }
    )

    with pytest.raises(ConfigError, match="invalid input source expression"):
        _config(recipe)


def test_derived_value_size_is_bounded() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": "{{ 'x' * 9000 }}"},
            },
        }
    )

    with pytest.raises(ConfigError, match="invalid input source expression"):
        _config(recipe)


def test_derived_large_integer_value_is_bounded() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "big": {"type": "int", "from": "{{ 2 ** 9000 }}"},
            },
        }
    )

    with pytest.raises(ConfigError, match="invalid input source expression"):
        _config(recipe)


def test_derived_large_record_integer_value_is_bounded() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "big": {"type": "str", "from": "{{ record.big }}"},
            },
        }
    )

    with pytest.raises(ValueError, match="maximum length"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api"), record={"big": 2**9000}),
        )


def test_derived_container_values_are_rejected_without_copying_contents() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": "{{ record }}"},
            },
        }
    )
    target = Target(path=Path("/work/acme/api"), record={"token": "TOP-SECRET-9000"})

    with pytest.raises(ValueError, match="derived input value must be a scalar") as exc_info:
        _resolve(recipe, target)

    assert "TOP-SECRET-9000" not in str(exc_info.value)


def test_derived_list_values_feed_structured_inputs() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "collections": {
                    "type": "list",
                    "items": "str",
                    "from": "{{ record.collections }}",
                },
            },
        }
    )
    target = Target(
        path=Path("/work/acme/api"),
        record={"collections": ["ansible.builtin", "community.general"]},
    )

    result = _resolve(recipe, target)

    assert result.values == {"collections": ["ansible.builtin", "community.general"]}


def test_scalar_declared_input_still_rejects_derived_list_values() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "from": "{{ record.services }}"},
            },
        }
    )

    with pytest.raises(ValueError, match="derived input value must be a scalar"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api"), record={"services": ["api"]}),
        )


def test_derived_structured_values_still_obey_size_bound() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "collections": {"type": "list", "from": "{{ record.collections }}"},
            },
        }
    )

    with pytest.raises(ValueError, match="maximum length"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api"), record={"collections": ["x" * 9000]}),
        )


def test_derived_structured_values_reject_nested_containers_during_coercion() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "collections": {"type": "list", "from": "{{ record.collections }}"},
            },
        }
    )

    with pytest.raises(ValueError, match="cannot coerce value to list"):
        _resolve(
            recipe,
            Target(path=Path("/work/acme/api"), record={"collections": [["nested"]]}),
        )


def test_fixed_values_are_coerced_once_during_input_preparation() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "replicas": {"type": "int", "scope": "target"},
            },
        }
    )

    config = _config(recipe, fixed_values={"replicas": "3"})

    assert config.fixed_values == {"replicas": 3}


def test_invalid_fixed_values_fail_during_input_preparation() -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "replicas": {"type": "int", "scope": "target"},
            },
        }
    )

    with pytest.raises(ConfigError, match="cannot coerce value to int"):
        _config(recipe, fixed_values={"replicas": "not-an-int"})


def test_has_sensitive_inputs_detects_declared_sensitive_display_values() -> None:
    assert has_sensitive_inputs(_recipe().inputs, {"token": "***"})
    assert not has_sensitive_inputs(_recipe().inputs, {"service": "api"})


def test_jinja_compile_failures_are_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    input_jinja._compile_template.cache_clear()

    def fail_compile(_expression: str) -> object:
        raise RuntimeError("compile boom")

    monkeypatch.setattr(input_jinja, "_compile_template", fail_compile)

    with pytest.raises(input_jinja.InputSourceError, match="compile boom"):
        input_jinja.compile_input_source(("{{ target.name }}",))


def test_redact_inputs_only_redacts_sensitive_declared_inputs() -> None:
    assert redact_inputs(_recipe().inputs, {"service": "api", "token": "secret"}) == {
        "service": "api",
        "token": "***",
    }
