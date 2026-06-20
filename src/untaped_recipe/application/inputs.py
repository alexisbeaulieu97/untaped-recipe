"""Resolve recipe inputs for one apply invocation and target."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from untaped.errors import ConfigError

from untaped_recipe.application.targets import Target
from untaped_recipe.domain.recipe import InputSpec, Recipe

REDACTED = "***"
_UNSET = object()


class PromptFunc(Protocol):
    """Prompt callback used by interactive input resolution."""

    def __call__(self, message: str, *, sensitive: bool) -> str: ...


class NoPromptAvailable(ConfigError):
    """Raised when interactive input is requested without a prompt backend."""


@dataclass(frozen=True)
class InputResolutionConfig:
    """Invocation-level input resolution settings."""

    global_values: Mapping[str, object] = field(default_factory=dict)
    input_from: Mapping[str, str] = field(default_factory=dict)
    interactive: bool = False
    prompt: PromptFunc | None = None


@dataclass(frozen=True)
class InputResolutionResult:
    """Resolved real inputs plus a redacted display/audit view."""

    values: dict[str, object]
    display_values: dict[str, object]


class _RenderableTemplate(Protocol):
    """Rendered template protocol for lazy Jinja loading."""

    def render(self, *args: object, **kwargs: object) -> object: ...


class _JinjaEnvironment(Protocol):
    """Minimal Jinja environment protocol used by input derivation."""

    def from_string(self, source: str) -> _RenderableTemplate: ...


def resolve_global_inputs(recipe: Recipe, config: InputResolutionConfig) -> dict[str, object]:
    """Resolve invocation-global input values once before target planning."""
    _validate_config(recipe, config)
    values: dict[str, object] = {}
    for name, spec in recipe.inputs.items():
        if spec.scope != "global":
            continue
        if name in config.global_values:
            values[name] = spec.coerce(config.global_values[name])
        elif spec.default is not None:
            values[name] = spec.coerce(spec.default)
        elif spec.required:
            if config.interactive:
                values[name] = spec.coerce(_prompt_value(name, spec, None, config))
            else:
                raise ValueError(f"missing required input: {name}")
    return values


def resolve_target_inputs(
    recipe: Recipe,
    target: Target,
    *,
    config: InputResolutionConfig,
) -> InputResolutionResult:
    """Resolve all declared inputs for one target."""
    _validate_config(recipe, config)
    values: dict[str, object] = {}
    for name, spec in recipe.inputs.items():
        if name in config.global_values:
            values[name] = spec.coerce(config.global_values[name])
            continue
        if spec.scope == "global":
            if spec.default is not None:
                values[name] = spec.coerce(spec.default)
            elif spec.required:
                if config.interactive:
                    values[name] = spec.coerce(_prompt_value(name, spec, None, config))
                else:
                    raise ValueError(f"missing required input: {name}")
            continue

        rendered = _resolve_target_source(name, spec, target, config)
        if rendered is not _UNSET:
            values[name] = spec.coerce(rendered)
        elif spec.default is not None:
            values[name] = spec.coerce(spec.default)
        elif spec.required:
            if config.interactive:
                values[name] = spec.coerce(_prompt_value(name, spec, target.path, config))
            else:
                raise ValueError(f"missing required input: {name}")
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


def _validate_config(recipe: Recipe, config: InputResolutionConfig) -> None:
    unknown_values = sorted(set(config.global_values) - set(recipe.inputs))
    if unknown_values:
        raise ConfigError(f"unknown input: {unknown_values[0]}")
    unknown_sources = sorted(set(config.input_from) - set(recipe.inputs))
    if unknown_sources:
        raise ConfigError(f"unknown input: {unknown_sources[0]}")
    for name in config.input_from:
        if recipe.inputs[name].scope == "global":
            raise ConfigError(f"cannot use --input-from for input {name!r} with scope global")
    conflicts = sorted(set(config.global_values) & set(config.input_from))
    if conflicts:
        raise ConfigError(f"cannot combine --var/--vars and --input-from for {conflicts[0]}")


def _resolve_target_source(
    name: str,
    spec: InputSpec,
    target: Target,
    config: InputResolutionConfig,
) -> object:
    source = (config.input_from[name],) if name in config.input_from else spec.from_
    if not source:
        return _UNSET
    context = _target_context(target)
    return _resolve_from(source, context=context)


def _resolve_from(candidates: tuple[str, ...], *, context: dict[str, object]) -> object:
    from jinja2 import Undefined  # noqa: PLC0415
    from jinja2.exceptions import TemplateError, UndefinedError  # noqa: PLC0415

    env = _jinja_env()
    for expression in candidates:
        try:
            value = env.from_string(expression).render(**context)
        except UndefinedError:
            continue
        except TemplateError as exc:
            raise ValueError(f"invalid input source expression: {exc}") from exc
        if value is None or isinstance(value, Undefined):
            continue
        return value
    return _UNSET


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
) -> str:
    if config.prompt is None:
        raise NoPromptAvailable("interactive input requires a terminal prompt backend")
    description = f" ({spec.description})" if spec.description else ""
    message = f"{name}{description}" if target is None else f"{name} for {target}{description}"
    return config.prompt(message, sensitive=spec.sensitive)


def _jinja_env() -> _JinjaEnvironment:
    from jinja2 import StrictUndefined  # noqa: PLC0415
    from jinja2.nativetypes import NativeCodeGenerator, native_concat  # noqa: PLC0415
    from jinja2.sandbox import SandboxedEnvironment  # noqa: PLC0415

    class _NativeSandboxedEnvironment(SandboxedEnvironment):
        code_generator_class = NativeCodeGenerator
        concat = staticmethod(native_concat)  # type: ignore[assignment]

    return _NativeSandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
