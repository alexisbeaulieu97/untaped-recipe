"""Application-layer ports for filesystem and hook adapters."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Protocol

from untaped_recipe.domain.plan import Verdict


class HookLoaderPort(Protocol):
    """Load a trusted hook module by name or path."""

    def load(self, name: str, recipe_dir: Path) -> ModuleType:
        """Return a Python module containing a transform or validate callable."""


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
