"""Helper object passed to trusted recipe hooks."""

from __future__ import annotations

from collections.abc import Mapping

from untaped_recipe.domain.plan import Verdict
from untaped_recipe.domain.templates import render_template
from untaped_recipe.infrastructure.ruamel_io import dump_yaml, load_yaml


class HookHelpers:
    """Helpers available to trusted local hooks.

    One instance is created per hook invocation so ``warn`` can accumulate
    warnings for a single target without leaking across targets or threads.
    """

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def pass_(self, message: str = "") -> Verdict:
        """Return a passing validation verdict."""
        return Verdict(status="pass", message=message)

    def fail(self, message: str) -> Verdict:
        """Return a failing validation verdict."""
        return Verdict(status="fail", message=message)

    def skip(self, message: str = "") -> Verdict:
        """Return a skip verdict marking the target not applicable."""
        return Verdict(status="skip", message=message)

    def warn(self, message: str) -> None:
        """Accumulate a non-fatal warning for the current target."""
        self._warnings.append(message)

    def drain_warnings(self) -> tuple[str, ...]:
        """Return and clear warnings accumulated during this invocation."""
        drained = tuple(self._warnings)
        self._warnings.clear()
        return drained

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
