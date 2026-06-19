"""Application-layer ports for filesystem and hook adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from untaped_recipe.domain.plan import Verdict


class HookExecutorPort(Protocol):
    """Execute trusted hooks through their resolved runtime."""

    def transform(
        self,
        hook: str,
        content: str,
        *,
        recipe_dir: Path,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> str:
        """Run a transform hook and return replacement content."""

    def validate(
        self,
        hook: str,
        *,
        recipe_dir: Path,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> Verdict:
        """Run a validate hook and return its coerced verdict."""


class HookHelpersPort(Protocol):
    """Helpers passed to trusted local hooks."""

    def pass_(self, message: str = "") -> Verdict:
        """Return a passing validation verdict."""

    def warn(self, message: str) -> Verdict:
        """Return a warning validation verdict."""

    def fail(self, message: str) -> Verdict:
        """Return a failing validation verdict."""

    def render_template(self, template: str, inputs: dict[str, object]) -> str:
        """Render simple recipe placeholders."""

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""

    def dump_yaml(self, data: object) -> str:
        """Round-trip-dump YAML data."""
