"""Small template renderer for recipe values and template steps."""

from __future__ import annotations

import re

_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def render_template(template: str, inputs: dict[str, object]) -> str:
    """Render ``{{ name }}`` placeholders from resolved recipe inputs."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in inputs:
            raise ValueError(f"template input {name!r} is not defined")
        return str(inputs[name])

    return _PLACEHOLDER_RE.sub(_replace, template)
