"""Public hook authoring contract for ``untaped-recipe`` hook projects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, TypedDict

HOOK_API_VERSION = "0.10.0"


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

    def fail(self, message: str) -> dict[str, str]:
        """Return a failing validation verdict."""

    def skip(self, message: str = "") -> dict[str, str]:
        """Return a skip verdict marking the target not applicable."""

    def warn(self, message: str) -> None:
        """Accumulate a non-fatal warning for the current target."""

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
        """Round-trip-dump YAML data with optional formatting controls."""


__all__ = ["HOOK_API_VERSION", "HookHelpers", "YamlDumpOptions", "YamlIndentOptions"]


if TYPE_CHECKING:

    def _plain_mapping_options_are_supported(
        helpers: HookHelpers,
        options: dict[str, object],
    ) -> str:
        return helpers.dump_yaml({}, options=options)

    def _typed_options_are_supported(
        helpers: HookHelpers,
        options: YamlDumpOptions,
    ) -> str:
        return helpers.dump_yaml({}, options=options)
