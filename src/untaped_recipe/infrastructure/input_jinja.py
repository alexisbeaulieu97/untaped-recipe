"""Sandboxed Jinja input derivation adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

MAX_DERIVED_VALUE_LENGTH = 8192
UNRESOLVED = object()


class _RenderableTemplate(Protocol):
    """Rendered template protocol for lazy Jinja loading."""

    def render(self, *args: object, **kwargs: object) -> object: ...


class _JinjaEnvironment(Protocol):
    """Minimal Jinja environment protocol used by input derivation."""

    def from_string(self, source: str) -> _RenderableTemplate: ...


@dataclass(frozen=True)
class CompiledInputSource:
    """One ordered set of compiled input source candidates."""

    templates: tuple[_RenderableTemplate, ...]


class InputSourceError(ValueError):
    """Raised when an input source cannot be compiled or rendered safely."""


def compile_input_source(candidates: tuple[str, ...]) -> CompiledInputSource:
    """Compile an ordered set of Jinja source candidates."""
    try:
        return CompiledInputSource(
            templates=tuple(_compile_template(candidate) for candidate in candidates)
        )
    except Exception as exc:
        from jinja2.exceptions import TemplateSyntaxError  # noqa: PLC0415

        if isinstance(exc, TemplateSyntaxError):
            raise InputSourceError(_error_message(exc)) from exc
        raise


def derive_input_value(
    source: CompiledInputSource,
    *,
    context: Mapping[str, object],
) -> object:
    """Return the first resolved candidate value or UNRESOLVED."""
    from jinja2 import Undefined  # noqa: PLC0415
    from jinja2.exceptions import TemplateError, UndefinedError  # noqa: PLC0415

    for template in source.templates:
        try:
            value = template.render(**context)
        except UndefinedError:
            continue
        except TemplateError as exc:
            message = _error_message(exc)
            raise InputSourceError(f"invalid input source expression: {message}") from exc
        if value is None or isinstance(value, Undefined):
            continue
        _ensure_value_within_bound(value)
        return value
    return UNRESOLVED


@lru_cache(maxsize=1024)
def _compile_template(expression: str) -> _RenderableTemplate:
    return _jinja_env().from_string(expression)


@lru_cache(maxsize=1)
def _jinja_env() -> _JinjaEnvironment:
    from jinja2 import StrictUndefined  # noqa: PLC0415
    from jinja2.exceptions import TemplateRuntimeError  # noqa: PLC0415
    from jinja2.nativetypes import NativeCodeGenerator, native_concat  # noqa: PLC0415
    from jinja2.runtime import Context  # noqa: PLC0415
    from jinja2.sandbox import SandboxedEnvironment  # noqa: PLC0415

    class _NativeSandboxedEnvironment(SandboxedEnvironment):
        code_generator_class = NativeCodeGenerator
        concat = staticmethod(native_concat)  # type: ignore[assignment]
        intercepted_binops = frozenset({"*"})

        def call_binop(
            self,
            context: Context,
            operator: str,
            left: object,
            right: object,
        ) -> object:
            if operator == "*":
                _ensure_repetition_within_bound(left, right, TemplateRuntimeError)
            return super().call_binop(context, operator, left, right)

    return _NativeSandboxedEnvironment(autoescape=False, undefined=StrictUndefined)


def _ensure_repetition_within_bound(
    left: object,
    right: object,
    error_type: type[Exception],
) -> None:
    sequence: object
    count: int
    if isinstance(left, int) and isinstance(right, str | bytes | list | tuple):
        count = left
        sequence = right
    elif isinstance(right, int) and isinstance(left, str | bytes | list | tuple):
        count = right
        sequence = left
    else:
        return
    if max(count, 0) * len(sequence) > MAX_DERIVED_VALUE_LENGTH:
        raise error_type(
            f"derived input value exceeds maximum length of {MAX_DERIVED_VALUE_LENGTH}"
        )


def _ensure_value_within_bound(value: object) -> None:
    if isinstance(value, str | bytes | list | tuple | dict):
        length = len(value)
    else:
        return
    if length > MAX_DERIVED_VALUE_LENGTH:
        raise InputSourceError(
            f"derived input value exceeds maximum length of {MAX_DERIVED_VALUE_LENGTH}"
        )


def _error_message(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return str(message or exc)
