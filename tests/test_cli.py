"""CLI tests for apply, libraries, and backup restore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.infrastructure.backup import BackupStore

pytestmark = pytest.mark.usefixtures("isolate_config")


def test_apply_yes_writes_and_emits_json_summary(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "name: demo\n"
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
    assert "out.txt" in result.stderr


def test_apply_dry_run_and_noninteractive_default_write_nothing(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "name: demo\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
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


def test_apply_stdin_requires_yes_and_resolves_workspace_repo_pipe(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "name: demo\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: out.txt\n"
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


def test_apply_stdin_without_yes_refuses_before_hooks_run(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "hooks").mkdir()
    marker = tmp_path / "hook-ran"
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "name: demo\n"
        "steps:\n"
        "  - type: validate\n"
        "    hook: touch\n"
        "    args:\n"
        f"      marker: {marker}\n"
    )
    (recipe_dir / "hooks" / "touch.py").write_text(
        "from pathlib import Path\n"
        "def validate(*, inputs, target, args, helpers):\n"
        "    Path(args['marker']).write_text('ran')\n"
        "    return helpers.pass_()\n"
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


def test_apply_creates_one_backup_bundle_for_bulk_invocation(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "name: demo\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: config.txt\n"
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
        "version: 1\n"
        "name: demo\n"
        "steps:\n"
        "  - type: template\n"
        "    template: template.txt\n"
        "    dest: config.txt\n"
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
        "name: demo\n"
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


def test_apply_outcome_includes_optional_transform_warnings(tmp_path: Path) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text(
        "version: 1\n"
        "name: demo\n"
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


def test_ansible_style_optional_multi_file_recipe_acceptance(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "hooks").mkdir()
    (recipe_dir / "recipe.yml").write_text(
        "version: 1\n"
        "name: ansible-2.12-playbook-migration\n"
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
    (recipe_dir / "hooks" / "add_play_collections.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    return content + '# collections added to ' + file.name + '\\n'\n"
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


def test_recipe_and_hook_library_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = tmp_path / "recipe.yml"
    recipe.write_text("version: 1\nname: demo\nsteps: []\n")
    hook = tmp_path / "check.py"
    hook.write_text("VALUE = 'hook'\n")
    editor = tmp_path / "editor.sh"
    marker = tmp_path / "edited.txt"
    editor.write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > {marker}\n")
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    invoker = CliInvoker()

    added_recipe = invoker.invoke(app, ["recipe", "add", str(recipe), "--name", "demo"])
    assert added_recipe.exit_code == 0, added_recipe.output
    listed_recipes = invoker.invoke(app, ["recipe", "list", "--format", "json"])
    assert listed_recipes.exit_code == 0, listed_recipes.output
    assert json.loads(listed_recipes.stdout)[0]["name"] == "demo"
    shown_recipe = invoker.invoke(app, ["recipe", "show", "demo"])
    assert "name: demo" in shown_recipe.stdout
    edited_recipe = invoker.invoke(app, ["recipe", "edit", "demo"])
    assert edited_recipe.exit_code == 0, edited_recipe.output
    assert marker.read_text().endswith("demo.yml")
    refused_recipe_remove = invoker.invoke(app, ["recipe", "remove", "demo"])
    assert refused_recipe_remove.exit_code != 0
    assert "requires --yes" in refused_recipe_remove.output
    removed_recipe = invoker.invoke(app, ["recipe", "remove", "demo", "--yes"])
    assert removed_recipe.exit_code == 0, removed_recipe.output

    added_hook = invoker.invoke(app, ["hook", "add", str(hook), "--name", "check"])
    assert added_hook.exit_code == 0, added_hook.output
    listed_hooks = invoker.invoke(app, ["hook", "list", "--format", "json"])
    assert listed_hooks.exit_code == 0, listed_hooks.output
    assert json.loads(listed_hooks.stdout)[0]["name"] == "check"
    shown_hook = invoker.invoke(app, ["hook", "show", "check"])
    assert "VALUE = 'hook'" in shown_hook.stdout
    edited_hook = invoker.invoke(app, ["hook", "edit", "check"])
    assert edited_hook.exit_code == 0, edited_hook.output
    assert marker.read_text().endswith("check.py")
    refused_hook_remove = invoker.invoke(app, ["hook", "remove", "check"])
    assert refused_hook_remove.exit_code != 0
    assert "requires --yes" in refused_hook_remove.output
    removed_hook = invoker.invoke(app, ["hook", "remove", "check", "--yes"])
    assert removed_hook.exit_code == 0, removed_hook.output


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

    restored = invoker.invoke(app, ["backup", "restore", bundle.id])
    assert restored.exit_code == 0, restored.output
    assert config.read_text() == "before\n"
