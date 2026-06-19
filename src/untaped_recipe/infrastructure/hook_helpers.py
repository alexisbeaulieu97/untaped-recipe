"""Helper object passed to trusted recipe hooks."""

from __future__ import annotations

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

    def render_template(self, template: str, inputs: dict[str, object]) -> str:
        """Render simple recipe placeholders."""
        return render_template(template, inputs)

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""
        return load_yaml(content)

    def dump_yaml(self, data: object) -> str:
        """Round-trip-dump YAML data."""
        return dump_yaml(data)
