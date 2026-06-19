"""Tests for uv-managed hook projects and worker execution."""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from untaped_recipe.domain.hook_project import HookProjectMetadata
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.hook_executor import HookExecutor
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_resolver import BuiltinHookRef, HookResolver, UvHookRef
from untaped_recipe.infrastructure.hook_worker_client import HookWorkerResponse, UvHookWorker


def _write_hook_project(
    root: Path,
    *,
    hooks: dict[str, str],
    package: str = "project_hooks",
    lock: bool = True,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "hooks").mkdir(parents=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    hook_rows = "\n".join(
        f'"{public_name}" = {{ module = "{module}" }}'
        for public_name, module in sorted(hooks.items())
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{root.name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe.hooks]\n"
        f"{hook_rows}\n"
    )
    if lock:
        (root / "uv.lock").write_text("version = 1\n")


def test_hook_project_metadata_validates_pyproject_hook_table() -> None:
    metadata = HookProjectMetadata.from_pyproject(
        {
            "tool": {
                "untaped_recipe": {
                    "hooks": {
                        "ansible.add_play_collections": {
                            "module": "project_hooks.hooks.add_play_collections"
                        }
                    }
                }
            }
        }
    )

    assert metadata.hooks["ansible.add_play_collections"].module == (
        "project_hooks.hooks.add_play_collections"
    )

    with pytest.raises(ValueError, match="invalid hook name"):
        HookProjectMetadata.from_pyproject(
            {"tool": {"untaped_recipe": {"hooks": {"bad-name": {"module": "pkg.hook"}}}}}
        )

    with pytest.raises(ValueError, match="module is required"):
        HookProjectMetadata.from_pyproject({"tool": {"untaped_recipe": {"hooks": {"check": {}}}}})


def test_hook_resolver_uses_recipe_local_global_namespaced_then_builtin(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    global_hooks = tmp_path / "library" / "hooks"
    _write_hook_project(
        recipe_dir,
        hooks={"pick": "local_hooks.hooks.pick"},
        package="local_hooks",
    )
    _write_hook_project(
        global_hooks / "pick",
        hooks={"pick": "global_hooks.hooks.pick"},
        package="global_hooks",
    )
    _write_hook_project(
        global_hooks / "ansible",
        hooks={"ansible.add_play_collections": "ansible_hooks.hooks.add_play_collections"},
        package="ansible_hooks",
    )
    resolver = HookResolver(global_hooks=global_hooks)

    local = resolver.resolve("pick", recipe_dir)
    assert isinstance(local, UvHookRef)
    assert local.project_root == recipe_dir
    assert local.module == "local_hooks.hooks.pick"

    global_ref = resolver.resolve("ansible.add_play_collections", recipe_dir)
    assert isinstance(global_ref, UvHookRef)
    assert global_ref.project_root == global_hooks / "ansible"
    assert global_ref.module == "ansible_hooks.hooks.add_play_collections"

    builtin = resolver.resolve("yaml_edit", recipe_dir)
    assert isinstance(builtin, BuiltinHookRef)
    assert builtin.public_name == "yaml_edit"


def test_hook_resolver_rejects_missing_lockfile(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        lock=False,
    )

    with pytest.raises(ValueError, match=r"missing uv\.lock"):
        HookResolver(global_hooks=tmp_path / "hooks").resolve("check", recipe_dir)


def test_worker_response_validation_rejects_malformed_protocol_rows() -> None:
    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"ok": True, "result": "value"})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": "1", "ok": False})


def test_uv_hook_worker_reports_missing_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_popen(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    with pytest.raises(ValueError, match="uv executable not found"):
        UvHookWorker(tmp_path)


def test_uv_hook_worker_rejects_malformed_json_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout="not-json\n")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(ValueError, match="malformed hook worker response"):
        worker.request({"kind": "transform", "module": "hooks.sample"})


def test_uv_hook_worker_rejects_response_id_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout='{"id": "wrong", "ok": true, "result": "after"}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(ValueError, match="response id mismatch"):
        worker.request({"kind": "transform", "module": "hooks.sample"})


def test_hook_executor_dispatches_builtin_without_worker(tmp_path: Path) -> None:
    class ExplodingWorkers:
        def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
            raise AssertionError("worker should not be used for built-ins")

    executor = HookExecutor(
        HookResolver(global_hooks=tmp_path / "hooks"),
        workers=ExplodingWorkers(),
        helpers=HookHelpers(),
    )

    result = executor.transform(
        "yaml_edit",
        "enabled: false\n",
        recipe_dir=tmp_path,
        target=tmp_path,
        file=tmp_path / "config.yml",
        inputs={},
        args={"edits": [{"op": "set", "path": ["enabled"], "value": True}]},
    )

    assert "enabled: true" in result


def test_hook_executor_sends_external_transform_to_worker(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"suffix": "project_hooks.hooks.suffix"})
    calls: list[dict[str, object]] = []

    class RecordingWorkers:
        def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
            calls.append({"ref": ref, "payload": payload})
            return "after\n"

    executor = HookExecutor(
        HookResolver(global_hooks=tmp_path / "hooks"),
        workers=RecordingWorkers(),
        helpers=HookHelpers(),
    )

    result = executor.transform(
        "suffix",
        "before\n",
        recipe_dir=recipe_dir,
        target=tmp_path / "target",
        file=tmp_path / "target" / "local.yml",
        inputs={"service": "api"},
        args={"flag": True},
    )

    assert result == "after\n"
    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert payload["kind"] == "transform"
    assert payload["content"] == "before\n"
    assert payload["target"] == str(tmp_path / "target")
    assert payload["file"] == str(tmp_path / "target" / "local.yml")


def test_worker_script_executes_hooks_and_redirects_prints_to_stderr(tmp_path: Path) -> None:
    package = tmp_path / "src" / "worker_hooks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sample.py").write_text(
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    print('diagnostic from hook')\n"
        "    return content + inputs['suffix'] + '\\n'\n"
        "\n"
        "def validate(*, inputs, target, args, helpers):\n"
        "    return helpers.warn('check warning')\n"
    )
    worker = Path(__file__).parents[1] / "src" / "untaped_recipe" / "hook_worker.py"
    proc = subprocess.Popen(
        [sys.executable, str(worker)],
        cwd=tmp_path,
        env={"PYTHONPATH": str(tmp_path / "src")},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    request = {
        "id": "1",
        "kind": "transform",
        "module": "worker_hooks.sample",
        "content": "before ",
        "inputs": {"suffix": "after"},
        "target": str(tmp_path),
        "file": str(tmp_path / "local.yml"),
        "args": {},
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()

    response = json.loads(proc.stdout.readline())
    proc.stdin.close()
    stderr = proc.stderr.read()
    proc.wait(timeout=10)

    assert response == {"id": "1", "ok": True, "result": "before after\n"}
    assert "diagnostic from hook" in stderr


def test_hook_executor_coerces_external_validate_verdict(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"check": "project_hooks.hooks.check"})

    class WarningWorkers:
        def request(self, ref: UvHookRef, payload: dict[str, object]) -> object:
            return {"status": "warn", "message": "check this"}

    executor = HookExecutor(
        HookResolver(global_hooks=tmp_path / "hooks"),
        workers=WarningWorkers(),
        helpers=HookHelpers(),
    )

    verdict = executor.validate(
        "check",
        recipe_dir=recipe_dir,
        target=tmp_path / "target",
        inputs={},
        args={},
    )

    assert verdict == Verdict(status="warn", message="check this")


class _FakeProcess:
    def __init__(self, *, stdout: str) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO(stdout)
        self.stderr = StringIO()

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None
