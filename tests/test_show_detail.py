"""Tests for structured show detail records."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.cli.detail import hook_detail, pack_detail, recipe_detail
from untaped_recipe.domain.hook_exports import hook_exports
from untaped_recipe.domain.pack import PackManifest
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

pytestmark = pytest.mark.usefixtures("isolate_config")


def _write_detail_pack(root: Path) -> None:
    recipe_path = root / "recipes" / "playbook" / "recipe.yml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    (recipe_path.parent / "templates").mkdir()
    (recipe_path.parent / "templates" / "config.yml").write_text("owner: {{ owner }}\n")
    recipe_path.write_text(
        "version: 1\n"
        "description: |\n"
        "  Configure ownership.\n"
        "  Extra detail stays out of pack summaries.\n"
        "inputs:\n"
        "  owner:\n"
        "    type: str\n"
        "    required: true\n"
        "    description: Owner name\n"
        "  token:\n"
        "    type: str\n"
        "    sensitive: true\n"
        "steps:\n"
        "  - type: template\n"
        "    template: templates/config.yml\n"
        "    dest: config.yml\n"
        "  - type: transform\n"
        "    file: config.yml\n"
        "    hook: set_owner\n"
        "  - type: validate\n"
        "    hook: check_owner\n",
        encoding="utf-8",
    )
    hooks_dir = root / "src" / "ansible_pack" / "hooks"
    hooks_dir.mkdir(parents=True)
    (root / "src" / "ansible_pack" / "__init__.py").write_text("", encoding="utf-8")
    (hooks_dir / "__init__.py").write_text("", encoding="utf-8")
    (hooks_dir / "set_owner.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content\n"
        "def validate(*, inputs, target, args, helpers):\n"
        "    return helpers.pass_()\n",
        encoding="utf-8",
    )
    (hooks_dir / "check_owner.py").write_text(
        "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
        encoding="utf-8",
    )
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-ansible"\n'
        'version = "0.2.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"playbook" = { path = "recipes/playbook/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"set_owner" = { module = "ansible_pack.hooks.set_owner" }\n'
        '"check_owner" = { module = "ansible_pack.hooks.check_owner" }\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def test_recipe_detail_lists_inputs_steps_and_hooks(tmp_path: Path) -> None:
    _write_detail_pack(tmp_path)
    recipe_path = tmp_path / "recipes" / "playbook" / "recipe.yml"

    detail = recipe_detail("ansible/playbook", load_recipe_file(recipe_path), recipe_path)

    assert detail["inputs"] == [
        {
            "name": "owner",
            "type": "str",
            "required": True,
            "default": None,
            "description": "Owner name",
            "sensitive": False,
        },
        {
            "name": "token",
            "type": "str",
            "required": False,
            "default": None,
            "description": "",
            "sensitive": True,
        },
    ]
    assert {"type": "transform", "file_or_files": "config.yml", "hook": "set_owner"} in detail[
        "steps"
    ]
    assert {"type": "validate", "file_or_files": "", "hook": "check_owner"} in detail["steps"]
    assert detail["hooks"] == ["check_owner", "set_owner"]
    assert detail["path"] == str(recipe_path)


def test_hook_detail_reports_ast_exports(tmp_path: Path) -> None:
    _write_detail_pack(tmp_path)
    manifest = PackManifest.from_pyproject(tmp_path)
    module_file = tmp_path / "src" / "ansible_pack" / "hooks" / "set_owner.py"

    detail = hook_detail(
        "ansible/set_owner",
        manifest.hooks["set_owner"],
        hook_exports(module_file),
        module_file,
    )

    assert detail == {
        "ref": "ansible/set_owner",
        "module": "ansible_pack.hooks.set_owner",
        "exports": ["transform", "validate"],
        "path": str(module_file),
    }


def test_pack_detail_lists_recipe_summaries_and_hook_exports(tmp_path: Path) -> None:
    _write_detail_pack(tmp_path)
    manifest = PackManifest.from_pyproject(tmp_path)

    detail = pack_detail("alias", manifest, tmp_path)

    assert detail["name"] == "alias"
    assert detail["project"] == "untaped-recipe-ansible"
    assert detail["version"] == "0.2.0"
    assert detail["recipes"] == [{"name": "playbook", "description": "Configure ownership."}]
    assert {"name": "set_owner", "exports": ["transform", "validate"]} in detail["hooks"]
    assert detail["path"] == str(tmp_path)


def test_show_recipe_cli_emits_structured_recipe_record(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_detail_pack(source)
    library = library_root()
    (library / "packs").mkdir(parents=True)
    shutil.copytree(source, library / "packs" / "ansible")

    result = CliInvoker().invoke(app, ["show", "ansible/playbook", "--format", "json"])

    assert result.exit_code == 0, result.output
    detail = json.loads(result.stdout)
    assert detail["ref"] == "ansible/playbook"
    assert detail["inputs"][0]["name"] == "owner"
    assert detail["inputs"][0]["required"] is True
