"""Public typing helpers for external recipe hook authors."""

from __future__ import annotations

from typing import Protocol, TypedDict


class YamlIndentOptions(TypedDict, total=False):
    """Indentation options passed to ``helpers.dump_yaml``."""

    mapping: int
    sequence: int
    offset: int


class YamlDumpOptions(TypedDict, total=False):
    """YAML dump formatting options accepted by external hook helpers."""

    width: int
    preserve_quotes: bool
    indent: YamlIndentOptions
    block_seq_indent: int
    explicit_start: bool
    explicit_end: bool


class HookHelpers(Protocol):
    """Helper methods available to external hook projects."""

    def pass_(self, message: str = "") -> dict[str, str]:
        """Return a passing validation verdict."""

    def warn(self, message: str) -> dict[str, str]:
        """Return a warning validation verdict."""

    def fail(self, message: str) -> dict[str, str]:
        """Return a failing validation verdict."""

    def render_template(self, template: str, inputs: dict[str, object]) -> str:
        """Render simple recipe placeholders."""

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""

    def dump_yaml(self, data: object, *, options: YamlDumpOptions | None = None) -> str:
        """Round-trip-dump YAML data with optional formatting controls."""
