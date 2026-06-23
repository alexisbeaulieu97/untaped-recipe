"""Application-layer ports for filesystem and hook adapters."""

from __future__ import annotations

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
    ) -> str:
        """Run a transform hook and return replacement content."""

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> Verdict:
        """Run a validate hook and return its coerced verdict."""


class HookDebugExecutorPort(HookExecutorPort, Protocol):
    """Execute trusted hooks and expose successful diagnostics for debugging."""

    def transform_for_debug(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> HookDebugResult[str]:
        """Run a transform hook and return replacement content plus diagnostics."""

    def validate_for_debug(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
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

    def dump_yaml(self, data: object) -> str:
        """Round-trip-dump YAML data."""
