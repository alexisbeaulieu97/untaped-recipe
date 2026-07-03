"""Small template renderer for recipe values and template steps."""

from __future__ import annotations

import re
from collections.abc import Mapping

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
                return str(inputs[name])
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
