"""Helper object passed to trusted recipe hooks."""

from __future__ import annotations

from collections.abc import Mapping

from untaped_recipe.domain.plan import Verdict
from untaped_recipe.domain.templates import render_template
from untaped_recipe.infrastructure.ruamel_io import dump_yaml, load_yaml


class HookHelpers:
    """Helpers available to trusted local hooks."""

    def pass_(self, message: str = "") -> Verdict:
        """Return a passing validation verdict."""
        return Verdict(status="pass", message=message)

    def warn(self, message: str) -> Verdict:
        """Return a warning validation verdict."""
        return Verdict(status="warn", message=message)

    def fail(self, message: str) -> Verdict:
        """Return a failing validation verdict."""
        return Verdict(status="fail", message=message)

    def render_template(
        self,
        template: str,
        inputs: dict[str, object],
        *,
        unknown_tokens: str = "error",
    ) -> str:
        """Render simple recipe placeholders."""
        return render_template(template, inputs, unknown_tokens=unknown_tokens)

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""
        return load_yaml(content)

    def dump_yaml(self, data: object, *, options: Mapping[str, object] | None = None) -> str:
        """Round-trip-dump YAML data."""
        return dump_yaml(data, options=options)
