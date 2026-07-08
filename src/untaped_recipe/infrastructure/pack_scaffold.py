"""Scaffold 0.9 recipe packs, recipes, and hooks."""

from __future__ import annotations

import shutil
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Literal

import tomlkit
from packaging.version import Version
from tomlkit import TOMLDocument

from untaped_recipe._version import PACKAGE_VERSION
from untaped_recipe.domain.hook_project import HookKind, normalize_hook_name
from untaped_recipe.domain.pack import PackManifest
from untaped_recipe.domain.paths import safe_library_name
from untaped_recipe.domain.project_toml import read_toml_document, toml_table
from untaped_recipe.hook_api import HOOK_API_VERSION
from untaped_recipe.infrastructure.uv_project import lock_project


def hook_api_requirements(
    *,
    package_version: str = PACKAGE_VERSION,
    hook_api_version: str = HOOK_API_VERSION,
) -> tuple[str, str]:
    """Return the hook API project floor and type-discovery dependency.

    The dev dependency exists only so hook authors get editor access to the
    public hook API. Its floor tracks the helper API contract, which changes
    rarely, rather than the CLI release cadence.
    """
    Version(package_version)
    hook_api = Version(hook_api_version)
    project_requirement = f">={hook_api.major}.{hook_api.minor},<{hook_api.major + 1}"
    dev_requirement = f"untaped-recipe>={hook_api.major}.{hook_api.minor}"
    return project_requirement, dev_requirement


_HOOK_API_PROJECT_REQUIREMENT, _HOOK_API_DEV_REQUIREMENT = hook_api_requirements()


class ScaffoldLockError(ValueError):
    """Raised when scaffold files were written but ``uv.lock`` could not refresh."""


_CASE_YML_TEMPLATE = """\
# Golden test case for this recipe (run with: untaped-recipe test <pack>/<recipe>).
# Sibling directories:
#   given/    - fixture target directory the plan runs against
#   expected/ - full expected tree after the plan; omit to assert no changes
# Every field below is optional.
#
# inputs:                     # recipe inputs, same names and types apply accepts
#   owner: platform-team
# expect: success             # success (default) | error
# error_contains: "..."       # required with expect: error; forbidden otherwise
# verdict:                    # assertions on validate-hook verdicts
#   status: skip              # expected worst status: pass | fail | skip
#   message_contains: "..."   # substring of at least one verdict message
"""


def scaffold_pack(dest: Path, name: str, *, lock: bool = True) -> Path:
    """Create a new uv recipe pack project at ``dest``."""
    pack_name = safe_library_name(name, field="pack")
    if dest.exists():
        raise ValueError(f"pack already exists: {dest}")
    package = _package_name(pack_name)
    try:
        (dest / "src" / package / "hooks").mkdir(parents=True)
        (dest / "src" / package / "__init__.py").write_text("", encoding="utf-8")
        (dest / "src" / package / "hooks" / "__init__.py").write_text("", encoding="utf-8")
        (dest / "pyproject.toml").write_text(
            "[project]\n"
            f'name = "untaped-recipe-{pack_name}"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.14"\n'
            "dependencies = []\n\n"
            "[dependency-groups]\n"
            f'dev = ["{_HOOK_API_DEV_REQUIREMENT}", "pytest"]\n\n'
            "[tool.pytest.ini_options]\n"
            'pythonpath = ["src"]\n\n'
            "[tool.untaped_recipe]\n"
            f'requires_hook_api = "{_HOOK_API_PROJECT_REQUIREMENT}"\n',
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if lock:
        try:
            lock_project(dest)
        except Exception as exc:
            raise _lock_error(
                created_label="recipe pack",
                created_path=dest,
                project_root=dest,
                cause=exc,
            ) from exc
    return dest


def scaffold_recipe(pack_dir: Path, name: str, *, lock: bool = True) -> Path:
    """Add a generated recipe plus a starter golden case to a pack."""
    recipe_name = safe_library_name(name, field="recipe")
    manifest = PackManifest.from_pyproject(pack_dir)
    if recipe_name in manifest.recipes:
        raise ValueError(f"recipe already exists: {recipe_name}")
    recipe_path = pack_dir / "recipes" / recipe_name / "recipe.yml"
    if recipe_path.exists():
        raise ValueError(f"recipe already exists: {recipe_name}")
    tests_dir = pack_dir / "tests"
    tests_root = tests_dir / recipe_name
    case_dir = tests_root / "basic"
    if case_dir.exists():
        raise ValueError(f"recipe tests already exist: {recipe_name}")
    if not tests_dir.exists():
        created_tests_path = tests_dir
    elif not tests_root.exists():
        created_tests_path = tests_root
    else:
        created_tests_path = case_dir
    try:
        recipe_path.parent.mkdir(parents=True)
        recipe_path.write_text(
            "version: 1\n"
            "description: ''\n"
            "inputs: {}\n"
            "steps: []\n"
            "\n"
            "# Example validate step:\n"
            "# - type: validate\n"
            "#   hook: check\n",
            encoding="utf-8",
        )
        (case_dir / "given").mkdir(parents=True)
        (case_dir / "case.yml").write_text(_CASE_YML_TEMPLATE, encoding="utf-8")
        _append_recipe_row(
            pack_dir / "pyproject.toml",
            recipe_name,
            recipe_path.relative_to(pack_dir),
        )
    except Exception:
        _remove_manifest_row(pack_dir / "pyproject.toml", "recipes", recipe_name)
        shutil.rmtree(recipe_path.parent, ignore_errors=True)
        shutil.rmtree(created_tests_path, ignore_errors=True)
        raise
    if lock:
        try:
            lock_project(pack_dir)
        except Exception as exc:
            raise _lock_error(
                created_label="recipe",
                created_path=recipe_path,
                project_root=pack_dir,
                cause=exc,
            ) from exc
    return recipe_path


def scaffold_hook(
    pack_dir: Path,
    name: str,
    *,
    kind: HookKind = "transform",
    lock: bool = True,
    force: bool = False,
) -> Path:
    """Add a hook module stub to an existing pack manifest.

    ``force`` re-scaffolds an existing hook, replacing both the module stub and
    the paired pytest for the requested ``kind`` (e.g. to fix a wrong default
    kind). Without ``force`` an existing hook, module, or test is refused.
    """
    hook_name = normalize_hook_name(name)
    manifest = PackManifest.from_pyproject(pack_dir)
    already_registered = hook_name in manifest.hooks
    if already_registered and not force:
        raise ValueError(f"hook already exists: {hook_name}")
    package = _package_name(manifest.name)
    module_leaf = hook_name.rsplit(".", maxsplit=1)[-1]
    module = f"{package}.hooks.{module_leaf}"
    module_path = pack_dir / "src" / package / "hooks" / f"{module_leaf}.py"
    tests_dir = pack_dir / "tests"
    test_path = tests_dir / f"test_hook_{module_leaf}.py"
    if not force:
        if module_path.exists():
            raise ValueError(f"hook already exists: {hook_name}")
        if test_path.exists():
            raise ValueError(f"hook test already exists: {test_path}")
    created_tests_dir = not tests_dir.exists()
    try:
        module_path.parent.mkdir(parents=True, exist_ok=True)
        (pack_dir / "src" / package / "__init__.py").touch()
        (pack_dir / "src" / package / "hooks" / "__init__.py").touch()
        module_path.write_text(_hook_stub(kind), encoding="utf-8")
        tests_dir.mkdir(exist_ok=True)
        test_path.write_text(_hook_test_stub(kind, module), encoding="utf-8")
        if not already_registered:
            _append_hook_row(pack_dir / "pyproject.toml", hook_name, module)
    except Exception:
        # Only clean up a fresh scaffold; a forced re-scaffold must not delete
        # the pre-existing hook it is replacing.
        if not already_registered:
            _remove_manifest_row(pack_dir / "pyproject.toml", "hooks", hook_name)
            module_path.unlink(missing_ok=True)
            test_path.unlink(missing_ok=True)
            if created_tests_dir:
                shutil.rmtree(tests_dir, ignore_errors=True)
        raise
    if lock:
        try:
            lock_project(pack_dir)
        except Exception as exc:
            raise _lock_error(
                created_label="hook module",
                created_path=module_path,
                project_root=pack_dir,
                cause=exc,
            ) from exc
    return module_path


_HOOK_STUB_PREAMBLE = (
    "from pathlib import Path\n"
    "from typing import TYPE_CHECKING\n"
    "\n"
    "if TYPE_CHECKING:\n"
    "    from untaped_recipe.hook_api import HookHelpers\n"
    "\n"
    "\n"
)


def _hook_stub(kind: Literal["transform", "validate"]) -> str:
    if kind == "validate":
        return (
            _HOOK_STUB_PREAMBLE + "def validate(\n"
            "    *,\n"
            "    inputs: dict[str, object],\n"
            "    target: Path,\n"
            "    args: dict[str, object],\n"
            '    helpers: "HookHelpers",\n'
            ") -> object:\n"
            "    return helpers.pass_()\n"
        )
    return (
        _HOOK_STUB_PREAMBLE + "def transform(\n"
        "    content: str,\n"
        "    *,\n"
        "    inputs: dict[str, object],\n"
        "    target: Path,\n"
        "    file: Path,\n"
        "    args: dict[str, object],\n"
        '    helpers: "HookHelpers",\n'
        ") -> str:\n"
        "    return content\n"
    )


def _hook_test_stub(kind: Literal["transform", "validate"], module: str) -> str:
    if kind == "validate":
        return (
            "from pathlib import Path\n"
            "\n"
            "from untaped_recipe.hook_worker import HookHelpers\n"
            "\n"
            f"from {module} import validate\n"
            "\n"
            "\n"
            "def test_validate_returns_pass_verdict() -> None:\n"
            '    result = validate(inputs={}, target=Path("."), args={}, helpers=HookHelpers())\n'
            "\n"
            "    assert result == HookHelpers().pass_()\n"
        )
    return (
        "from pathlib import Path\n"
        "\n"
        "from untaped_recipe.hook_worker import HookHelpers\n"
        "\n"
        f"from {module} import transform\n"
        "\n"
        "\n"
        "def test_transform_returns_content_unchanged() -> None:\n"
        "    result = transform(\n"
        '        "hello\\n",\n'
        "        inputs={},\n"
        '        target=Path("."),\n'
        '        file=Path("example.txt"),\n'
        "        args={},\n"
        "        helpers=HookHelpers(),\n"
        "    )\n"
        "\n"
        '    assert result == "hello\\n"\n'
    )


def _append_recipe_row(pyproject: Path, name: str, relative_path: Path) -> None:
    doc = read_toml_document(pyproject)
    recipes = _manifest_table(doc, "recipes")
    entry = tomlkit.inline_table()
    entry["path"] = relative_path.as_posix()
    recipes[name] = entry
    pyproject.write_text(doc.as_string(), encoding="utf-8")


def _append_hook_row(pyproject: Path, name: str, module: str) -> None:
    doc = read_toml_document(pyproject)
    hooks = _manifest_table(doc, "hooks")
    entry = tomlkit.inline_table()
    entry["module"] = module
    hooks[name] = entry
    pyproject.write_text(doc.as_string(), encoding="utf-8")


def _remove_manifest_row(pyproject: Path, table_name: str, name: str) -> None:
    doc = read_toml_document(pyproject)
    tool = toml_table(doc, "tool", "tool", create=False)
    if tool is None:
        return
    untaped = toml_table(tool, "untaped_recipe", "tool.untaped_recipe", create=False)
    if untaped is None:
        return
    table = toml_table(
        untaped,
        table_name,
        f"tool.untaped_recipe.{table_name}",
        create=False,
    )
    if table is None:
        return
    table.pop(name, None)
    pyproject.write_text(doc.as_string(), encoding="utf-8")


def _lock_error(
    *,
    created_label: str,
    created_path: Path,
    project_root: Path,
    cause: Exception,
) -> ScaffoldLockError:
    detail = str(cause).strip() or cause.__class__.__name__
    return ScaffoldLockError(
        f"created {created_label} at {created_path}, but uv lock failed: {detail}; "
        "fix the index or add a [tool.uv.sources] override, then run "
        f"`uv lock` in {project_root}"
    )


def _manifest_table(doc: TOMLDocument, table_name: str) -> MutableMapping[str, Any]:
    tool = toml_table(doc, "tool", "tool", create=True)
    untaped = toml_table(tool, "untaped_recipe", "tool.untaped_recipe", create=True)
    return toml_table(
        untaped,
        table_name,
        f"tool.untaped_recipe.{table_name}",
        create=True,
    )


def _package_name(pack_name: str) -> str:
    return pack_name.replace("-", "_").replace(".", "_") + "_pack"
