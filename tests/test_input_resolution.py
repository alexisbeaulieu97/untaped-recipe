"""Tests for per-target recipe input resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped.errors import ConfigError

from untaped_recipe.application.inputs import (
    InputResolutionConfig,
    InputResolutionResult,
    NoPromptAvailable,
    redact_inputs,
    resolve_target_inputs,
)
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.recipe import Recipe


class PromptRecorder:
    """Test prompt backend for input-resolution unit tests."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.messages: list[str] = []

    def ask(self, message: str, *, sensitive: bool) -> str:
        self.messages.append(f"{message}|sensitive={sensitive}")
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


def test_resolve_target_inputs_uses_record_target_fallbacks_and_native_values() -> None:
    target = Target(
        path=Path("/work/acme/api"),
        record={"repo": "inventory", "replicas": 0, "enabled": False, "label": ""},
        kind="workspace.repo",
        lineno=7,
    )

    result = resolve_target_inputs(
        _recipe(),
        target,
        config=InputResolutionConfig(global_values={"token": "secret"}),
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
    result = resolve_target_inputs(
        _recipe(),
        Target(path=Path("/work/acme/api"), record=record),
        config=InputResolutionConfig(global_values={"token": "secret"}),
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
        resolve_target_inputs(
            recipe,
            Target(path=Path("/work/acme/api")),
            config=InputResolutionConfig(),
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
        resolve_target_inputs(
            recipe,
            Target(path=Path("/work/acme/api")),
            config=InputResolutionConfig(),
        )


def test_input_resolution_rejects_cli_value_and_source_conflicts() -> None:
    with pytest.raises(ConfigError, match="cannot combine --var/--vars and --input-from"):
        resolve_target_inputs(
            _recipe(),
            Target(path=Path("/work/acme/api")),
            config=InputResolutionConfig(
                global_values={"service": "api", "token": "secret"},
                input_from={"service": "{{ target.name }}"},
            ),
        )


def test_input_resolution_rejects_input_from_for_explicit_global_scope() -> None:
    with pytest.raises(ConfigError, match="scope global"):
        resolve_target_inputs(
            _recipe(),
            Target(path=Path("/work/acme/api")),
            config=InputResolutionConfig(
                global_values={"token": "secret"},
                input_from={"token": "{{ target.name }}"},
            ),
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

    result = resolve_target_inputs(
        recipe,
        Target(path=Path("/work/acme/api")),
        config=InputResolutionConfig(interactive=True, prompt=prompt.ask),
    )

    assert result.values == {"owner": "platform", "service": "api"}
    assert prompt.messages == [
        "owner (Owning team.)|sensitive=False",
        "service for /work/acme/api (Service name.)|sensitive=False",
    ]


def test_interactive_without_prompt_backend_fails_clearly() -> None:
    recipe = Recipe.model_validate(
        {"version": 1, "inputs": {"service": {"type": "str", "required": True}}}
    )

    with pytest.raises(NoPromptAvailable, match="interactive input requires a terminal"):
        resolve_target_inputs(
            recipe,
            Target(path=Path("/work/acme/api")),
            config=InputResolutionConfig(interactive=True),
        )


def test_redact_inputs_only_redacts_sensitive_declared_inputs() -> None:
    assert redact_inputs(_recipe().inputs, {"service": "api", "token": "secret"}) == {
        "service": "api",
        "token": "***",
    }
