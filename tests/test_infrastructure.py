"""Additional infrastructure and orchestration contract tests."""

from __future__ import annotations

import errno
import importlib.util
from pathlib import Path

import pytest

import untaped_recipe.infrastructure.file_writer as file_writer_module
import untaped_recipe.infrastructure.ruamel_io as ruamel_io_module
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.run_bulk import ApplyWriteError, RunBulkApply, flush_changes
from untaped_recipe.application.targets import Target
from untaped_recipe.builtins.hooks import yaml_edit
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.hook_worker import HookHelpers as WorkerHookHelpers
from untaped_recipe.infrastructure.hook_executor import HookExecutor
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_resolver import HookResolver
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.ruamel_io import dump_yaml, load_yaml


def test_build_package_wheel_writes_recipe_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    dist_dir = tmp_path / "dist"
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    release.build_package_wheel(dist_dir)

    assert list(dist_dir.glob("untaped_recipe-0.12.0-*.whl"))


def test_release_smoke_new_runs_outside_workspace_with_local_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        assert env is not None
        calls.append((command, cwd, env))
        pack = cwd / "hook_api_smoke"
        pack.mkdir(exist_ok=True)
        (pack / "uv.lock").write_text('name = "untaped-recipe"\nversion = "0.9.0"\n')

    monkeypatch.setattr(release, "_run", fake_run)

    release.smoke_new("0.9.0", find_links=dist_dir)

    assert len(calls) == 2
    first, second = calls
    command, cwd, env = first
    assert cwd != Path(release.ROOT)
    assert "--no-project" in command
    assert "--project" not in command
    assert "untaped-recipe==0.9.0" in command
    assert command[-4:] == ["untaped-recipe", "new", "pack", "hook_api_smoke"]
    assert second[0][-4:] == ["untaped-recipe", "new", "hook", "./hook_api_smoke/probe"]
    assert env is not None
    assert env["UV_FIND_LINKS"] == str(dist_dir.resolve())


def test_release_smoke_new_resolves_relative_local_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    captured_find_links: list[str] = []

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        del command
        assert env is not None
        captured_find_links.append(env["UV_FIND_LINKS"])
        pack = cwd / "hook_api_smoke"
        pack.mkdir(exist_ok=True)
        (pack / "uv.lock").write_text('name = "untaped-recipe"\nversion = "0.9.0"\n')

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.chdir(tmp_path)

    release.smoke_new("0.9.0", find_links=Path("dist"))

    assert captured_find_links == [str(dist_dir.resolve()), str(dist_dir.resolve())]


def test_release_smoke_new_uses_published_index_and_isolated_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(release.ROOT) / ".venv"))
    monkeypatch.setenv("PYTHONPATH", str(Path(release.ROOT) / "src"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        assert env is not None
        calls.append((command, cwd, env))
        pack = cwd / "hook_api_smoke"
        pack.mkdir(exist_ok=True)
        (pack / "uv.lock").write_text('name = "untaped-recipe"\nversion = "0.9.0"\n')

    monkeypatch.setattr(release, "_run", fake_run)

    release.smoke_new("0.9.0", index_url="https://test.pypi.org/simple/")

    assert len(calls) == 2
    command, cwd, env = calls[0]
    assert cwd != Path(release.ROOT)
    assert "--no-project" in command
    assert "--project" not in command
    assert "untaped-recipe==0.9.0" in command
    assert env is not None
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["UV_CACHE_DIR"] == str(cwd / "uv-cache")
    assert env["UV_INDEX"] == "https://test.pypi.org/simple/"
    assert env["UV_INDEX_STRATEGY"] == "unsafe-best-match"


def test_release_verify_sdk_published_uses_isolated_uv_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(release.ROOT) / ".venv"))
    monkeypatch.setenv("PYTHONPATH", str(Path(release.ROOT) / "src"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> object:
        assert capture_output is True
        assert text is True
        assert check is False
        calls.append((command, cwd, env))
        return release.subprocess.CompletedProcess(command, 0, stdout="2.4.0\n", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)

    release.verify_sdk_published()

    assert calls
    command, cwd, env = calls[0]
    assert cwd != Path(release.ROOT)
    assert "--with" in command
    assert "untaped>=3.0.0,<4" in command
    assert env is not None
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["UV_CACHE_DIR"] == str(cwd / "uv-cache")


def test_release_wait_published_uses_isolated_uv_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    calls: list[tuple[list[str], Path, dict[str, str] | None]] = []
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(release.ROOT) / ".venv"))
    monkeypatch.setenv("PYTHONPATH", str(Path(release.ROOT) / "src"))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> object:
        assert capture_output is True
        assert text is True
        assert check is False
        calls.append((command, cwd, env))
        return release.subprocess.CompletedProcess(command, 0, stdout="0.8.0\n", stderr="")

    monkeypatch.setattr(release.subprocess, "run", fake_run)

    release.wait_published("0.8.0", timeout_seconds=1)

    assert calls
    command, cwd, env = calls[0]
    assert cwd != Path(release.ROOT)
    assert "--with" in command
    assert "untaped-recipe==0.8.0" in command
    assert env is not None
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["UV_CACHE_DIR"] == str(cwd / "uv-cache")


def test_release_publish_package_filters_artifacts_by_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _release_module()
    dist = Path(release.ROOT) / "dist"
    published: list[list[str]] = []

    matching_wheel = dist / "untaped_recipe-0.8.0-py3-none-any.whl"
    matching_sdist = dist / "untaped_recipe-0.8.0.tar.gz"
    stale_wheel = dist / "untaped_recipe-0.7.0-py3-none-any.whl"
    unrelated = dist / "unrelated_package-0.8.0-py3-none-any.whl"

    def fake_glob(pattern: str) -> list[Path]:
        assert pattern == "untaped_recipe-0.8.0*"
        return [matching_wheel, matching_sdist, stale_wheel, unrelated]

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
        del cwd, env
        published.append(command)

    monkeypatch.setattr(type(dist), "glob", lambda self, pattern: fake_glob(pattern))
    monkeypatch.setattr(release, "_run", fake_run)

    release.publish_package("0.8.0")

    assert published == [
        [
            "uv",
            "publish",
            "--trusted-publishing",
            "always",
            str(matching_wheel),
            str(matching_sdist),
        ]
    ]


def _release_module() -> object:
    module_path = Path(__file__).parents[1] / "scripts" / "release.py"
    spec = importlib.util.spec_from_file_location("release", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_hook_api_exposes_yaml_option_types() -> None:
    from untaped_recipe.hook_api import HOOK_API_VERSION, YamlDumpOptions, YamlIndentOptions
    from untaped_recipe.hook_api import HookHelpers as ExternalHookHelpers

    indent: YamlIndentOptions = {"mapping": 2, "sequence": 4, "offset": 2}
    options: YamlDumpOptions = {"width": 120, "indent": indent}

    assert HOOK_API_VERSION == "0.9.0"
    assert options["indent"]["sequence"] == 4
    assert ExternalHookHelpers.__name__ == "HookHelpers"


def test_dump_yaml_applies_core_formatting_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class FakeYaml:
        def __init__(self) -> None:
            self.preserve_quotes: bool | None = None
            self.width: int | None = None
            self.block_seq_indent: int | None = None
            self.explicit_start: bool | None = None
            self.explicit_end: bool | None = None
            self.indent_calls: list[dict[str, int]] = []
            created.append(self)

        def indent(self, **kwargs: int) -> None:
            self.indent_calls.append(kwargs)

        def dump(self, data: object, out: object) -> None:
            del data
            out.write("dumped\n")

    monkeypatch.setattr(ruamel_io_module, "YAML", FakeYaml)

    result = ruamel_io_module.dump_yaml(
        {"items": [1]},
        options={
            "width": 120,
            "preserve_quotes": False,
            "indent": {"mapping": 2, "sequence": 4, "offset": 2},
            "block_seq_indent": 2,
            "explicit_start": True,
            "explicit_end": True,
        },
    )

    yaml = created[0]
    assert result == "dumped\n"
    assert yaml.preserve_quotes is False
    assert yaml.width == 120
    assert yaml.indent_calls == [{"mapping": 2, "sequence": 4, "offset": 2}]
    assert yaml.block_seq_indent == 2
    assert yaml.explicit_start is True
    assert yaml.explicit_end is True


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"preserve_quote": True}, "unsupported YAML dump option"),
        ({"indent": {"seqence": 4}}, "unsupported YAML indent option"),
        ({"width": "100"}, "must be an integer"),
        ({"width": True}, "must be an integer"),
        ({"preserve_quotes": "yes"}, "must be a boolean"),
        ({"indent": 2}, "must be a mapping"),
    ],
)
def test_dump_yaml_rejects_invalid_options(
    options: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        dump_yaml({"items": [1]}, options=options)


def test_dump_yaml_defaults_preserve_existing_in_process_formatting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    class FakeYaml:
        def __init__(self) -> None:
            self.preserve_quotes: bool | None = None
            self.width: int | None = None
            self.indent_calls: list[dict[str, int]] = []
            created.append(self)

        def indent(self, **kwargs: int) -> None:
            self.indent_calls.append(kwargs)

        def dump(self, data: object, out: object) -> None:
            del data
            out.write("dumped\n")

    monkeypatch.setattr(ruamel_io_module, "YAML", FakeYaml)

    assert ruamel_io_module.dump_yaml({"items": [1]}) == "dumped\n"

    yaml = created[0]
    assert yaml.preserve_quotes is True
    assert yaml.width == 4096
    assert yaml.indent_calls == []


def test_worker_yaml_dump_matches_in_process_defaults_and_options() -> None:
    data = {"items": ["x" * 100, "y" * 100]}

    assert WorkerHookHelpers().dump_yaml(data) == HookHelpers().dump_yaml(data)
    assert WorkerHookHelpers().dump_yaml(data, options={"width": 40}) == HookHelpers().dump_yaml(
        data,
        options={"width": 40},
    )
    assert WorkerHookHelpers().dump_yaml(data, options={"explicit_start": True}).startswith("---\n")


def test_hook_helpers_and_builtin_yaml_edit_preserve_round_trip_yaml(tmp_path: Path) -> None:
    helpers = HookHelpers()

    assert helpers.pass_("ok").status == "pass"
    assert helpers.warn("check").status == "warn"
    assert helpers.fail("bad").failed
    assert helpers.render_template("{{ name }}", {"name": "api"}) == "api"

    result = yaml_edit.transform(
        "# top\nservices:\n  - name: api\n    config:\n      old: true\n"
        '  - name: web\nquoted: "keep me"\n',
        inputs={"owner": "platform"},
        target=tmp_path,
        file=tmp_path / "config.yml",
        args={
            "edits": [
                {
                    "op": "merge",
                    "path": ["services", {"where": {"name": "api"}}, "config"],
                    "value": {"owner": "{{ owner }}"},
                },
                {
                    "op": "delete",
                    "path": ["services", {"where": {"name": "web"}}],
                },
                {"op": "set", "path": ["enabled"], "value": True},
            ]
        },
        helpers=helpers,
    )
    assert "# top" in result
    assert 'quoted: "keep me"' in result
    assert "owner: platform" in result
    assert "enabled: true" in result
    assert "name: web" not in result

    loaded = load_yaml(result)
    assert dump_yaml(loaded) == result

    empty = yaml_edit.transform(
        "",
        inputs={},
        target=tmp_path,
        file=tmp_path / "empty.yml",
        args={"edits": [{"op": "set", "path": ["enabled"], "value": True}]},
        helpers=helpers,
    )
    assert "enabled: true" in empty


def test_builtin_yaml_edit_forwards_unknown_token_policy(tmp_path: Path) -> None:
    helpers = HookHelpers()
    args = {
        "unknown_tokens": "keep",
        "edits": [
            {
                "op": "set",
                "path": ["ref"],
                "value": "${{ github.ref }}",
            }
        ],
    }

    result = yaml_edit.transform(
        "{}\n",
        inputs={},
        target=tmp_path,
        file=tmp_path / "config.yml",
        args=args,
        helpers=helpers,
    )

    assert "ref: ${{ github.ref }}" in result


def test_apply_recipe_rejects_recipe_source_symlink_escape(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n")
    (recipe_dir / "template.txt").symlink_to(outside)
    target = tmp_path / "target"
    target.mkdir()
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "template", "template": "template.txt", "dest": "out.txt"}],
        }
    )
    planner = ApplyRecipe(
        HookExecutor(
            HookResolver(),
            workers=UvHookWorkerPool(),
            helpers=HookHelpers(),
        )
    )

    with pytest.raises(ValueError, match="symlink"):
        planner(recipe=recipe, recipe_dir=recipe_dir, target=target, inputs={})


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({}, "edits"),
        ({"edits": [{"op": "explode", "path": ["a"], "value": 1}]}, "invalid op"),
        (
            {
                "edits": [
                    {
                        "op": "set",
                        "path": ["items", {"where": {"name": "missing"}}],
                        "value": 1,
                    }
                ]
            },
            "no list item",
        ),
        (
            {"edits": [{"op": "set", "path": ["a"], "value": "{{ missing }}"}]},
            "template input",
        ),
    ],
)
def test_builtin_yaml_edit_reports_bad_args(
    args: dict[str, object],
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=message):
        yaml_edit.transform(
            "items:\n  - name: api\n",
            inputs={},
            target=tmp_path,
            file=tmp_path / "config.yml",
            args=args,
            helpers=HookHelpers(),
        )


def test_parallel_bulk_plan_returns_ordered_errors_and_flushes_atomically(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("hello\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "steps": [{"type": "template", "template": "template.txt", "dest": "nested/out.txt"}],
        }
    )
    good = tmp_path / "good"
    good.mkdir()
    missing = tmp_path / "missing"
    runner = RunBulkApply(
        ApplyRecipe(
            HookExecutor(
                HookResolver(),
                workers=UvHookWorkerPool(),
                helpers=HookHelpers(),
            )
        )
    )

    plans = runner.plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=None,
        targets=[Target(path=good), Target(path=missing)],
        inputs={},
        parallel=2,
    )

    assert [plan.target for plan in plans] == [good, missing]
    assert [plan.status for plan in plans] == ["planned", "error"]
    flush_changes(plans[0].changes)
    assert (good / "nested" / "out.txt").read_text() == "hello\n"

    removable = good / "legacy.txt"
    removable.write_text("old\n")
    flush_changes(
        (
            FileChange(
                target=good,
                relative_path=Path("legacy.txt"),
                before="old\n",
                after=None,
            ),
        )
    )
    assert not removable.exists()


def test_bulk_plan_resolves_per_target_inputs_and_preserves_duplicate_order(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("service={{ service }}\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {
                    "type": "str",
                    "required": True,
                    "from": ["{{ record.repo }}", "{{ target.name }}"],
                },
                "token": {
                    "type": "str",
                    "scope": "global",
                    "sensitive": True,
                    "required": True,
                },
            },
            "steps": [{"type": "template", "template": "template.txt", "dest": "out.txt"}],
        }
    )
    target = tmp_path / "api"
    target.mkdir()
    other = tmp_path / "worker"
    other.mkdir()
    runner = RunBulkApply(
        ApplyRecipe(
            HookExecutor(
                HookResolver(),
                workers=UvHookWorkerPool(),
                helpers=HookHelpers(),
            )
        )
    )

    plans = runner.plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=None,
        targets=[
            Target(path=target, record={"repo": "first"}),
            Target(path=other, record={"repo": "second"}),
            Target(path=target, record={"repo": "third"}),
        ],
        inputs={"token": "secret"},
        parallel=3,
    )

    assert [plan.target for plan in plans] == [target, other, target]
    assert [plan.display_inputs["token"] for plan in plans] == ["***", "***", "***"]
    assert [plan.display_inputs["service"] for plan in plans] == ["first", "second", "third"]
    assert [plan.changes[0].after for plan in plans] == [
        "service=first\n",
        "service=second\n",
        "service=third\n",
    ]


def test_bulk_plan_error_rows_preserve_resolved_input_display(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    recipe_dir.mkdir()
    (recipe_dir / "template.txt").write_text("service={{ service }} token={{ token }}\n")
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "required": True, "from": "{{ target.name }}"},
                "token": {
                    "type": "str",
                    "scope": "global",
                    "sensitive": True,
                    "required": True,
                },
            },
            "steps": [{"type": "template", "template": "template.txt", "dest": "out.txt"}],
        }
    )
    missing = tmp_path / "missing"
    runner = RunBulkApply(
        ApplyRecipe(
            HookExecutor(
                HookResolver(),
                workers=UvHookWorkerPool(),
                helpers=HookHelpers(),
            )
        )
    )

    plans = runner.plan(
        recipe=recipe,
        recipe_dir=recipe_dir,
        local_hook_project=None,
        targets=[Target(path=missing)],
        inputs={"token": "secret"},
    )

    assert plans[0].status == "error"
    assert plans[0].display_inputs == {"service": "missing", "token": "***"}


def test_bulk_plan_input_resolution_errors_have_empty_inputs(tmp_path: Path) -> None:
    recipe = Recipe.model_validate(
        {
            "version": 1,
            "inputs": {
                "service": {"type": "str", "required": True, "from": "{{ record.repo }}"},
            },
            "steps": [],
        }
    )
    target = tmp_path / "api"
    target.mkdir()
    runner = RunBulkApply(
        ApplyRecipe(
            HookExecutor(
                HookResolver(),
                workers=UvHookWorkerPool(),
                helpers=HookHelpers(),
            )
        )
    )

    plans = runner.plan(
        recipe=recipe,
        recipe_dir=tmp_path,
        local_hook_project=None,
        targets=[Target(path=target)],
        inputs={},
    )

    assert plans[0].status == "error"
    assert plans[0].display_inputs == {}


def test_flush_changes_reports_rollback_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "one.txt"
    second = tmp_path / "two.txt"
    first.write_text("one-before\n")
    second.write_text("two-before\n")
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

    with pytest.raises(ApplyWriteError) as excinfo:
        flush_changes(
            (
                FileChange(
                    target=tmp_path,
                    relative_path=Path("one.txt"),
                    before="one-before\n",
                    after="one-after\n",
                ),
                FileChange(
                    target=tmp_path,
                    relative_path=Path("two.txt"),
                    before="two-before\n",
                    after="two-after\n",
                ),
            )
        )

    message = str(excinfo.value)
    assert "write failed" in message
    assert "rollback incomplete" in message
    assert "rollback denied" in message


def test_flush_changes_rejects_stale_files_without_overwriting(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    config = target / "config.yml"
    config.write_text("before\n")
    change = FileChange(
        target=target,
        relative_path=Path("config.yml"),
        before="before\n",
        after="after\n",
    )

    config.write_text("user edit\n")

    with pytest.raises(ApplyWriteError, match="changed since planning"):
        flush_changes((change,))
    assert config.read_text() == "user edit\n"


def test_flush_changes_rolls_back_target_when_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    first = target / "first.txt"
    second = target / "second.txt"
    first.write_text("old first\n")
    second.write_text("old second\n")
    changes = (
        FileChange(
            target=target,
            relative_path=Path("first.txt"),
            before="old first\n",
            after="new first\n",
        ),
        FileChange(
            target=target,
            relative_path=Path("second.txt"),
            before="old second\n",
            after="new second\n",
        ),
    )
    original_replace = file_writer_module.os.replace
    calls = 0

    def flaky_replace(src: Path, dst: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        original_replace(src, dst)

    monkeypatch.setattr(file_writer_module.os, "replace", flaky_replace)

    with pytest.raises(ApplyWriteError, match="disk full"):
        flush_changes(changes)
    assert first.read_text() == "old first\n"
    assert second.read_text() == "old second\n"


def test_flush_changes_stages_replacements_next_to_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    change = FileChange(
        target=target,
        relative_path=Path("nested/out.txt"),
        before=None,
        after="new\n",
    )
    original_replace = file_writer_module.os.replace
    observed: list[tuple[Path, Path]] = []

    def replace_requires_same_parent(src: Path, dst: Path) -> None:
        source = Path(src)
        destination = Path(dst)
        observed.append((source, destination))
        if source.parent != destination.parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        original_replace(source, destination)

    monkeypatch.setattr(file_writer_module.os, "replace", replace_requires_same_parent)

    flush_changes((change,))

    assert (target / "nested" / "out.txt").read_text() == "new\n"
    assert observed == [(observed[0][0], target / "nested" / "out.txt")]
    assert observed[0][0].parent == target / "nested"


def test_flush_changes_rejects_target_symlink_escape(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "linked").symlink_to(outside, target_is_directory=True)
    change = FileChange(
        target=target,
        relative_path=Path("linked/out.txt"),
        before=None,
        after="escaped\n",
    )

    with pytest.raises(ApplyWriteError, match="symlink"):
        flush_changes((change,))
    assert not (outside / "out.txt").exists()


def test_flush_changes_removes_created_directories_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    change = FileChange(
        target=target,
        relative_path=Path("created/dir/out.txt"),
        before=None,
        after="new\n",
    )

    def fail_replace(src: Path, dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(file_writer_module.os, "replace", fail_replace)

    with pytest.raises(ApplyWriteError, match="disk full"):
        flush_changes((change,))
    assert not (target / "created").exists()


def test_file_change_kind_reports_create_modify_and_remove(tmp_path: Path) -> None:
    target = tmp_path / "target"

    create = FileChange(target=target, relative_path=Path("a"), before=None, after="x")
    modify = FileChange(target=target, relative_path=Path("a"), before="x", after="y")
    remove = FileChange(target=target, relative_path=Path("a"), before="x", after=None)

    assert create.kind == "create"
    assert modify.kind == "modify"
    assert remove.kind == "remove"
