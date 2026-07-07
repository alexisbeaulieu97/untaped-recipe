"""Small template renderer for recipe values and template steps."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.domain.recipe import InputSpec

_TOKEN_RE = re.compile(r"{{.*?}}")
_BARE_TOKEN_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_UNKNOWN_TOKEN_MODES = {"error", "keep"}


def render_template(
    template: str,
    inputs: Mapping[str, object],
    *,
    unknown_tokens: str = "error",
) -> str:
    """Render ``{{ name }}`` placeholders from resolved recipe inputs."""
    if unknown_tokens not in _UNKNOWN_TOKEN_MODES:
        raise ValueError("unknown_tokens must be 'error' or 'keep'")

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        bare_token = _BARE_TOKEN_RE.fullmatch(token)
        if bare_token is not None:
            name = bare_token.group(1)
            if name in inputs:
                value = inputs[name]
                if _is_structured_value(value):
                    raise ValueError(_structured_render_error(name))
                return str(value)
            if unknown_tokens == "keep":
                return token
            raise ValueError(f"template input {name!r} is not defined")
        if unknown_tokens == "keep":
            return token
        raise ValueError(
            f"template token {token!r} is not a bare input name; "
            "set unknown_tokens: keep to pass it through"
        )

    return _TOKEN_RE.sub(_replace, template)


def render_field(
    text: str,
    *,
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
    field: str,
) -> str:
    """Render a path-bearing recipe field using strict bare input tokens."""
    for match in _TOKEN_RE.finditer(text):
        bare_token = _BARE_TOKEN_RE.fullmatch(match.group(0))
        if bare_token is None:
            continue
        name = bare_token.group(1)
        spec = specs.get(name)
        if spec is None:
            continue
        if spec.sensitive:
            raise ValueError(f"sensitive input {name!r} cannot be used in path field {field!r}")
        if spec.type in {"list", "dict"}:
            raise ValueError(_structured_render_error(name))
    return render_template(text, values, unknown_tokens="error")


def _is_structured_value(value: object) -> bool:
    return isinstance(value, Mapping | list | tuple)


def _structured_render_error(name: str) -> str:
    return f"structured input {name!r} cannot be rendered; hooks receive it natively"
