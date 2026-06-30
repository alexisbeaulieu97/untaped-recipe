"""Tests for the public hook authoring API contract package."""

from __future__ import annotations


def test_hook_api_contract_package_exposes_helper_types() -> None:
    from untaped_recipe_hook_api import (
        HOOK_API_VERSION,
        HookHelpers,
        YamlDumpOptions,
        YamlIndentOptions,
    )

    indent: YamlIndentOptions = {"mapping": 2, "sequence": 4, "offset": 2}
    options: YamlDumpOptions = {"width": 120, "indent": indent}

    assert HOOK_API_VERSION == "0.8.0"
    assert options["indent"]["sequence"] == 4
    assert HookHelpers.__name__ == "HookHelpers"
