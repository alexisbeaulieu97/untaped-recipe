"""Tests for the public hook authoring API contract."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

from untaped_recipe.infrastructure import hook_library


def _release_module() -> object:
    module_path = Path(__file__).parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("release", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_hook_api_exposes_helper_types() -> None:
    from untaped_recipe.hook_api import (
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
    from untaped_recipe.hook_api import HOOK_API_VERSION

    root = Path(__file__).parents[1]
    package_version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    package_major_minor = ".".join(package_version.split(".")[:2])
    contract_major_minor = ".".join(HOOK_API_VERSION.split(".")[:2])
    project_requirement, dev_requirement = hook_library.hook_api_requirements(
        package_version=package_version,
        hook_api_version=HOOK_API_VERSION,
    )

    assert f">={contract_major_minor}" == project_requirement
    assert f"untaped-recipe>={package_major_minor}" == dev_requirement
    assert project_requirement == hook_library._HOOK_API_PROJECT_REQUIREMENT
    assert dev_requirement == hook_library._HOOK_API_DEV_REQUIREMENT


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
