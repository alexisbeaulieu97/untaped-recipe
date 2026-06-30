"""Compatibility re-exports for external recipe hook author types."""

from __future__ import annotations

from typing import TYPE_CHECKING

from untaped_recipe_hook_api import (
    HOOK_API_VERSION,
    HookHelpers,
    YamlDumpOptions,
    YamlIndentOptions,
)

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
