"""Application-layer ports for filesystem and hook adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from untaped_recipe.domain.plan import Verdict


@dataclass(frozen=True)
class HookDebugResult[T]:
    """Hook result plus diagnostics captured for one debug invocation."""

    result: T
    diagnostics: str


class HookExecutorPort(Protocol):
    """Execute trusted hooks through their resolved runtime."""

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        """Run a transform hook and return replacement content plus diagnostics."""

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        """Run a validate hook and return its coerced verdict plus diagnostics."""


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

    def dump_yaml(self, data: object, *, options: Mapping[str, object] | None = None) -> str:
        """Round-trip-dump YAML data."""
