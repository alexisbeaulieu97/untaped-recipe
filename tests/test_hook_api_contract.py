"""Tests for the public hook authoring API contract package."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from untaped_recipe.infrastructure import hook_library


def _release_module() -> object:
    module_path = Path(__file__).parents[1] / "scripts" / "hook_api_release.py"
    spec = importlib.util.spec_from_file_location("hook_api_release", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_hook_api_versions_and_scaffold_floor_stay_in_sync() -> None:
    from untaped_recipe_hook_api import HOOK_API_VERSION

    root = Path(__file__).parents[1]
    root_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    hook_api_version = tomllib.loads(
        (root / "packages" / "hook-api" / "pyproject.toml").read_text()
    )["project"]["version"]
    major_minor = ".".join(HOOK_API_VERSION.split(".")[:2])

    assert root_version == hook_api_version == HOOK_API_VERSION
    assert f">={major_minor}" == hook_library._HOOK_API_PROJECT_REQUIREMENT
    assert (f"untaped-recipe-hook-api>={major_minor},<1") == hook_library._HOOK_API_DEV_REQUIREMENT


def test_release_script_verifies_version_parity() -> None:
    module = _release_module()

    module.verify_versions("0.8.0")


def test_release_script_rejects_version_mismatch() -> None:
    module = _release_module()

    try:
        module.verify_versions("0.8.1")
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected version parity failure")
