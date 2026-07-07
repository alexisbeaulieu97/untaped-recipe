"""CLI tests for apply, libraries, and backup restore."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from untaped.api import build_tool_app, invalidate_settings_cache
from untaped.testing import CliInvoker, assert_destructive_contract

import untaped_recipe.infrastructure.file_writer as file_writer_module
from untaped_recipe import app
from untaped_recipe.__main__ import SPEC
from untaped_recipe.builtins.registry import BUILTIN_HOOKS, BuiltinHook
from untaped_recipe.cli.common import library_root
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.infrastructure.backup import BackupStore
from untaped_recipe.infrastructure.pack_store import PackLibrary

pytestmark = pytest.mark.usefixtures("isolate_config")


class _DeclineUi:
    stdin = object()

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return False

    def progress(self, message: str) -> _DeclineUi:
        return self

    def update(self, label: str, *, fraction: float | None = None) -> None:
        pass

    def message(self, kind: str, message: str) -> None:
        print(message, file=sys.stderr)

    def __enter__(self) -> _DeclineUi:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def _write_hook_project(
    root: Path,
    *,
    public_name: str,
    module_name: str,
    code: str,
    package: str = "recipe_hooks",
) -> None:
    module_path = root / "src" / package / "hooks" / f"{module_name}.py"
    module_path.parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    module_path.write_text(code)
    recipe_metadata = (
        '[tool.untaped_recipe.recipes]\n"demo" = { path = "recipe.yml" }\n\n'
        if (root / "recipe.yml").is_file()
        else ""
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{root.name}-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        f"{recipe_metadata}"
        "[tool.untaped_recipe.hooks]\n"
        f'"{public_name}" = {{ module = "{package}.hooks.{module_name}" }}\n'
    )
    subprocess.run(["uv", "lock"], cwd=root, check=True)


def _write_pack_project(root: Path) -> None:
    hook_module = root / "src" / "demo_hooks" / "hooks" / "check.py"
    hook_module.parent.mkdir(parents=True, exist_ok=True)
    (root / "src" / "demo_hooks" / "__init__.py").write_text("")
    (root / "src" / "demo_hooks" / "hooks" / "__init__.py").write_text("")
    hook_module.write_text(
        "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n"
    )
    recipe = root / "recipes" / "demo" / "recipe.yml"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("version: 1\nsteps: []\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"demo-recipe" = { path = "recipes/demo/recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"demo_hook" = { module = "demo_hooks.hooks.check" }\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


def test_add_pack_prints_recipes_and_hooks_before_confirm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)

    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr("untaped_recipe.cli.commands.ui_context", lambda **kwargs: _DeclineUi())
    result = CliInvoker().invoke(app, ["add", str(pack)])

    assert result.exit_code == 0, result.output
    assert "demo-recipe" in result.stderr
    assert "demo_hook" in result.stderr
    assert not (library_root() / "packs" / "demo").exists()


def test_add_force_fails_fast_on_local_edits_before_confirm(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = CliInvoker().invoke(app, ["add", str(pack), "--force", "--yes"])

    assert result.exit_code == 1
    assert "pack 'demo' has local edits in the library" in result.stderr
    assert "--discard-edits" in result.stderr
    assert "Pack: demo" not in result.stderr
    assert installed_recipe.read_text().startswith("version: 1\ndescription: 'edited'")


def test_add_force_discard_edits_warns_in_preview_and_overwrites(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = CliInvoker().invoke(
        app,
        ["add", str(pack), "--force", "--discard-edits", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert "Warning: library copy has local edits; --discard-edits will overwrite them." in (
        result.stderr
    )
    assert installed_recipe.read_text() == "version: 1\nsteps: []\n"

    result = CliInvoker().invoke(app, ["add", str(pack), "--force", "--yes"])
    assert result.exit_code == 0, result.output


def test_remove_warns_on_local_edits_before_confirm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")
    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr("untaped_recipe.cli.commands.ui_context", lambda **kwargs: _DeclineUi())

    result = CliInvoker().invoke(app, ["remove", "demo"])

    assert result.exit_code == 0, result.output
    assert "About to remove 1 pack(s):\n  - demo\n" in result.stderr
    assert (
        "Warning: pack 'demo' has local edits in the library (via edit or new "
        "recipe/hook); removing discards them."
    ) in result.stderr
    assert (library_root() / "packs" / "demo").exists()


def test_remove_clean_and_legacy_rows_keep_generic_preview_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr("untaped_recipe.cli.commands.ui_context", lambda **kwargs: _DeclineUi())

    clean = CliInvoker().invoke(app, ["remove", "demo"])

    index_path = library_root() / "packs.toml"
    index_path.write_text(
        index_path.read_text(encoding="utf-8").replace("content_hash", "ignored_field"),
        encoding="utf-8",
    )
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")
    legacy = CliInvoker().invoke(app, ["remove", "demo"])

    assert clean.exit_code == 0, clean.output
    assert legacy.exit_code == 0, legacy.output
    assert clean.stderr == "About to remove 1 pack(s):\n  - demo\n"
    assert legacy.stderr == "About to remove 1 pack(s):\n  - demo\n"


def test_remove_yes_skips_local_edits_warning(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "pack"
    _write_pack_project(pack)
    result = CliInvoker().invoke(app, ["add", str(pack), "--yes"])
    assert result.exit_code == 0, result.output
    installed_recipe = library_root() / "packs" / "demo" / "recipes" / "demo" / "recipe.yml"
    installed_recipe.write_text("version: 1\ndescription: 'edited'\nsteps: []\n")

    result = CliInvoker().invoke(app, ["remove", "demo", "--yes"])

    assert result.exit_code == 0, result.output
    assert "local edits" not in result.stderr
    assert not (library_root() / "packs" / "demo").exists()


def test_apply_yes_writes_and_emits_json_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "service=api",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "service=api\n"
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "applied"
    assert "Recipe preview:" in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "Recipe apply:" in result.stderr


def test_apply_dry_run_defaults_to_table_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "dry-run"
    assert "Recipe preview:" in result.stderr
    assert "path" in result.stderr
    assert "action" in result.stderr
    assert "changes" in result.stderr
    assert "files_changed" not in result.stderr
    assert "error" not in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "create" in result.stderr
    assert "+1 -0" in result.stderr
    assert "--- a/out.txt" not in result.stderr
    assert "+++ b/out.txt" not in result.stderr


def test_apply_table_preview_counts_prefix_like_diff_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: remove\n"
        "    file: doc.yml\n"
        "  - type: template\n"
        "    template: plus.txt\n"
        "    dest: plus.txt\n"
    )
    (tmp_path / "plus.txt").write_text("++same\n++same\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "doc.yml").write_text("---\nold\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert str(target / "doc.yml") in result.stderr
    assert "+0 -2" in result.stderr
    assert str(target / "plus.txt") in result.stderr
    assert "+2 -0" in result.stderr


def test_apply_table_preview_renders_relative_target_as_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    Path("template.txt").write_text("hello\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", "./recipe.yml", str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "target" / "out.txt") in result.stderr


def test_apply_preview_diff_preserves_patch_headers(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--preview", "diff"],
    )

    assert result.exit_code == 0, result.output
    assert "Recipe preview:" in result.stderr
    assert f"# {target}" in result.stderr
    assert "--- a/out.txt" in result.stderr
    assert "+++ b/out.txt" in result.stderr
    assert str(target / "out.txt") not in result.stderr


def test_apply_diff_preview_renders_relative_target_context_as_absolute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    Path("template.txt").write_text("hello\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", "./recipe.yml", str(target), "--dry-run", "--preview", "diff"],
    )

    assert result.exit_code == 0, result.output
    assert f"# {tmp_path / 'target'}" in result.stderr
    assert "# target" not in result.stderr
    assert "--- a/out.txt" in result.stderr
    assert "+++ b/out.txt" in result.stderr


def test_apply_diff_preview_renders_sensitive_target_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token: {type: str, sensitive: true, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    Path("template.txt").write_text("token={{ token }}\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            "./recipe.yml",
            str(target),
            "--var",
            "token=secret",
            "--dry-run",
            "--preview",
            "diff",
        ],
    )

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "target") in result.stderr
    assert "files_changed" in result.stderr
    assert "out.txt" not in result.stderr
    assert "secret" not in result.stderr
    assert "diff suppressed for target with sensitive inputs" not in result.stderr
    assert "--- a/out.txt" not in result.stderr
    assert "+++ b/out.txt" not in result.stderr


def test_apply_diff_preview_renders_planning_failure_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text("version: 1\nsteps:\n  - type: validate\n    hook: noop\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", "./recipe.yml", str(target), "--dry-run", "--preview", "diff"],
    )

    assert result.exit_code == 1, result.output
    assert str(tmp_path / "target") in result.stderr
    assert "error" in result.stderr
    assert "noop" in result.stderr
    assert "--- a/" not in result.stderr
    assert "+++ b/" not in result.stderr


def test_apply_preview_none_keeps_summary_without_table_or_hunks(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--preview", "none", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "dry-run"
    assert "Recipe preview:" in result.stderr
    assert str(target / "out.txt") not in result.stderr
    assert "--- a/out.txt" not in result.stderr
    assert "+++ b/out.txt" not in result.stderr


def test_apply_preview_none_keeps_stdout_format_independent(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--dry-run",
            "--preview",
            "none",
            "--format",
            "json",
            "--columns",
            "target",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [{"target": str(target)}]
    assert "Recipe preview:" in result.stderr
    assert "out.txt" not in result.stderr


def test_apply_table_preview_reports_planning_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\nsteps:\n  - type: validate\n    hook: noop\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "Recipe preview:" in result.stderr
    assert "error" in result.stderr
    assert str(target) in result.stderr
    assert "noop" in result.stderr


def test_apply_table_preview_renders_relative_error_target_as_absolute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text("version: 1\nsteps:\n  - type: validate\n    hook: noop\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", "./recipe.yml", str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    assert str(tmp_path / "target") in result.stderr
    assert "noop" in result.stderr


def test_apply_table_preview_renders_relative_sensitive_target_as_absolute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token: {type: str, sensitive: true, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    Path("template.txt").write_text("token={{ token }}\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", "./recipe.yml", str(target), "--var", "token=secret", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert str(tmp_path / "target") in result.stderr
    assert "files_changed" in result.stderr
    assert "out.txt" not in result.stderr
    assert "secret" not in result.stderr


def test_apply_quiet_mutes_preview_summary_and_post_run_info(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()
    wired = build_tool_app(app, SPEC)

    result = CliInvoker().invoke(
        wired.meta,
        ["--quiet", "apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "Recipe preview:" not in result.stderr
    assert "Recipe dry run:" not in result.stderr


def test_apply_table_preview_uses_configured_collection_view(
    tmp_path: Path,
    isolate_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    isolate_config.write_text("profiles:\n  default:\n    ui:\n      collection_view: list\n")
    invalidate_settings_cache()
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "path:" in result.stderr
    assert "action: create" in result.stderr
    assert str(target / "out.txt") in result.stderr


def test_apply_table_preview_uses_configured_preview_max_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "500")
    monkeypatch.setenv("UNTAPED_RECIPE__PREVIEW_MAX_ROWS", "1")
    invalidate_settings_cache()
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (tmp_path / "one.txt").write_text("one\n")
    (tmp_path / "two.txt").write_text("two\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert str(target) in result.stderr
    assert "files" in result.stderr
    assert "one.txt" not in result.stderr
    assert "two.txt" not in result.stderr


def test_apply_decline_renders_cancelled_summary_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr("untaped_recipe.cli.commands.ui_context", lambda **kwargs: _DeclineUi())
    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--preview", "none"],
    )

    assert result.exit_code == 0, result.output
    assert not (target / "out.txt").exists()
    assert "Recipe apply cancelled:" in result.stderr
    assert "1 changing target not applied" in result.stderr


def test_apply_confirmation_reprints_summary_adjacent_to_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (tmp_path / "one.txt").write_text("one\n")
    (tmp_path / "two.txt").write_text("two\n")
    target = tmp_path / "target"
    target.mkdir()

    class _PromptUi(_DeclineUi):
        def confirm(self, message: str, *, default: bool = False) -> bool:
            print(f"PROMPT {message}", file=sys.stderr)
            return False

    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr("untaped_recipe.cli.commands.ui_context", lambda **kwargs: _PromptUi())

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--preview", "table"])

    assert result.exit_code == 0, result.output
    before_prompt = result.stderr.rsplit("PROMPT Continue?", maxsplit=1)[0]
    assert before_prompt.rstrip().endswith(
        "Recipe preview: 1 target, 1 changing, 0 unchanged, 0 failed, 2 files changed"
    )
    assert before_prompt.count("Recipe preview:") == 2
    assert not (target / "one.txt").exists()
    assert not (target / "two.txt").exists()


def test_confirm_accept_applies_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    class _AcceptUi(_DeclineUi):
        def confirm(self, message: str, *, default: bool = False) -> bool:
            return True

    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr("untaped_recipe.cli.commands.ui_context", lambda **kwargs: _AcceptUi())
    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--preview", "none"],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text(encoding="utf-8") == "hello\n"
    assert "Recipe apply cancelled:" not in result.stderr


def test_apply_preview_only_modes_do_not_render_cancelled_summary(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    dry_run = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])
    check = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--check"])

    assert dry_run.exit_code == 0, dry_run.output
    assert check.exit_code == 1, check.output
    assert "Recipe apply cancelled:" not in dry_run.stderr
    assert "Recipe apply cancelled:" not in check.stderr
    assert not (target / "out.txt").exists()


def test_apply_preserves_backup_when_write_rollback_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (recipe_dir / "one.txt").write_text("one-after\n")
    (recipe_dir / "two.txt").write_text("two-after\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "one.txt").write_text("one-before\n")
    (target / "two.txt").write_text("two-before\n")
    original_replace = file_writer_module.os.replace

    def fail_second_write_and_first_rollback(source: Path, dest: Path) -> None:
        source_path = Path(source)
        dest_path = Path(dest)
        if ".rollback." in source_path.name:
            raise OSError("rollback denied")
        if dest_path.name == "two.txt":
            raise OSError("write failed")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_write_and_first_rollback)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir / "recipe.yml"), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0, result.output
    assert "rollback incomplete" in result.stdout
    store = BackupStore(library_root() / "backups")
    bundles = store.list()
    assert len(bundles) == 1
    metadata = store.metadata("latest")
    assert [entry["relative_path"] for entry in metadata["files"]] == ["one.txt", "two.txt"]


def test_apply_discards_backup_when_failed_write_rolls_back_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: one.txt\n"
        "    dest: one.txt\n"
        "  - type: template\n"
        "    template: two.txt\n"
        "    dest: two.txt\n"
    )
    (recipe_dir / "one.txt").write_text("one-after\n")
    (recipe_dir / "two.txt").write_text("two-after\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "one.txt").write_text("one-before\n")
    (target / "two.txt").write_text("two-before\n")
    original_replace = file_writer_module.os.replace

    def fail_second_write(source: Path, dest: Path) -> None:
        if Path(dest).name == "two.txt":
            raise OSError("write failed")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_write)

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir / "recipe.yml"), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0, result.output
    assert BackupStore(library_root() / "backups").list() == []


def test_apply_dry_run_and_noninteractive_default_write_nothing(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    dry = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert not (target / "out.txt").exists()

    declined = CliInvoker().invoke(app, ["apply", str(recipe), str(target)])
    assert declined.exit_code != 0
    assert "requires --yes" in declined.output
    assert not (target / "out.txt").exists()


def test_apply_check_reports_drift_without_writing_or_backing_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    drift = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert drift.exit_code == 1, drift.output
    assert not (target / "out.txt").exists()
    assert BackupStore(library_root() / "backups").list() == []
    rows = json.loads(drift.stdout)
    assert rows[0]["status"] == "check"
    assert rows[0]["files_changed"] == 1
    assert "Recipe preview:" in drift.stderr
    assert str(target / "out.txt") not in drift.stderr
    assert "action" not in drift.stderr
    assert "changes" not in drift.stderr

    (target / "out.txt").write_text("hello\n")
    clean = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert clean.exit_code == 0, clean.output
    rows = json.loads(clean.stdout)
    assert rows[0]["status"] == "check"
    assert rows[0]["files_changed"] == 0
    assert "Recipe preview:" in clean.stderr
    assert str(target / "out.txt") not in clean.stderr


def test_apply_check_explicit_table_preview_reports_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--preview", "table"],
    )

    assert result.exit_code == 1, result.output
    assert "Recipe preview:" in result.stderr
    assert str(target / "out.txt") in result.stderr
    assert "action" in result.stderr
    assert "changes" in result.stderr


def test_apply_check_explicit_diff_preview_reports_drift_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    recipe = Path("recipe.yml")
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    Path("template.txt").write_text("hello\n")
    target = Path("target")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", "./recipe.yml", str(target), "--check", "--preview", "diff"],
    )

    assert result.exit_code == 1, result.output
    assert not (target / "out.txt").exists()
    assert BackupStore(library_root() / "backups").list() == []
    assert f"# {tmp_path / 'target'}" in result.stderr
    assert "--- a/out.txt" in result.stderr
    assert "+++ b/out.txt" in result.stderr


def test_apply_stdin_requires_yes_and_resolves_workspace_repo_pipe(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    repo.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {"path": str(workspace), "target_path": str(repo), "repo": "api"},
        }
    )

    refused = CliInvoker().invoke(app, ["apply", str(recipe), "--stdin"], input=payload + "\n")
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )
    assert result.exit_code == 0, result.output
    assert (repo / "out.txt").read_text() == "hello\n"


def test_apply_stdin_rejects_old_workspace_repo_pipe_without_target_path(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    workspace = tmp_path / "workspace"
    repo = workspace / "api"
    repo.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {"path": str(workspace), "repo": "api"},
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )

    assert result.exit_code != 0
    assert "workspace.repo pipe record requires target_path" in result.output
    assert "rerun or upgrade untaped-workspace" in result.output
    assert not (workspace / "out.txt").exists()
    assert not (repo / "out.txt").exists()


def test_apply_stdin_summary_only_is_noop(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.summary",
            "record": {
                "workspace": "prod",
                "path": str(workspace),
                "default_branch": "main",
                "repo_count": 0,
                "repo": "",
                "url": "",
                "repo_branch": None,
                "target_branch": None,
            },
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    assert "Recipe apply: 0 applied, 0 unchanged, 0 failed" in result.stderr
    assert not (workspace / "out.txt").exists()


def test_apply_stdin_empty_input_still_errors(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes"],
        input="",
    )

    assert result.exit_code != 0
    assert "no targets received on stdin" in result.output


def test_apply_check_allows_stdin_without_yes(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--check"],
        input=str(target) + "\n",
    )

    assert result.exit_code == 1, result.output
    assert "requires --yes" not in result.output
    assert not (target / "out.txt").exists()


def test_apply_stdin_without_yes_refuses_before_hooks_run(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    marker = tmp_path / "hook-ran"
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: touch\n"
        "    args:\n"
        f"      marker: {marker}\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="touch",
        module_name="touch",
        code=(
            "from pathlib import Path\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    Path(args['marker']).write_text('ran')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    refused = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), "--stdin"],
        input=str(target) + "\n",
    )

    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert not marker.exists()


@pytest.mark.parametrize(
    ("recipe_content", "expected"),
    [
        ("version: [\n", "invalid recipe YAML"),
        ("version: 2\nsteps: []\n", "invalid recipe"),
        ("version: 1\nname: demo\nsteps: []\n", "name"),
    ],
)
def test_apply_recipe_load_errors_are_reported_cleanly(
    tmp_path: Path,
    recipe_content: str,
    expected: str,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(recipe_content)
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--yes"])

    assert result.exit_code != 0
    assert "error: " in result.output
    assert expected in result.output
    assert "Traceback" not in result.output


def test_apply_missing_recipe_is_reported_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", "missing", str(target), "--yes"])

    assert result.exit_code != 0
    assert "error: recipe not found: missing" in result.output
    assert "Traceback" not in result.output


def test_apply_creates_one_backup_bundle_for_bulk_invocation(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: config.txt\n"
    )
    (tmp_path / "template.txt").write_text("after\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "config.txt").write_text("before first\n")
    (second / "config.txt").write_text("before second\n")

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(first), str(second), "--yes"])

    assert result.exit_code == 0, result.output
    bundles = BackupStore(library_root() / "backups").list()
    assert len(bundles) == 1
    metadata = BackupStore(library_root() / "backups").metadata(bundles[0].id)
    assert len(metadata["files"]) == 2


def test_apply_backup_bundle_records_only_successful_targets(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: config.txt\n"
    )
    (tmp_path / "template.txt").write_text("after\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "config.txt").write_text("before\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    rows = json.loads(result.stdout)
    assert [row["status"] for row in rows] == ["applied", "error"]
    assert (target / "config.txt").read_text() == "after\n"
    bundles = BackupStore(library_root() / "backups").list()
    assert len(bundles) == 1
    metadata = BackupStore(library_root() / "backups").metadata(bundles[0].id)
    assert len(metadata["files"]) == 1


def test_apply_var_values_keep_equals_and_unknown_vars_are_rejected(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "service=api=v1", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "service=api=v1\n"

    rejected = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "servcie=typo", "--yes"],
    )
    assert rejected.exit_code != 0
    assert "unknown input" in rejected.output


@pytest.mark.parametrize("value", ["[a, b]", "a: b", "{x: y}", "true"])
def test_apply_scalar_var_values_that_look_like_yaml_stay_literal_strings(
    tmp_path: Path,
    value: str,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("service={{ service }}\n")
    target = tmp_path / value.replace("/", "_")
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", f"service={value}", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == f"service={value}\n"


def test_apply_scalar_var_coercion_error_text_is_unchanged_for_yaml_like_values(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\ninputs:\n  replicas: {type: int, required: true}\nsteps: []\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "replicas=[a, b]", "--yes"],
    )

    assert result.exit_code != 0
    assert "cannot coerce value to int" in result.output
    assert "expects YAML" not in result.output


def test_apply_structured_var_yaml_list_resolves_to_native_outcome_value(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  cols: {type: list, items: str, required: true}\nsteps: []\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "cols=[a, b]",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["inputs"] == {"cols": ["a", "b"]}


def test_apply_structured_var_malformed_yaml_reports_pinned_error(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  cols: {type: list, items: str, required: true}\nsteps: []\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "cols=[", "--yes"],
    )

    assert result.exit_code != 0
    assert "input 'cols' expects YAML list:" in result.output


def test_apply_structured_var_scalar_yaml_result_reports_pinned_error(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  cols: {type: list, items: str, required: true}\nsteps: []\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", "cols=enabled", "--yes"],
    )

    assert result.exit_code != 0
    assert "input 'cols' expects YAML list: parsed value is not a list" in result.output


def test_apply_vars_file_native_list_skips_string_parsing(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  cols: {type: list, items: str, required: true}\nsteps: []\n"
    )
    vars_file = tmp_path / "vars.yml"
    vars_file.write_text("cols:\n  - a\n  - b\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--vars",
            str(vars_file),
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["inputs"] == {"cols": ["a", "b"]}


def test_apply_sensitive_structured_inputs_are_redacted_as_whole_values(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  tokens:\n"
        "    type: list\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps: []\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "tokens=[alpha, beta]",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["inputs"] == {"tokens": "***"}
    assert "alpha" not in result.stdout
    assert "beta" not in result.stdout


def test_apply_derives_target_inputs_and_redacts_outcome_rows(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    required: true\n"
        "    from: '{{ target.name }}'\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ token }}\n")
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "token=secret",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "api secret\n"
    rows = json.loads(result.stdout)
    assert rows[0]["inputs"] == {"service": "api", "token": "***"}
    assert "secret" not in result.stdout


def test_apply_sensitive_target_input_coercion_error_does_not_leak_secret(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: int\n"
        "    sensitive: true\n"
        "    required: true\n"
        "    from: '{{ record.token }}'\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "recipe.target",
            "record": {"path": str(target), "token": secret},
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=payload + "\n",
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "cannot coerce value to int"
    assert rows[0]["inputs"] == {}


def test_apply_sensitive_global_input_coercion_error_does_not_leak_secret(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: int\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--var", f"token={secret}", "--dry-run"],
    )

    assert result.exit_code != 0
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "cannot coerce value to int" in result.output


def test_apply_sensitive_inputs_redact_warnings_and_suppress_diffs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "240")
    secret = 'TOP-SECRET-9000"\\tail'
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: leak\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (recipe_dir / "template.txt").write_text("token={{ token }}\n")
    _write_hook_project(
        recipe_dir,
        public_name="leak",
        module_name="leak",
        code=(
            "import json\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    return helpers.warn(json.dumps({'warning': inputs['token']}))\n"
        ),
    )
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe_dir),
            str(target),
            "--var",
            f"token={secret}",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert "diff suppressed for target with sensitive inputs" not in result.stderr
    assert str(target) in result.stderr
    assert "files_changed" in result.stderr
    assert "out.txt" not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == "diagnostic suppressed for target with sensitive inputs"
    assert rows[0]["inputs"] == {"token": "***"}


def test_apply_sensitive_inputs_redact_hook_failures(
    tmp_path: Path,
) -> None:
    secret = 'TOP-SECRET-9000"\\tail'
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "inputs:\n"
        "  token:\n"
        "    type: str\n"
        "    scope: global\n"
        "    sensitive: true\n"
        "    required: true\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: config.txt\n"
        "    hook: leak\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="leak",
        module_name="leak",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    raise RuntimeError(f\"failed {inputs['token']!r}\")\n"
        ),
    )
    target = tmp_path / "api"
    target.mkdir()
    (target / "config.txt").write_text("before\n")

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe_dir),
            str(target),
            "--var",
            f"token={secret}",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == (
        "target planning failed; diagnostic suppressed for target with sensitive inputs"
    )
    assert rows[0]["inputs"] == {"token": "***"}


def test_apply_invalid_fixed_target_input_fails_before_target_rows(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\ninputs:\n  replicas: {type: int, scope: target}\nsteps: []\n")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(first),
            str(second),
            "--var",
            "replicas=not-an-int",
            "--dry-run",
            "--format",
            "json",
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "cannot coerce value to int" in result.stderr


def test_apply_invalid_jinja_source_fails_before_target_rows(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service:\n    type: str\n    from: '{{ target.name'\nsteps: []\n"
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(first), str(second), "--dry-run", "--format", "json"],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "invalid input source expression for service" in result.stderr


def test_apply_jinja_control_blocks_fail_before_target_rows(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    from: '{% for item in [target.name] %}{{ item }}{% endfor %}'\n"
        "steps: []\n"
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(first), str(second), "--dry-run", "--format", "json"],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "invalid input source expression for service" in result.stderr


def test_apply_record_valued_source_fails_without_copying_record_contents(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  debug: {type: str, from: '{{ record }}'}\nsteps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "recipe.target",
            "record": {"path": str(target), "token": secret},
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--dry-run", "--format", "json"],
        input=payload + "\n",
    )

    assert result.exit_code == 1, result.output
    assert secret not in result.stdout
    assert secret not in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert rows[0]["error"] == "derived input value must be a scalar"


def test_apply_outcome_inputs_render_in_yaml_and_table(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    yaml_result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "yaml"],
    )
    assert yaml_result.exit_code == 0, yaml_result.output
    assert "inputs:" in yaml_result.stdout
    assert "service: api" in yaml_result.stdout

    table_result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])
    assert table_result.exit_code == 0, table_result.output
    assert "inputs" in table_result.stdout
    assert "service" in table_result.stdout


def test_apply_derives_inputs_from_pipe_record_and_input_from_override(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "    required: true\n"
        "    from:\n"
        "      - '{{ record.repo }}'\n"
        "      - '{{ target.name }}'\n"
        "  owner:\n"
        "    type: str\n"
        "    from: '{{ record.team }}'\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ owner }}\n")
    workspace = tmp_path / "workspace"
    target = workspace / "api"
    target.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {
                "path": str(workspace),
                "target_path": str(target),
                "repo": "api",
                "team": "platform",
            },
        }
    )

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            "--stdin",
            "--yes",
            "--input-from",
            "owner={{ target.parent_name }}",
            "--format",
            "pipe",
        ],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    assert (target / "out.txt").read_text() == "api workspace\n"
    row = json.loads(result.stdout)
    assert row["kind"] == "recipe.outcome"
    assert row["record"]["inputs"] == {"service": "api", "owner": "workspace"}


def test_apply_derives_structured_input_from_pipe_record(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  collections:\n"
        "    type: list\n"
        "    items: str\n"
        "    from: '{{ record.collections }}'\n"
        "steps: []\n"
    )
    workspace = tmp_path / "workspace"
    target = workspace / "api"
    target.mkdir(parents=True)
    payload = json.dumps(
        {
            "untaped": "1",
            "kind": "workspace.repo",
            "record": {
                "path": str(workspace),
                "target_path": str(target),
                "collections": ["ansible.builtin", "community.general"],
            },
        }
    )

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--yes", "--format", "pipe"],
        input=payload + "\n",
    )

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)
    assert row["kind"] == "recipe.outcome"
    assert row["record"]["inputs"] == {"collections": ["ansible.builtin", "community.general"]}


def test_apply_rejects_input_from_conflicts_global_scope_and_interactive_check(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "  owner: {type: str, scope: global, required: true}\n"
        "steps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    conflict = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "service=fixed",
            "--input-from",
            "service={{ target.name }}",
            "--var",
            "owner=platform",
            "--dry-run",
        ],
    )
    assert conflict.exit_code != 0
    assert "cannot combine --var/--vars and --input-from for service" in conflict.output

    global_source = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--input-from",
            "owner={{ target.name }}",
            "--dry-run",
        ],
    )
    assert global_source.exit_code != 0
    assert "scope global" in global_source.output

    interactive_check = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--interactive", "--check"],
    )
    assert interactive_check.exit_code != 0
    assert "--interactive cannot be used with --check" in interactive_check.output


def test_apply_stdin_interactive_without_tty_fails_before_prompting(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\ninputs:\n  service: {type: str, scope: target, required: true}\nsteps: []\n"
    )
    target = tmp_path / "api"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), "--stdin", "--interactive", "--dry-run"],
        input=str(target) + "\n",
    )

    assert result.exit_code != 0
    assert "interactive input requires a terminal" in result.output


def test_apply_backup_metadata_records_redacted_per_target_inputs(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service: {type: str, required: true, from: '{{ target.name }}'}\n"
        "  token: {type: str, scope: global, sensitive: true, required: true}\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("{{ service }} {{ token }}\n")
    target = tmp_path / "api"
    target.mkdir()
    (target / "out.txt").write_text("before\n")

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--var",
            "token=secret",
            "--yes",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = BackupStore(library_root() / "backups").metadata("latest")
    assert metadata["files"][0]["inputs"] == {"service": "api", "token": "***"}
    assert "secret" not in json.dumps(metadata)


def test_apply_outcome_includes_optional_transform_warnings(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: missing.yml\n"
        "    optional: true\n"
        "    hook: unused\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    json_result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "json"],
    )
    assert json_result.exit_code == 0, json_result.output
    rows = json.loads(json_result.stdout)
    assert rows[0]["warnings"] == "optional transform skipped missing file: missing.yml"

    yaml_result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "yaml"],
    )
    assert yaml_result.exit_code == 0, yaml_result.output
    assert "warnings: 'optional transform skipped missing file: missing.yml'" in yaml_result.stdout

    pipe_result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "pipe"],
    )
    assert pipe_result.exit_code == 0, pipe_result.output
    pipe_row = json.loads(pipe_result.stdout)
    assert pipe_row["kind"] == "recipe.outcome"
    assert pipe_row["record"]["warnings"] == (
        "optional transform skipped missing file: missing.yml"
    )


def test_apply_outcome_includes_zero_match_glob_warnings(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: remove\n    globs:\n      - '**/*.generated'\n"
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--dry-run", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == "globs matched no files: **/*.generated"


def test_ansible_style_optional_multi_file_recipe_acceptance(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    files:\n"
        "      - local.yml\n"
        "      - site.yml\n"
        "      - playbooks/deploy.yml\n"
        "    optional: true\n"
        "    hook: add_play_collections\n"
        "  - type: remove\n"
        "    files:\n"
        "      - ansible.cfg\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="add_play_collections",
        module_name="add_play_collections",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + '# collections added to ' + file.name + '\\n'\n"
        ),
    )
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "playbooks").mkdir()
    (first / "local.yml").write_text("- hosts: localhost\n")
    (first / "ansible.cfg").write_text("[defaults]\n")
    (second / "local.yml").write_text("- hosts: localhost\n")
    (second / "site.yml").write_text("- hosts: all\n")
    (second / "playbooks" / "deploy.yml").write_text("- hosts: deploy\n")
    (second / "ansible.cfg").write_text("[defaults]\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(first), str(second), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == (
        "optional transform skipped missing file: site.yml; "
        "optional transform skipped missing file: playbooks/deploy.yml"
    )
    assert rows[1]["warnings"] == ""
    assert "# collections added to local.yml" in (first / "local.yml").read_text()
    assert not (first / "ansible.cfg").exists()
    assert "# collections added to local.yml" in (second / "local.yml").read_text()
    assert "# collections added to site.yml" in (second / "site.yml").read_text()
    assert "# collections added to deploy.yml" in (second / "playbooks" / "deploy.yml").read_text()
    assert not (second / "ansible.cfg").exists()


def test_explicit_single_file_recipe_does_not_use_sibling_hook_project(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: transform\n    file: local.yml\n    hook: sibling\n"
    )
    _write_hook_project(
        tmp_path,
        public_name="sibling",
        module_name="sibling",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + 'changed\\n'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    assert "hook not found: sibling" in result.output
    assert (target / "local.yml").read_text() == "---\n"


def test_external_hook_args_with_yaml_dates_are_rejected_before_worker(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: local.yml\n"
        "    hook: stamp\n"
        "    args:\n"
        "      day: 2026-06-19\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="stamp",
        module_name="stamp",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + 'day=' + args['day'] + '\\n'\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("---\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe_dir), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code != 0
    assert "is not JSON-serializable" in result.output
    assert (target / "local.yml").read_text() == "---\n"


def test_hook_run_transform_reads_disk_and_emits_exact_content(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="append",
        module_name="append",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('external diagnostic')\n"
            "    return content + args['suffix']\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "append",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
            "--arg",
            "suffix='!'",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert (target / "local.txt").read_text() == "start"
    assert "external diagnostic" in result.stderr
    assert str(target) in result.stderr
    assert "local.txt" in result.stderr
    assert '"suffix": "!"' in result.stderr


def test_hook_run_transform_content_overrides_do_not_require_existing_file(
    tmp_path: Path,
) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="show_context",
        module_name="show_context",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content + '|' + file.name + '|' + target.name\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    content_file = tmp_path / "fixture.txt"
    content_file.write_text("from-file")

    literal = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "show_context",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "missing.txt",
            "--content",
            "literal",
        ],
    )
    stdin = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "show_context",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "stdin.txt",
            "--content",
            "-",
        ],
        input="from-stdin",
    )
    file_result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "show_context",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "file.txt",
            "--content-file",
            str(content_file),
        ],
    )

    assert literal.exit_code == 0, literal.output
    assert literal.stdout == "literal|missing.txt|target"
    assert stdin.exit_code == 0, stdin.output
    assert stdin.stdout == "from-stdin|stdin.txt|target"
    assert file_result.exit_code == 0, file_result.output
    assert file_result.stdout == "from-file|file.txt|target"


def test_hook_run_missing_content_file_is_reported_cleanly(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="append",
        module_name="append",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "append",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
            "--content-file",
            str(tmp_path / "missing.txt"),
        ],
    )

    assert result.exit_code != 0
    assert "error: --content-file file not found" in result.output
    assert "Traceback" not in result.output


def test_hook_run_transform_diff_and_structured_output(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="replace",
        module_name="replace",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return content.replace('old', 'new')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("old\n")

    diff = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "replace",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
            "--diff",
        ],
    )
    structured = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "replace",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
            "--diff",
            "--format",
            "json",
        ],
    )

    assert diff.exit_code == 0, diff.output
    assert "--- a/local.txt" in diff.stdout
    assert "+++ b/local.txt" in diff.stdout
    assert "-old" in diff.stdout
    assert "+new" in diff.stdout
    assert structured.exit_code == 0, structured.output
    row = json.loads(structured.stdout)
    assert row["content"] == "new\n"
    assert "--- a/local.txt" in row["diff"]
    assert "inputs" not in row
    assert "args" not in row


def test_hook_run_validate_records_and_fail_exit(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "def validate(*, inputs, target, args, helpers):\n"
            "    if args.get('fail'):\n"
            "        return helpers.fail('not ready')\n"
            "    return helpers.warn('check manually')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    warn = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "ready",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--format",
            "json",
        ],
    )
    failed = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "ready",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--arg",
            "fail=yes",
            "--format",
            "pipe",
        ],
    )

    assert warn.exit_code == 0, warn.output
    warn_row = json.loads(warn.stdout)
    assert warn_row["hook"] == "ready"
    assert warn_row["kind"] == "validate"
    assert warn_row["status"] == "warn"
    assert warn_row["message"] == "check manually"
    assert failed.exit_code == 1, failed.output
    failed_row = json.loads(failed.stdout)
    assert failed_row["kind"] == "recipe.hook_run"
    assert failed_row["record"]["status"] == "fail"
    assert failed_row["record"]["message"] == "not ready"


def test_hook_run_dual_export_infers_or_requires_kind(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="dual",
        module_name="dual",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return 'transformed'\n\n"
            "def validate(*, inputs, target, args, helpers):\n"
            "    return helpers.warn('validated')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("before")

    inferred_transform = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "dual",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )
    ambiguous = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "dual",
            "--project",
            str(hook_project),
            "--target",
            str(target),
        ],
    )
    explicit_validate = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "dual",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--kind",
            "validate",
            "--format",
            "json",
        ],
    )

    assert inferred_transform.exit_code == 0, inferred_transform.output
    assert inferred_transform.stdout == "transformed"
    assert ambiguous.exit_code != 0
    assert (
        "hook 'dual' exports both transform() and validate(); pass --kind or --file"
        in ambiguous.output
    )
    assert explicit_validate.exit_code == 0, explicit_validate.output
    row = json.loads(explicit_validate.stdout)
    assert row["kind"] == "validate"
    assert row["status"] == "warn"
    assert row["message"] == "validated"


def test_hook_run_rejects_kind_specific_context_options(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    marker = tmp_path / "marker"
    _write_hook_project(
        hook_project,
        public_name="ready",
        module_name="ready",
        code=(
            "from pathlib import Path\n"
            "def validate(*, inputs, target, args, helpers):\n"
            f"    Path({str(marker)!r}).write_text('ran')\n"
            "    return helpers.pass_()\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()

    validate_with_file = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "ready",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )
    validate_with_diff = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "ready",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--diff",
        ],
    )
    transform_without_file = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "yaml_edit",
            "--target",
            str(target),
            "--args",
            str(tmp_path / "missing.yml"),
        ],
    )

    assert validate_with_file.exit_code != 0
    assert "validate hooks do not accept --file or content options" in validate_with_file.output
    assert validate_with_diff.exit_code != 0
    assert "validate hooks do not accept --file or content options" in validate_with_diff.output
    assert not marker.exists()
    assert transform_without_file.exit_code != 0
    assert "transform hooks require --file" in transform_without_file.output


def test_hook_run_inputs_and_args_merge_files_and_yaml_flags(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="types",
        module_name="types",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    return (\n"
            "        f\"enabled={inputs['enabled']!r};\"\n"
            "        f\"count={inputs['count']!r};\"\n"
            "        f\"mode={args['mode']!r}\"\n"
            "    )\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")
    inputs = tmp_path / "inputs.yml"
    inputs.write_text("enabled: false\ncount: 1\n")
    args = tmp_path / "args.yml"
    args.write_text("mode: old\n")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "types",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
            "--inputs",
            str(inputs),
            "--input",
            "enabled=yes",
            "--input",
            "count=3",
            "--args",
            str(args),
            "--arg",
            "mode=new",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "enabled=True;count=3;mode='new'"
    assert '"enabled": true' in result.stderr
    assert '"count": 3' in result.stderr
    assert '"mode": "new"' in result.stderr


def test_hook_run_yaml_flag_errors_are_reported_cleanly(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="types",
        module_name="types",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "types",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
            "--arg",
            "items=[",
        ],
    )

    assert result.exit_code != 0
    assert "error: --arg value for 'items' is invalid YAML" in result.output
    assert "Traceback" not in result.output


def test_hook_run_explicit_project_must_be_valid_before_global_or_builtin_fallback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.yml").write_text("enabled: false\n")
    args = tmp_path / "args.yml"
    args.write_text("edits:\n  - {op: set, path: [enabled], value: true}\n")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "yaml_edit",
            "--project",
            str(tmp_path / "missing-project"),
            "--target",
            str(target),
            "--file",
            "local.yml",
            "--args",
            str(args),
        ],
    )

    assert result.exit_code != 0
    assert "error: hook project not found" in result.output
    assert result.stdout == ""
    assert "Hook run:" not in result.stderr


def test_hook_run_resolution_order_prefers_cwd_then_installed_pack_then_builtin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_project = tmp_path / "pack-source"
    _write_hook_project(
        global_project,
        public_name="shadow",
        module_name="shadow",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return 'global'\n"
        ),
    )
    PackLibrary(library_root=library_root()).add(
        global_project,
        source=str(global_project),
        rev=None,
        name="shared",
        force=False,
    )
    cwd_project = tmp_path / "cwd"
    _write_hook_project(
        cwd_project,
        public_name="shadow",
        module_name="shadow",
        code="def transform(content, *, inputs, target, file, args, helpers):\n    return 'cwd'\n",
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    monkeypatch.chdir(cwd_project)
    cwd = CliInvoker().invoke(
        app,
        ["hook", "run", "shadow", "--target", str(target), "--file", "local.txt"],
    )
    monkeypatch.chdir(tmp_path)
    global_result = CliInvoker().invoke(
        app,
        ["hook", "run", "shadow", "--target", str(target), "--file", "local.txt"],
    )

    assert cwd.exit_code == 0, cwd.output
    assert cwd.stdout == "cwd"
    assert global_result.exit_code == 0, global_result.output
    assert global_result.stdout == "global"


def test_hook_run_external_failure_prints_traceback(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="broken",
        module_name="broken",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('before failure')\n"
            "    raise RuntimeError('boom')\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("ignored")

    result = CliInvoker().invoke(
        app,
        [
            "hook",
            "run",
            "broken",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "before failure" in result.stderr
    assert "Traceback" in result.stderr
    assert "RuntimeError: boom" in result.stderr


def test_hook_run_builtin_stdout_is_redirected_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("debug_builtin")

    def transform(content: str, **kwargs: object) -> str:
        print("builtin diagnostic")
        return content + "!"

    module.transform = transform  # type: ignore[attr-defined]
    monkeypatch.setitem(
        BUILTIN_HOOKS,
        "debug_builtin",
        BuiltinHook(module=module, exports=frozenset({"transform"})),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = CliInvoker().invoke(
        app,
        ["hook", "run", "debug_builtin", "--target", str(target), "--file", "local.txt"],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start!"
    assert "builtin diagnostic" in result.stderr


def test_hook_run_quiet_suppresses_context_but_not_hook_diagnostics(tmp_path: Path) -> None:
    hook_project = tmp_path / "hooks"
    _write_hook_project(
        hook_project,
        public_name="noisy",
        module_name="noisy",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n"
            "    print('hook diagnostic')\n"
            "    return content\n"
        ),
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "local.txt").write_text("start")

    result = CliInvoker().invoke(
        build_tool_app(app, SPEC).meta,
        [
            "--quiet",
            "hook",
            "run",
            "noisy",
            "--project",
            str(hook_project),
            "--target",
            str(target),
            "--file",
            "local.txt",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "start"
    assert "hook diagnostic" in result.stderr
    assert "Hook run:" not in result.stderr
    assert str(target) not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["show", "missing"],
        ["backup", "show", "latest"],
    ],
)
def test_library_command_value_errors_are_reported_cleanly(args: list[str]) -> None:
    result = CliInvoker().invoke(app, args)

    assert result.exit_code != 0
    assert "error: " in result.output
    assert "Traceback" not in result.output


def test_recipe_check_validates_package_assets_and_hooks(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("hello\n")
    (recipe_dir / "copy.txt").write_text("copy\n")
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
        "  - type: copy\n"
        "    source: copy.txt\n"
        "    dest: copy.txt\n"
        "  - type: validate\n"
        "    hook: check\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "pack": "recipe-hooks",
            "status": "pass",
            "path": str(recipe_dir),
            "recipes": 1,
            "hooks": 1,
            "error": "",
        }
    ]
    assert "Recipe preview:" not in result.stderr


def test_recipe_check_rejects_step_hook_kind_mismatch(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n  - type: validate\n    hook: check\n"
    )
    package = recipe_dir / "src" / "recipe_hooks" / "hooks"
    package.mkdir(parents=True)
    (recipe_dir / "src" / "recipe_hooks" / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "check.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    (recipe_dir / "pyproject.toml").write_text(
        "[project]\n"
        'name = "recipe-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        '"demo" = { path = "recipe.yml" }\n\n'
        "[tool.untaped_recipe.hooks]\n"
        '"check" = { module = "recipe_hooks.hooks.check" }\n'
    )
    (recipe_dir / "uv.lock").write_text("version = 1\n")

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "validate step hook 'check' does not export a validate() function" in rows[0]["error"]


@pytest.mark.parametrize(
    ("recipe_body", "expected"),
    [
        ("version: [\n", "invalid recipe YAML"),
        ("version: 2\nsteps: []\n", "invalid recipe"),
        (
            "version: 1\n"
            "steps:\n"
            "  - type: template\n"
            "    template: missing.txt\n"
            "    dest: out.txt\n",
            "template not found",
        ),
        (
            "version: 1\nsteps:\n  - type: validate\n    hook: missing\n",
            "hook not found",
        ),
        (
            "version: 1\n"
            "inputs:\n"
            "  service: {type: str, from: '{{ target.name | upper }}'}\n"
            "steps: []\n",
            "invalid input source expression for service",
        ),
    ],
)
def test_recipe_check_reports_invalid_packages(
    tmp_path: Path,
    recipe_body: str,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(recipe_body)

    result = CliInvoker().invoke(
        app,
        ["check", str(recipe_dir / "recipe.yml"), "--format", "json"],
    )

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert expected in rows[0]["error"]
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("remove_lock", "remove_module", "expected"),
    [
        (True, False, "missing uv.lock"),
        (False, True, "hook module file not found"),
    ],
)
def test_recipe_check_reports_broken_local_hook_projects(
    tmp_path: Path,
    remove_lock: bool,
    remove_module: bool,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\nsteps:\n  - type: validate\n    hook: check\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    if remove_lock:
        (recipe_dir / "uv.lock").unlink()
    if remove_module:
        (recipe_dir / "src" / "recipe_hooks" / "hooks" / "check.py").unlink()

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert expected in rows[0]["error"]


def test_recipe_check_validates_unreferenced_local_hook_project_lockfile(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text("version: 1\nsteps: []\n")
    _write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    (recipe_dir / "uv.lock").unlink()

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "missing uv.lock" in rows[0]["error"]


def test_recipe_check_rejects_runtime_untaped_recipe_hook_dependency(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text("version: 1\nsteps: []\n")
    _write_hook_project(
        recipe_dir,
        public_name="check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    pyproject = recipe_dir / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            "dependencies = []",
            'dependencies = ["untaped-recipe>=0.7"]',
        )
    )

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "must not depend on untaped-recipe at runtime" in rows[0]["error"]


def test_recipe_check_validates_unreferenced_local_hook_project_modules(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "steps:\n"
        "  - type: transform\n"
        "    file: config.yml\n"
        "    hook: yaml_edit\n"
        "    args: {edits: []}\n"
    )
    _write_hook_project(
        recipe_dir,
        public_name="local_check",
        module_name="check",
        code="def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n",
    )
    (recipe_dir / "src" / "recipe_hooks" / "hooks" / "check.py").unlink()

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "hook module file not found" in rows[0]["error"]


@pytest.mark.parametrize(
    ("pyproject", "expected"),
    [
        (
            "[project]\nname = 'recipe-hooks'\nversion = '0.1.0'\n\n"
            "[tool.untaped_recipe.recipes]\n"
            '"demo" = { path = "recipe.yml" }\n\n'
            "[tool.untaped_recipe.hooks]\n"
            '"bad-name" = { module = "recipe_hooks.hooks.check" }\n',
            "invalid hook name",
        ),
        (
            "[project]\nname = 'recipe-hooks'\nversion = '0.1.0'\n\n"
            "[tool.untaped_recipe.recipes]\n"
            '"demo" = { path = "recipe.yml" }\n\n'
            "[tool.untaped_recipe.hooks]\n"
            '"check" = { module =',
            "invalid pack project pyproject",
        ),
    ],
)
def test_recipe_check_validates_unreferenced_local_hook_project_metadata(
    tmp_path: Path,
    pyproject: str,
    expected: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (recipe_dir / "pyproject.toml").write_text(pyproject)
    (recipe_dir / "uv.lock").write_text("version = 1\n")

    result = CliInvoker().invoke(app, ["check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert expected in rows[0]["error"]


def test_backup_commands_show_list_and_restore(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    config = target / "config.yml"
    config.write_text("before\n")
    store = BackupStore(library_root() / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={"service": "api"},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("config.yml"),
                before="before\n",
                after="after\n",
            )
        ],
    )
    config.write_text("after\n")
    invoker = CliInvoker()

    listed = invoker.invoke(app, ["backup", "list", "--format", "json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)[0]["id"] == bundle.id
    shown = invoker.invoke(app, ["backup", "show", bundle.id])
    assert shown.exit_code == 0, shown.output
    assert "recipe: demo" in shown.stdout
    shown_json = invoker.invoke(app, ["backup", "show", bundle.id, "--format", "json"])
    assert shown_json.exit_code == 0, shown_json.output
    assert json.loads(shown_json.stdout)["id"] == bundle.id

    restored = invoker.invoke(app, ["backup", "restore", bundle.id, "--yes"])
    assert restored.exit_code == 0, restored.output
    assert config.read_text() == "before\n"


def test_backup_restore_refuses_non_tty_without_yes_and_restores_with_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    config = target / "config.yml"
    config.write_text("before\n")
    store = BackupStore(library_root() / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("config.yml"),
                before="before\n",
                after="after\n",
            )
        ],
    )
    config.write_text("after\n")
    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: False)
    invoker = CliInvoker()

    refused = invoker.invoke(app, ["backup", "restore", bundle.id])
    restored = invoker.invoke(app, ["backup", "restore", bundle.id, "--yes"])

    assert refused.exit_code != 0
    assert "requires --yes" in refused.output
    assert restored.exit_code == 0, restored.output
    assert config.read_text() == "before\n"


def test_backup_restore_failing_item_exits_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    first = target / "one.txt"
    second = target / "two.txt"
    first.write_text("one-before\n")
    second.write_text("two-before\n")
    store = BackupStore(library_root() / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("one.txt"),
                before="one-before\n",
                after="one-after\n",
            ),
            FileChange(
                target=target,
                relative_path=Path("two.txt"),
                before="two-before\n",
                after="two-after\n",
            ),
        ],
    )
    first.write_text("one-after\n")
    second.write_text("two-after\n")
    original_replace = file_writer_module.os.replace

    def fail_second_replace(source: Path, dest: Path) -> None:
        if Path(dest).name == "two.txt":
            raise OSError("disk full")
        original_replace(source, dest)

    monkeypatch.setattr(file_writer_module.os, "replace", fail_second_replace)

    result = CliInvoker().invoke(app, ["backup", "restore", bundle.id, "--yes"])

    assert result.exit_code == 1, result.output
    assert "disk full" in result.output
    # The restore is one staged transaction: a mid-write failure rolls back
    # already-restored files, so both keep their pre-restore content.
    assert first.read_text() == "one-after\n"
    assert second.read_text() == "two-after\n"


def test_backup_restore_flushes_bundle_in_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    changes = []
    for name in ("one.txt", "two.txt", "three.txt"):
        (target / name).write_text(f"{name}-before\n")
        changes.append(
            FileChange(
                target=target,
                relative_path=Path(name),
                before=f"{name}-before\n",
                after=f"{name}-after\n",
            )
        )
    store = BackupStore(library_root() / "backups")
    bundle = store.create(recipe_name="demo", inputs={}, changes=changes)
    for name in ("one.txt", "two.txt", "three.txt"):
        (target / name).write_text(f"{name}-after\n")

    import untaped_recipe.infrastructure.backup as backup_module

    calls: list[int] = []
    original_flush = backup_module.flush_changes

    def counting_flush(changes: tuple[FileChange, ...]) -> None:
        calls.append(len(changes))
        original_flush(changes)

    monkeypatch.setattr(backup_module, "flush_changes", counting_flush)

    result = CliInvoker().invoke(app, ["backup", "restore", bundle.id, "--yes"])

    assert result.exit_code == 0, result.output
    assert calls == [3]
    for name in ("one.txt", "two.txt", "three.txt"):
        assert (target / name).read_text() == f"{name}-before\n"


def _seed_bundle(backups_root: Path, bundle_id: str, *, payload_bytes: int = 10) -> Path:
    bundle_dir = backups_root / bundle_id
    (bundle_dir / "files").mkdir(parents=True)
    (bundle_dir / "files" / "0").write_text("x" * payload_bytes)
    (bundle_dir / "metadata.json").write_text(
        json.dumps({"id": bundle_id, "recipe": "demo", "inputs": {}, "files": []})
    )
    return bundle_dir


def test_backup_prune_keep_prunes_oldest(tmp_path: Path) -> None:
    backups = library_root() / "backups"
    old = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    mid = _seed_bundle(backups, "20250201T000000000000Z-bbbbbbbb")
    new = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    result = CliInvoker().invoke(app, ["backup", "prune", "--keep", "2", "--yes"])

    assert result.exit_code == 0, result.output
    assert not old.exists()
    assert mid.exists()
    assert new.exists()
    assert "20250101T000000000000Z-aaaaaaaa" in result.stdout
    assert "pruned 1 of 3 backup(s)" in result.stderr
    assert "reclaimed" in result.stderr


def test_backup_prune_older_than_days(tmp_path: Path) -> None:
    backups = library_root() / "backups"
    old = _seed_bundle(backups, "20200101T000000000000Z-aaaaaaaa")
    fresh = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    result = CliInvoker().invoke(app, ["backup", "prune", "--older-than", "30", "--yes"])

    assert result.exit_code == 0, result.output
    assert not old.exists()
    assert fresh.exists()


def test_backup_prune_union_of_keep_and_age(tmp_path: Path) -> None:
    backups = library_root() / "backups"
    aged = _seed_bundle(backups, "20200101T000000000000Z-aaaaaaaa")
    mid = _seed_bundle(backups, "20990201T000000000000Z-bbbbbbbb")
    newest = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    result = CliInvoker().invoke(
        app, ["backup", "prune", "--keep", "1", "--older-than", "30", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert not aged.exists()
    assert not mid.exists()
    assert newest.exists()


def test_backup_prune_uses_settings_when_flags_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backups = library_root() / "backups"
    old = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    new = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")
    monkeypatch.setenv("UNTAPED_RECIPE__BACKUP_KEEP", "1")
    invalidate_settings_cache()

    result = CliInvoker().invoke(app, ["backup", "prune", "--yes"])

    assert result.exit_code == 0, result.output
    assert not old.exists()
    assert new.exists()


def test_backup_prune_requires_a_policy(tmp_path: Path) -> None:
    _seed_bundle(library_root() / "backups", "20250101T000000000000Z-aaaaaaaa")

    result = CliInvoker().invoke(app, ["backup", "prune", "--yes"])

    assert result.exit_code != 0
    assert "backup prune needs --keep/--older-than" in result.output


def test_backup_prune_conforms_to_destructive_contract(tmp_path: Path) -> None:
    backups = library_root() / "backups"
    old = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    new = _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    def assert_unchanged() -> None:
        assert old.exists()
        assert new.exists()

    assert_destructive_contract(
        app,
        ["backup", "prune", "--keep", "1"],
        assert_unchanged=assert_unchanged,
    )


def test_new_help_placeholders_render_meaningfully(tmp_path: Path) -> None:
    invoker = CliInvoker()

    recipe_help = invoker.invoke(app, ["new", "recipe", "--help"])
    hook_help = invoker.invoke(app, ["new", "hook", "--help"])
    hook_run_help = invoker.invoke(app, ["hook", "run", "--help"])

    assert "PACK/RECIPE reference." in recipe_help.stdout
    assert "PACK/HOOK reference." in hook_help.stdout
    assert "PACK/HOOK reference." in hook_run_help.stdout
    for result in (recipe_help, hook_help, hook_run_help):
        assert "REF  /." not in result.stdout


def test_apply_help_uses_slash_ref_grammar(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["apply", "--help"])

    assert "pack/recipe" in result.stdout
    assert "pack:recipe" not in result.stdout


def test_empty_library_list_and_check_print_guidance(tmp_path: Path) -> None:
    invoker = CliInvoker()

    listed = invoker.invoke(app, ["list"])
    packs = invoker.invoke(app, ["list", "--packs"])
    checked = invoker.invoke(app, ["check"])

    for result in (listed, packs, checked):
        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert "no packs installed" in result.stderr
        assert "new pack" in result.stderr
        assert "add" in result.stderr


def test_recipe_schema_errors_are_domain_errors_with_path(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\nname: nope\nsteps: []\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])

    assert result.exit_code != 0
    assert str(recipe) in result.output
    assert "name is not allowed here" in result.output
    assert "pydantic" not in result.output
    assert "extra_forbidden" not in result.output


def test_recipe_yaml_parse_errors_name_the_file(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: [unclosed\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(app, ["apply", str(recipe), str(target), "--dry-run"])

    assert result.exit_code != 0
    assert str(recipe) in result.output
    assert "invalid recipe YAML" in result.output


def test_apply_unchanged_targets_report_unchanged_status(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    changing = tmp_path / "changing"
    changing.mkdir()
    unchanged = tmp_path / "unchanged"
    unchanged.mkdir()
    (unchanged / "out.txt").write_text("hello\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(changing), str(unchanged), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = {row["target"]: row["status"] for row in json.loads(result.stdout)}
    assert rows[str(changing)] == "applied"
    assert rows[str(unchanged)] == "unchanged"
    assert "planned" not in result.stdout


def test_apply_table_inputs_cell_is_not_a_dict_repr(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "inputs:\n"
        "  service:\n"
        "    type: str\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("svc={{ service }}\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--yes",
            "--var",
            "service=api",
            "--columns",
            "inputs",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "service=api" in result.stdout
    assert "{'service'" not in result.stdout


def test_apply_table_inputs_cell_renders_structured_values_with_python_repr(
    tmp_path: Path,
) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\ninputs:\n  cols:\n    type: list\nsteps: []\n")
    target = tmp_path / "target"
    target.mkdir()

    result = CliInvoker().invoke(
        app,
        [
            "apply",
            str(recipe),
            str(target),
            "--yes",
            "--var",
            "cols=[a, b]",
            "--columns",
            "inputs",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "cols=['a', 'b']" in result.stdout


def test_backup_show_renders_files_as_lines(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "config.yml").write_text("before\n")
    store = BackupStore(library_root() / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("config.yml"),
                before="before\n",
                after="after\n",
            )
        ],
    )

    result = CliInvoker().invoke(app, ["backup", "show", bundle.id])

    assert result.exit_code == 0, result.output
    assert "files:" in result.stdout
    assert f"  - {target}/config.yml" in result.stdout
    assert "[{" not in result.stdout


def test_apply_all_unchanged_run_reports_unchanged_not_planned(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\nsteps:\n  - type: template\n    template: template.txt\n    dest: out.txt\n"
    )
    (tmp_path / "template.txt").write_text("hello\n")
    target = tmp_path / "target"
    target.mkdir()
    (target / "out.txt").write_text("hello\n")

    result = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--yes", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [row["status"] for row in rows] == ["unchanged"]
    assert "0 applied, 1 unchanged" in result.stderr


def test_backup_restore_decline_prints_no_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "config.yml").write_text("before\n")
    store = BackupStore(library_root() / "backups")
    bundle = store.create(
        recipe_name="demo",
        inputs={},
        changes=[
            FileChange(
                target=target,
                relative_path=Path("config.yml"),
                before="before\n",
                after="after\n",
            )
        ],
    )
    (target / "config.yml").write_text("after\n")

    monkeypatch.setattr("untaped.batch.stream_is_tty", lambda stream: True)
    monkeypatch.setattr(
        "untaped_recipe.cli.backup_commands.ui_context", lambda **kwargs: _DeclineUi()
    )
    result = CliInvoker().invoke(app, ["backup", "restore", bundle.id])

    assert result.exit_code == 0, result.output
    assert "restored" not in result.stderr
    assert (target / "config.yml").read_text() == "after\n"


def test_backup_prune_counts_failed_deletions_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backups = library_root() / "backups"
    first = _seed_bundle(backups, "20250101T000000000000Z-aaaaaaaa")
    second = _seed_bundle(backups, "20250201T000000000000Z-bbbbbbbb")
    _seed_bundle(backups, "20990301T000000000000Z-cccccccc")

    original_delete = BackupStore.delete

    def flaky_delete(self: BackupStore, backup_id: str) -> None:
        if backup_id.endswith("aaaaaaaa"):
            raise ValueError(f"backup not found: {backup_id}")
        original_delete(self, backup_id)

    monkeypatch.setattr(BackupStore, "delete", flaky_delete)

    result = CliInvoker().invoke(app, ["backup", "prune", "--keep", "1", "--yes"])

    assert result.exit_code == 1, result.output
    assert "error: 20250101T000000000000Z-aaaaaaaa" in result.stderr
    assert first.exists()
    assert not second.exists()
