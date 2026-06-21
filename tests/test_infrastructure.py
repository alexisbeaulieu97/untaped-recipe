"""Additional infrastructure and orchestration contract tests."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

import untaped_recipe.infrastructure.file_writer as file_writer_module
import untaped_recipe.infrastructure.hook_library as hook_library_module
from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.run_bulk import ApplyWriteError, RunBulkApply, flush_changes
from untaped_recipe.application.targets import Target
from untaped_recipe.builtins.hooks import yaml_edit
from untaped_recipe.domain.plan import FileChange
from untaped_recipe.domain.recipe import Recipe
from untaped_recipe.infrastructure.hook_executor import HookExecutor
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_library import HookLibrary, add_hook_to_project
from untaped_recipe.infrastructure.hook_resolver import HookResolver
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.recipe_library import RecipeLibrary
from untaped_recipe.infrastructure.ruamel_io import dump_yaml, load_yaml


def test_recipe_library_project_crud_and_errors(tmp_path: Path) -> None:
    root = tmp_path / "library"
    single_source = tmp_path / "single-source"
    _write_recipe_project(single_source, recipe_id="single")
    package_source = tmp_path / "package-source"
    _write_recipe_project(package_source, recipe_id="package")
    library = RecipeLibrary(root)

    assert library.list() == []
    single = library.add(single_source)
    package = library.add(package_source)

    assert library.resolve("single") == single / "recipe.yml"
    assert library.resolve("package") == package / "recipe.yml"
    assert [entry.kind for entry in library.list()] == ["recipe", "recipe"]
    with pytest.raises(ValueError, match="already exists"):
        library.add(single_source)
    with pytest.raises(ValueError, match="source not found"):
        library.add(tmp_path / "missing.yml")
    file_source = tmp_path / "single.yml"
    file_source.write_text("version: 1\nsteps: []\n")
    with pytest.raises(ValueError, match="uv recipe project directory"):
        library.add(file_source)

    assert library.remove("single") == single
    assert library.remove("package") == root / "recipes" / "package"
    with pytest.raises(ValueError, match="recipe not found"):
        library.resolve("single")
    with pytest.raises(ValueError, match="recipe not found"):
        library.remove("package")


def _write_recipe_project(root: Path, *, recipe_id: str) -> None:
    root.mkdir(parents=True)
    (root / "recipe.yml").write_text("version: 1\nsteps: []\n")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{recipe_id}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.recipes]\n"
        f'"{recipe_id}" = {{ path = "recipe.yml" }}\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


def test_hook_library_crud_and_errors(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_hook_project(source, hook_name="shared")
    library = HookLibrary(tmp_path / "library")

    assert library.list() == []
    hook = library.add(source)

    assert hook == tmp_path / "library" / "hooks" / "shared"
    assert library.resolve("shared") == hook
    assert library.resolve(str(source)) == source
    entries = library.list()
    assert [entry.name for entry in entries] == ["shared"]
    assert entries[0].hooks == ("shared",)
    assert library.resolve_editable("shared").name == "shared.py"
    with pytest.raises(ValueError, match="already exists"):
        library.add(source, name="shared")
    with pytest.raises(ValueError, match="must match declared hook namespace"):
        library.add(source, name="wrong")
    with pytest.raises(ValueError, match="source not found"):
        library.add(tmp_path / "missing")
    (tmp_path / "not-a-dir.py").write_text("VALUE = 1\n")
    with pytest.raises(ValueError, match="uv hook project directory"):
        library.add(tmp_path / "not-a-dir.py")

    assert library.remove("shared") == hook
    with pytest.raises(ValueError, match="hook not found"):
        library.resolve("shared")
    with pytest.raises(ValueError, match="hook not found"):
        library.remove("shared")


def test_scoped_hook_init_rolls_back_on_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = RecipeLibrary(tmp_path / "library").init("demo", base_dir=tmp_path)
    before = (project / "pyproject.toml").read_text()

    def fail_lock(project_root: Path) -> None:
        raise ValueError(f"lock failed: {project_root}")

    monkeypatch.setattr(hook_library_module, "lock_project", fail_lock, raising=False)

    with pytest.raises(ValueError, match="lock failed"):
        add_hook_to_project(project, "check", kind="validate")

    assert (project / "pyproject.toml").read_text() == before
    assert not (project / "src" / "demo_hooks" / "hooks" / "check.py").exists()
    assert not (project / "src" / "demo_hooks").exists()


def test_hook_library_add_rejects_empty_or_mixed_namespace_projects(tmp_path: Path) -> None:
    library = HookLibrary(tmp_path / "library")
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "pyproject.toml").write_text(
        "[project]\n"
        'name = "empty-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.hooks]\n"
    )
    (empty / "uv.lock").write_text("version = 1\n")
    mixed = tmp_path / "mixed"
    _write_hook_project(mixed, hook_name="ansible.add_play_collections")
    with (mixed / "pyproject.toml").open("a") as pyproject:
        pyproject.write('"awx.update_job_template" = { module = "shared_hooks.hooks.awx" }\n')

    with pytest.raises(ValueError, match="at least one hook"):
        library.add(empty)
    with pytest.raises(ValueError, match="same namespace"):
        library.add(mixed)


def test_hook_library_namespaced_add_show_remove_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_hook_project(
        source,
        hook_name="ansible.add_play_collections",
        module_name="add_play_collections",
    )
    library = HookLibrary(tmp_path / "library")

    added = library.add(source)

    assert added == tmp_path / "library" / "hooks" / "ansible"
    assert library.resolve("ansible.add_play_collections") == added
    assert library.resolve_editable("ansible.add_play_collections").name == (
        "add_play_collections.py"
    )
    assert library.remove("ansible.add_play_collections") == added


def test_hook_library_rejects_declared_modules_missing_from_src(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_hook_project(source, hook_name="shared")
    (source / "src" / "shared_hooks" / "hooks" / "shared.py").unlink()
    library = HookLibrary(tmp_path / "library")

    with pytest.raises(ValueError, match="hook module file not found"):
        library.add(source)


def test_hook_library_bare_names_do_not_resolve_cwd_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write_hook_project(source, hook_name="shared")
    library = HookLibrary(tmp_path / "library")
    added = library.add(source)
    cwd_project = tmp_path / "shared"
    _write_hook_project(cwd_project, hook_name="other")
    monkeypatch.chdir(tmp_path)

    assert library.resolve("shared") == added
    assert library.resolve("./shared") == cwd_project


def test_hook_library_init_cleans_up_partial_project_on_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = HookLibrary(tmp_path / "library")

    def fail_lock(project_root: Path) -> None:
        raise ValueError("failed to create project uv.lock")

    monkeypatch.setattr(hook_library_module, "lock_project", fail_lock)

    with pytest.raises(ValueError, match=r"failed to create project uv\.lock"):
        library.init("check")

    assert not (tmp_path / "library" / "hooks" / "check").exists()
    assert library.list() == []


def _write_hook_project(root: Path, *, hook_name: str, module_name: str | None = None) -> None:
    package = "shared_hooks"
    (root / "src" / package / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    module_leaf = module_name or hook_name.rsplit(".", maxsplit=1)[-1]
    (root / "src" / package / "hooks" / f"{module_leaf}.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "shared-hooks"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.hooks]\n"
        f'"{hook_name}" = {{ module = "{package}.hooks.{module_leaf}" }}\n'
    )
    (root / "uv.lock").write_text("version = 1\n")


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
            HookResolver(global_hooks=tmp_path / "global"),
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
                HookResolver(global_hooks=tmp_path / "global"),
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
                HookResolver(global_hooks=tmp_path / "global"),
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
                HookResolver(global_hooks=tmp_path / "global"),
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
                HookResolver(global_hooks=tmp_path / "global"),
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
