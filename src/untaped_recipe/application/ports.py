"""Application-layer ports for filesystem and hook adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from untaped_recipe.domain.plan import Verdict


@dataclass(frozen=True)
class HookDebugResult[T]:
    """Hook result plus diagnostics and accumulated warnings for one invocation."""

    result: T
    diagnostics: str
    warnings: tuple[str, ...] = ()


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
    """Helpers passed to trusted local hooks (one instance per invocation)."""

    def pass_(self, message: str = "") -> Verdict:
        """Return a passing validation verdict."""

    def fail(self, message: str) -> Verdict:
        """Return a failing validation verdict."""

    def skip(self, message: str = "") -> Verdict:
        """Return a skip verdict marking the target not applicable."""

    def warn(self, message: str) -> None:
        """Accumulate a non-fatal warning for the current target."""

    def drain_warnings(self) -> tuple[str, ...]:
        """Return and clear warnings accumulated during this invocation."""

    def render_template(
        self,
        template: str,
        inputs: dict[str, object],
        *,
        unknown_tokens: str = "error",
    ) -> str:
        """Render simple recipe placeholders."""

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""

    def dump_yaml(self, data: object, *, options: Mapping[str, object] | None = None) -> str:
        """Round-trip-dump YAML data."""
