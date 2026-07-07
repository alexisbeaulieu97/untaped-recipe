"""Resolve recipe inputs for one apply invocation and target."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml
from untaped.api import ConfigError

from untaped_recipe.application.targets import Target
from untaped_recipe.domain.recipe import InputSpec, Recipe
from untaped_recipe.infrastructure.input_jinja import (
    UNRESOLVED,
    CompiledInputSource,
    InputSourceError,
    compile_input_source,
    derive_input_value,
    ensure_derived_value_within_bound,
)

REDACTED = "***"
_UNSET = object()


class PromptFunc(Protocol):
    """Prompt callback used by interactive input resolution."""

    def __call__(
        self,
        message: str,
        *,
        sensitive: bool,
        default: object | None = None,
        required: bool = True,
    ) -> object: ...


class NoPromptAvailable(ConfigError):
    """Raised when interactive input is requested without a prompt backend."""


@dataclass(frozen=True)
class InputResolutionConfig:
    """Invocation-level input resolution settings."""

    fixed_values: Mapping[str, object] = field(default_factory=dict)
    cli_sources: Mapping[str, CompiledInputSource] = field(default_factory=dict)
    recipe_sources: Mapping[str, CompiledInputSource] = field(default_factory=dict)
    interactive: bool = False
    prompt: PromptFunc | None = None


@dataclass(frozen=True)
class InputResolutionResult:
    """Resolved real inputs plus a redacted display/audit view."""

    values: dict[str, object]
    display_values: dict[str, object]


def prepare_input_resolution(
    recipe: Recipe,
    *,
    fixed_values: Mapping[str, object],
    input_from: Mapping[str, str],
    interactive: bool = False,
    prompt: PromptFunc | None = None,
) -> InputResolutionConfig:
    """Validate and compile invocation-level input resolution settings."""
    _validate_config(recipe, fixed_values=fixed_values, input_from=input_from)
    typed_fixed_values = _coerce_fixed_values(recipe, fixed_values)
    cli_sources = {
        name: _compile_named_source(name, (expression,)) for name, expression in input_from.items()
    }
    recipe_sources = {
        name: _compile_named_source(name, spec.from_)
        for name, spec in recipe.inputs.items()
        if spec.from_
    }
    return InputResolutionConfig(
        fixed_values=typed_fixed_values,
        cli_sources=cli_sources,
        recipe_sources=recipe_sources,
        interactive=interactive,
        prompt=prompt,
    )


def validate_recipe_input_sources(recipe: Recipe) -> None:
    """Validate recipe-owned input source expressions without resolving targets."""
    for name, spec in recipe.inputs.items():
        if spec.from_:
            _compile_named_source(name, spec.from_)


def resolve_global_values(recipe: Recipe, config: InputResolutionConfig) -> dict[str, object]:
    """Resolve invocation-global input values once before target planning."""
    values: dict[str, object] = {}
    for name, spec in recipe.inputs.items():
        if spec.scope != "global":
            continue
        value = _resolve_one(name, spec, target=None, config=config)
        if value is not _UNSET:
            values[name] = value
    return values


def resolve_target_inputs(
    recipe: Recipe,
    target: Target,
    *,
    config: InputResolutionConfig,
    global_values: Mapping[str, object],
) -> InputResolutionResult:
    """Resolve all declared inputs for one target."""
    values: dict[str, object] = {}
    for name, spec in recipe.inputs.items():
        if spec.scope == "global":
            if name in global_values:
                values[name] = global_values[name]
            continue
        value = _resolve_one(name, spec, target=target, config=config)
        if value is not _UNSET:
            values[name] = value
    return InputResolutionResult(values=values, display_values=redact_inputs(recipe.inputs, values))


def redact_inputs(
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
) -> dict[str, object]:
    """Return declared input values with sensitive entries redacted."""
    redacted: dict[str, object] = {}
    for name, value in values.items():
        spec = specs.get(name)
        if spec is None:
            continue
        redacted[name] = REDACTED if spec.sensitive else value
    return redacted


def has_sensitive_inputs(
    specs: Mapping[str, InputSpec],
    display_values: Mapping[str, object],
) -> bool:
    """Return whether the display row contains any resolved sensitive input."""
    return any((spec := specs.get(name)) is not None and spec.sensitive for name in display_values)


def _resolve_one(
    name: str,
    spec: InputSpec,
    *,
    target: Target | None,
    config: InputResolutionConfig,
) -> object:
    if name in config.fixed_values:
        return config.fixed_values[name]
    cli_source = config.cli_sources.get(name)
    if cli_source is not None and target is not None:
        rendered = _derive_source_value(cli_source, target)
        if rendered is UNRESOLVED:
            raise ValueError(
                f"--input-from for input {name!r} did not resolve for target: {target.path}"
            )
        return _coerce_derived_value(spec, rendered)
    recipe_source = config.recipe_sources.get(name) if target is not None else None
    if recipe_source is not None and target is not None:
        rendered = _derive_source_value(recipe_source, target)
        if rendered is not UNRESOLVED:
            return _coerce_derived_value(spec, rendered)
    if config.interactive:
        if spec.type in {"list", "dict"}:
            raise ConfigError(
                f"interactive prompting is not supported for structured input {name!r}; "
                "pass --var or --vars"
            )
        prompt_target = None if target is None else target.path
        prompted = _prompt_value(name, spec, prompt_target, config)
        return _UNSET if prompted is _UNSET else spec.coerce(prompted)
    if spec.default is not None:
        return spec.coerce(spec.default)
    if spec.required:
        raise ValueError(f"missing required input: {name}")
    return _UNSET


def _validate_config(
    recipe: Recipe,
    *,
    fixed_values: Mapping[str, object],
    input_from: Mapping[str, str],
) -> None:
    unknown_values = sorted(set(fixed_values) - set(recipe.inputs))
    if unknown_values:
        raise ConfigError(f"unknown input: {unknown_values[0]}")
    unknown_sources = sorted(set(input_from) - set(recipe.inputs))
    if unknown_sources:
        raise ConfigError(f"unknown input: {unknown_sources[0]}")
    for name in input_from:
        if recipe.inputs[name].scope == "global":
            raise ConfigError(f"cannot use --input-from for input {name!r} with scope global")
    conflicts = sorted(set(fixed_values) & set(input_from))
    if conflicts:
        raise ConfigError(f"cannot combine --var/--vars and --input-from for {conflicts[0]}")


def _coerce_fixed_values(
    recipe: Recipe,
    fixed_values: Mapping[str, object],
) -> dict[str, object]:
    typed: dict[str, object] = {}
    for name, value in fixed_values.items():
        spec = recipe.inputs[name]
        try:
            typed[name] = spec.coerce(_prepare_fixed_value(name, spec, value))
        except ConfigError:
            raise
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    return typed


def _prepare_fixed_value(name: str, spec: InputSpec, value: object) -> object:
    if spec.type not in {"list", "dict"} or not isinstance(value, str):
        return value
    expected = "list" if spec.type == "list" else "mapping"
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        raise ConfigError(f"input {name!r} expects YAML {expected}: {exc}") from exc
    if spec.type == "list" and not isinstance(parsed, list):
        raise ConfigError(f"input {name!r} expects YAML list: parsed value is not a list")
    if spec.type == "dict" and not isinstance(parsed, dict):
        raise ConfigError(f"input {name!r} expects YAML mapping: parsed value is not a mapping")
    return parsed


def _compile_named_source(name: str, candidates: tuple[str, ...]) -> CompiledInputSource:
    try:
        return compile_input_source(candidates)
    except InputSourceError as exc:
        raise ConfigError(f"invalid input source expression for {name}: {exc}") from exc


def _derive_source_value(source: CompiledInputSource, target: Target) -> object:
    try:
        return derive_input_value(source, context=_target_context(target))
    except InputSourceError as exc:
        raise ValueError(str(exc)) from exc


def _coerce_derived_value(spec: InputSpec, value: object) -> object:
    structured = spec.type in {"list", "dict"}
    try:
        ensure_derived_value_within_bound(value, structured=structured)
        coerced = spec.coerce(value)
        ensure_derived_value_within_bound(coerced, structured=structured)
    except InputSourceError as exc:
        raise ValueError(str(exc)) from exc
    return coerced


def _target_context(target: Target) -> dict[str, object]:
    context: dict[str, object] = {
        "target": {
            "path": str(target.path),
            "name": target.path.name,
            "parent_path": str(target.path.parent),
            "parent_name": target.path.parent.name,
        }
    }
    if target.record is not None:
        context["record"] = dict(target.record)
    return context


def _prompt_value(
    name: str,
    spec: InputSpec,
    target: Path | None,
    config: InputResolutionConfig,
) -> object:
    if config.prompt is None:
        raise NoPromptAvailable("interactive input requires a terminal prompt backend")
    details: list[str] = []
    if spec.description:
        details.append(spec.description)
    if spec.default is not None and not spec.sensitive:
        details.append(f"default: {spec.default}")
    suffix = f" ({'; '.join(details)})" if details else ""
    message = f"{name}{suffix}" if target is None else f"{name} for {target}{suffix}"
    value = config.prompt(
        message,
        sensitive=spec.sensitive,
        default=None if spec.sensitive else spec.default,
        required=spec.required and spec.default is None,
    )
    if value == "" and spec.default is not None:
        return spec.default
    if value == "" and not spec.required:
        return _UNSET
    return value
