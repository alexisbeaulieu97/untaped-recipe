"""CLI tests for apply, libraries, and backup restore."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

import untaped_recipe.infrastructure.file_writer as file_writer_module
from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.infrastructure.backup import BackupStore

pytestmark = pytest.mark.usefixtures("isolate_config")


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


def test_apply_yes_writes_and_emits_json_summary(tmp_path: Path) -> None:
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
    assert "out.txt" in result.stderr


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


def test_apply_check_reports_drift_without_writing_or_backing_up(tmp_path: Path) -> None:
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

    (target / "out.txt").write_text("hello\n")
    clean = CliInvoker().invoke(
        app,
        ["apply", str(recipe), str(target), "--check", "--format", "json"],
    )

    assert clean.exit_code == 0, clean.output
    rows = json.loads(clean.stdout)
    assert rows[0]["status"] == "check"
    assert rows[0]["files_changed"] == 0


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
) -> None:
    secret = "TOP-SECRET-9000"
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
            "def validate(*, inputs, target, args, helpers):\n"
            "    return helpers.warn(f\"warning {inputs['token']}\")\n"
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
    assert "diff suppressed for target with sensitive inputs" in result.stderr
    rows = json.loads(result.stdout)
    assert rows[0]["warnings"] == "warning ***"
    assert rows[0]["inputs"] == {"token": "***"}


def test_apply_sensitive_inputs_redact_hook_failures(
    tmp_path: Path,
) -> None:
    secret = "TOP-SECRET-9000"
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
            "    raise RuntimeError(f\"failed {inputs['token']}\")\n"
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
    assert "failed ***" in rows[0]["error"]
    assert rows[0]["inputs"] == {"token": "***"}


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
            "record": {"path": str(workspace), "repo": "api", "team": "platform"},
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


def test_external_hook_args_with_yaml_dates_cross_worker_as_strings(tmp_path: Path) -> None:
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

    assert result.exit_code == 0, result.output
    assert "day=2026-06-19" in (target / "local.yml").read_text()


@pytest.mark.parametrize(
    "args",
    [
        ["hook", "show", "missing"],
        ["recipe", "show", "missing"],
        ["backup", "show", "latest"],
    ],
)
def test_library_command_value_errors_are_reported_cleanly(args: list[str]) -> None:
    result = CliInvoker().invoke(app, args)

    assert result.exit_code != 0
    assert "error: " in result.output
    assert "Traceback" not in result.output


def test_recipe_and_hook_library_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recipe = tmp_path / "demo"
    editor = tmp_path / "editor.sh"
    marker = tmp_path / "edited.txt"
    editor.write_text(f"#!/bin/sh\nprintf '%s' \"$1\" > {marker}\n")
    editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))
    monkeypatch.chdir(tmp_path)
    invoker = CliInvoker()

    initialized_recipe = invoker.invoke(app, ["recipe", "init", "demo"])
    assert initialized_recipe.exit_code == 0, initialized_recipe.output
    assert Path(initialized_recipe.stdout.strip()) == recipe
    added_recipe = invoker.invoke(app, ["recipe", "add", str(recipe)])
    assert added_recipe.exit_code == 0, added_recipe.output
    listed_recipes = invoker.invoke(app, ["recipe", "list", "--format", "json"])
    assert listed_recipes.exit_code == 0, listed_recipes.output
    assert json.loads(listed_recipes.stdout)[0]["name"] == "demo"
    shown_recipe = invoker.invoke(app, ["recipe", "show", "demo"])
    assert "steps: []" in shown_recipe.stdout
    edited_recipe = invoker.invoke(app, ["recipe", "edit", "demo"])
    assert edited_recipe.exit_code == 0, edited_recipe.output
    assert marker.read_text().endswith("recipe.yml")
    refused_recipe_remove = invoker.invoke(app, ["recipe", "remove", "demo"])
    assert refused_recipe_remove.exit_code != 0
    assert "requires --yes" in refused_recipe_remove.output
    removed_recipe = invoker.invoke(app, ["recipe", "remove", "demo", "--yes"])
    assert removed_recipe.exit_code == 0, removed_recipe.output

    initialized_hook = invoker.invoke(app, ["hook", "init", "check"])
    assert initialized_hook.exit_code == 0, initialized_hook.output
    initialized_path = Path(initialized_hook.stdout.strip())
    assert (initialized_path / "pyproject.toml").is_file()
    assert (initialized_path / "uv.lock").is_file()

    listed_initialized_hooks = invoker.invoke(app, ["hook", "list", "--format", "json"])
    assert listed_initialized_hooks.exit_code == 0, listed_initialized_hooks.output
    initialized_row = json.loads(listed_initialized_hooks.stdout)[0]
    assert initialized_row["name"] == "check"
    assert initialized_row["hooks"] == "check"
    shown_initialized_hook = invoker.invoke(app, ["hook", "show", "check"])
    assert "def transform" in shown_initialized_hook.stdout
    edited_initialized_hook = invoker.invoke(app, ["hook", "edit", "check"])
    assert edited_initialized_hook.exit_code == 0, edited_initialized_hook.output
    assert marker.read_text().endswith("check.py")
    removed_initialized_hook = invoker.invoke(app, ["hook", "remove", "check", "--yes"])
    assert removed_initialized_hook.exit_code == 0, removed_initialized_hook.output

    hook_project = tmp_path / "shared-project"
    _write_hook_project(
        hook_project,
        public_name="shared",
        module_name="shared",
        code=(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        ),
    )
    added_hook = invoker.invoke(app, ["hook", "add", str(hook_project), "--name", "shared"])
    assert added_hook.exit_code == 0, added_hook.output
    listed_hooks = invoker.invoke(app, ["hook", "list", "--format", "json"])
    assert listed_hooks.exit_code == 0, listed_hooks.output
    assert json.loads(listed_hooks.stdout)[0]["name"] == "shared"
    shown_hook = invoker.invoke(app, ["hook", "show", "shared"])
    assert "def transform" in shown_hook.stdout
    edited_hook = invoker.invoke(app, ["hook", "edit", "shared"])
    assert edited_hook.exit_code == 0, edited_hook.output
    assert marker.read_text().endswith("shared.py")
    refused_hook_remove = invoker.invoke(app, ["hook", "remove", "shared"])
    assert refused_hook_remove.exit_code != 0
    assert "requires --yes" in refused_hook_remove.output
    removed_hook = invoker.invoke(app, ["hook", "remove", "shared", "--yes"])
    assert removed_hook.exit_code == 0, removed_hook.output


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

    result = CliInvoker().invoke(app, ["recipe", "check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert rows == [
        {
            "recipe": "demo",
            "status": "ok",
            "path": str(recipe_dir / "recipe.yml"),
            "error": "",
        }
    ]


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
        ["recipe", "check", str(recipe_dir / "recipe.yml"), "--format", "json"],
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

    result = CliInvoker().invoke(app, ["recipe", "check", str(recipe_dir), "--format", "json"])

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

    result = CliInvoker().invoke(app, ["recipe", "check", str(recipe_dir), "--format", "json"])

    assert result.exit_code == 1, result.output
    rows = json.loads(result.stdout)
    assert rows[0]["status"] == "error"
    assert "missing uv.lock" in rows[0]["error"]


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

    result = CliInvoker().invoke(app, ["recipe", "check", str(recipe_dir), "--format", "json"])

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
            "invalid recipe project pyproject",
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

    result = CliInvoker().invoke(app, ["recipe", "check", str(recipe_dir), "--format", "json"])

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

    restored = invoker.invoke(app, ["backup", "restore", bundle.id])
    assert restored.exit_code == 0, restored.output
    assert config.read_text() == "before\n"
