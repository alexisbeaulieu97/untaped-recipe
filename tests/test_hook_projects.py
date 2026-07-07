"""Tests for uv-managed hook projects and worker execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import suppress
from io import StringIO
from pathlib import Path
from threading import Barrier, Event
from typing import Literal

import pytest
from pydantic import ValidationError

import untaped_recipe.infrastructure.hook_resolver as hook_resolver_module
import untaped_recipe.infrastructure.hook_worker_client as worker_client
from untaped_recipe.domain.hook_project import HookProjectMetadata, read_hook_metadata
from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.hook_executor import HookExecutor
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_resolver import (
    BuiltinHookRef,
    HookResolver,
    UvHookRef,
    ensure_hook_supports,
)
from untaped_recipe.infrastructure.hook_worker_client import (
    HookWorkerCallResult,
    HookWorkerResponse,
    UvHookWorker,
    UvHookWorkerPool,
)


def _write_hook_project(
    root: Path,
    *,
    hooks: dict[str, str],
    package: str = "project_hooks",
    kind: Literal["transform", "validate"] | None = None,
    exports: tuple[Literal["transform", "validate"], ...] = ("transform",),
    lock: bool = True,
    dependencies: list[str] | None = None,
    requires_hook_api: str | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "src" / package / "hooks").mkdir(parents=True)
    (root / "src" / package / "__init__.py").write_text("")
    (root / "src" / package / "hooks" / "__init__.py").write_text("")
    for module in hooks.values():
        module_path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(_hook_source(exports))
    hook_row_values = []
    for public_name, module in sorted(hooks.items()):
        if kind is None:
            hook_row_values.append(f'"{public_name}" = {{ module = "{module}" }}')
        else:
            hook_row_values.append(f'"{public_name}" = {{ kind = "{kind}", module = "{module}" }}')
    hook_rows = "\n".join(hook_row_values)
    dependency_rows = ", ".join(json.dumps(dependency) for dependency in dependencies or [])
    tool_table = (
        f'[tool.untaped_recipe]\nrequires_hook_api = "{requires_hook_api}"\n\n'
        if requires_hook_api is not None
        else ""
    )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "{root.name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        f"dependencies = [{dependency_rows}]\n\n"
        f"{tool_table}"
        "[tool.untaped_recipe.hooks]\n"
        f"{hook_rows}\n"
    )
    if lock:
        (root / "uv.lock").write_text("version = 1\n")


def _hook_source(exports: tuple[Literal["transform", "validate"], ...]) -> str:
    parts: list[str] = []
    if "transform" in exports:
        parts.append(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
        )
    if "validate" in exports:
        parts.append(
            "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n"
        )
    return "\n".join(parts)


def test_hook_project_metadata_validates_pyproject_hook_table() -> None:
    metadata = HookProjectMetadata.from_pyproject(
        {
            "tool": {
                "untaped_recipe": {
                    "hooks": {
                        "ansible.add_play_collections": {
                            "module": "project_hooks.hooks.add_play_collections",
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

    with pytest.raises(ValueError, match=r"kind was removed in 0\.9"):
        HookProjectMetadata.from_pyproject(
            {
                "tool": {
                    "untaped_recipe": {
                        "hooks": {"check": {"kind": "template", "module": "pkg.hook"}}
                    }
                }
            }
        )


def test_manifest_kind_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / "recipe"
    _write_hook_project(
        project_root,
        hooks={"x": "project_hooks.hooks.x"},
        kind="transform",
    )

    with pytest.raises(ValueError, match=r"kind was removed in 0\.9"):
        HookProjectMetadata.from_pyproject(
            {
                "tool": {
                    "untaped_recipe": {
                        "hooks": {
                            "x": {
                                "kind": "transform",
                                "module": "project_hooks.hooks.x",
                            }
                        }
                    }
                }
            }
        )
    with pytest.raises(ValueError, match=r"kind was removed in 0\.9"):
        read_hook_metadata(project_root)


def test_hook_resolver_uses_recipe_local_then_builtin(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"pick": "local_hooks.hooks.pick"},
        package="local_hooks",
    )
    resolver = HookResolver()

    local = resolver.resolve("pick", recipe_dir)
    assert isinstance(local, UvHookRef)
    assert local.project_root == recipe_dir
    assert local.module == "local_hooks.hooks.pick"
    assert local.exports == frozenset({"transform"})

    builtin = resolver.resolve("yaml_edit", recipe_dir)
    assert isinstance(builtin, BuiltinHookRef)
    assert builtin.exports == frozenset({"transform"})


def test_resolver_carries_exports_from_ast_scan(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        exports=("validate",),
    )
    resolver = HookResolver()

    ref = resolver.resolve("check", recipe_dir)

    assert isinstance(ref, UvHookRef)
    assert ref.exports == frozenset({"validate"})


def test_ensure_hook_supports_rejects_missing_verb(tmp_path: Path) -> None:
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with pytest.raises(ValueError, match=r"does not export a validate\(\) function"):
        ensure_hook_supports(ref, "sample", verb="validate")


def test_hook_resolver_rejects_missing_lockfile(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        lock=False,
    )

    with pytest.raises(ValueError, match=r"missing uv\.lock"):
        HookResolver().resolve("check", recipe_dir)


def test_hook_resolver_rejects_runtime_untaped_recipe_dependency(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        dependencies=["untaped-recipe>=0.7"],
    )

    with pytest.raises(ValueError, match="must not depend on untaped-recipe"):
        HookResolver().resolve("check", recipe_dir)


@pytest.mark.parametrize(
    "dependency",
    [
        "Untaped_Recipe[hooks]>=0.7; python_version >= '3.14'",
        "untaped-recipe @ git+https://example.invalid/untaped-recipe.git",
    ],
)
def test_hook_resolver_rejects_pep508_runtime_untaped_recipe_dependencies(
    tmp_path: Path,
    dependency: str,
) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        dependencies=[dependency],
    )

    with pytest.raises(ValueError, match="must not depend on untaped-recipe"):
        HookResolver().resolve("check", recipe_dir)


def test_hook_project_metadata_rejects_invalid_dependency_declarations() -> None:
    with pytest.raises(ValueError, match=r"\[project\]\.dependencies entry"):
        HookProjectMetadata.from_pyproject(
            {
                "project": {"dependencies": ["not a valid @@@ requirement"]},
                "tool": {
                    "untaped_recipe": {
                        "hooks": {
                            "check": {
                                "module": "project_hooks.hooks.check",
                            }
                        }
                    }
                },
            }
        )


def test_hook_resolver_rejects_newer_required_hook_api(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        requires_hook_api=">=99",
    )

    with pytest.raises(ValueError, match="requires hook API >=99"):
        HookResolver().resolve("check", recipe_dir)


def test_hook_resolver_ignores_unrelated_local_project_contract_for_builtin(
    tmp_path: Path,
) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={},
        dependencies=["untaped-recipe>=0.8"],
    )

    ref = HookResolver().resolve("yaml_edit", recipe_dir)

    assert isinstance(ref, BuiltinHookRef)
    assert ref.name == "yaml_edit"


def test_hook_resolver_rejects_missing_declared_module_file(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"check": "project_hooks.hooks.check"})
    (recipe_dir / "src" / "project_hooks" / "hooks" / "check.py").unlink()

    with pytest.raises(ValueError, match="hook module file not found"):
        HookResolver().resolve("check", recipe_dir)


def test_hook_resolver_caches_metadata_for_apply_lifetime(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"check": "project_hooks.hooks.check"})
    resolver = HookResolver()

    first = resolver.resolve("check", recipe_dir)
    (recipe_dir / "pyproject.toml").write_text("not toml = [\n")
    second = resolver.resolve("check", recipe_dir)

    assert isinstance(first, UvHookRef)
    assert isinstance(second, UvHookRef)
    assert second.module == "project_hooks.hooks.check"


def test_hook_resolver_validates_project_contract_once_per_metadata_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"check": "project_hooks.hooks.check"})
    calls: list[Path] = []

    def validate(project_root: Path, metadata: HookProjectMetadata) -> None:
        del metadata
        calls.append(project_root)

    monkeypatch.setattr(hook_resolver_module, "validate_hook_project_contract", validate)
    resolver = HookResolver()

    resolver.resolve("check", recipe_dir)
    resolver.resolve("check", recipe_dir)

    assert calls == [recipe_dir]


def test_worker_response_validation_rejects_malformed_protocol_rows() -> None:
    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"ok": True, "result": "value"})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": "1", "ok": False})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": "1", "ok": False, "error": "bad", "result": ""})

    with pytest.raises(ValidationError):
        HookWorkerResponse.model_validate({"id": 1, "ok": True, "result": "value"})


def test_uv_hook_worker_reports_missing_uv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_popen(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError("uv")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    with pytest.raises(ValueError, match="uv executable not found"):
        UvHookWorker(tmp_path)


def test_uv_hook_worker_excludes_dev_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    call_kwargs: list[dict[str, object]] = []

    def popen(args: list[str], **kwargs: object) -> _FakeProcess:
        calls.append(args)
        call_kwargs.append(kwargs)
        return _FakeProcess(stdout="")

    monkeypatch.setattr(subprocess, "Popen", popen)

    UvHookWorker(tmp_path)

    assert calls
    assert "--no-dev" in calls[0]
    assert calls[0].index("--no-dev") < calls[0].index("python")
    assert call_kwargs[0]["start_new_session"] is True


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


def test_uv_hook_worker_times_out_and_closes_hung_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _SlowProcess(delay=0.2)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path, hook_timeout_seconds=0.01)

    start = time.monotonic()
    with pytest.raises(worker_client.FatalHookWorkerError, match="timed out"):
        worker.request({"kind": "transform", "module": "hooks.sample"})

    assert time.monotonic() - start < 0.15
    assert fake.killed


def test_hook_timeout_starts_after_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _DelayedReadyProcess(
        ready_delay=0.1,
        lines=['{"id": "1", "ok": true, "result": "after"}\n'],
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path, hook_timeout_seconds=0.05, startup_timeout_seconds=5)

    result = worker.request({"kind": "transform", "module": "hooks.sample"})

    assert result.result == "after"


def test_startup_timeout_names_environment_not_hook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _SlowProcess(delay=0.2, ready=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path, hook_timeout_seconds=60, startup_timeout_seconds=0.01)

    with pytest.raises(worker_client.FatalHookWorkerError) as exc_info:
        worker.request({"kind": "transform", "module": "hooks.sample"})

    message = str(exc_info.value)
    assert "not ready" in message
    assert "environment" in message
    assert "hook worker timed out after" not in message
    assert fake.killed


def test_startup_notice_fires_once_per_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(
        stdout=(
            '{"id": "1", "ok": true, "result": "one"}\n{"id": "2", "ok": true, "result": "two"}\n'
        )
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    notices: list[Path] = []
    worker = UvHookWorker(tmp_path, startup_notice=notices.append)

    worker.request({"kind": "transform", "module": "hooks.sample"})
    worker.request({"kind": "transform", "module": "hooks.sample"})

    assert notices == [tmp_path]


def test_worker_exits_before_ready_reports_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout="", ready=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(worker_client.FatalHookWorkerError, match="exited before ready"):
        worker.request({"kind": "transform", "module": "hooks.sample"})


def test_malformed_handshake_line_is_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout="hello\n", ready=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(worker_client.FatalHookWorkerError, match="malformed hook worker handshake"):
        worker.request({"kind": "transform", "module": "hooks.sample"})


def test_worker_exits_before_request_reports_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout="")
    fake.stdin = _BrokenStdin()  # type: ignore[assignment]
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(worker_client.FatalHookWorkerError, match="exited before request"):
        worker.request({"kind": "transform", "module": "hooks.sample"})


def test_worker_exits_before_response_reports_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout="")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(worker_client.FatalHookWorkerError, match="exited before response"):
        worker.request({"kind": "transform", "module": "hooks.sample"})


def test_uv_hook_worker_rejects_non_json_serializable_request_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import datetime as dt

    fake = _FakeProcess(stdout='{"id": "1", "ok": true, "result": "after"}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    with pytest.raises(ValueError, match="is not JSON-serializable"):
        worker.request(
            {
                "kind": "transform",
                "module": "hooks.sample",
                "args": {"day": dt.date(2026, 6, 19)},
            }
        )

    assert fake.stdin.getvalue() == ""


def test_uv_hook_worker_preserves_json_serializable_request_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload_args = {
        "enabled": True,
        "count": 2,
        "labels": ["api", "worker"],
        "options": {"mode": "strict", "empty": None},
    }
    fake = _FakeProcess(stdout='{"id": "1", "ok": true, "result": "after"}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)

    result = worker.request(
        {
            "kind": "transform",
            "module": "hooks.sample",
            "args": payload_args,
        }
    )

    sent = json.loads(fake.stdin.getvalue())
    assert result.result == "after"
    assert sent["args"] == payload_args


def test_uv_hook_worker_discards_success_diagnostics_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(
        stdout=(
            '{"id": "1", "ok": true, "result": "after"}\n'
            '{"id": "2", "ok": false, "error": "failed"}\n'
        )
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)
    worker._stderr.put("success diagnostic\n")

    assert worker.request({"kind": "transform", "module": "hooks.sample"}).result == "after"
    worker._stderr.put("failure diagnostic\n")

    with pytest.raises(ValueError) as excinfo:
        worker.request({"kind": "transform", "module": "hooks.sample"})

    message = str(excinfo.value)
    assert "failed" in message
    assert "failure diagnostic" in message
    assert "success diagnostic" not in message


def test_uv_hook_worker_request_returns_result_and_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout='{"id": "1", "ok": true, "result": "after"}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)
    worker._stderr.put("success diagnostic\n")

    result = worker.request(
        {"kind": "transform", "module": "hooks.sample"},
        diagnostic_limit=10_000,
        settle_seconds=0.05,
    )

    assert result.result == "after"
    assert result.diagnostics == "success diagnostic"


def test_uv_hook_worker_survives_non_utf8_stderr(tmp_path: Path) -> None:
    _write_hook_project(
        tmp_path,
        hooks={"noisy": "project_hooks.hooks.noisy"},
    )
    module = tmp_path / "src" / "project_hooks" / "hooks" / "noisy.py"
    module.write_text(
        "import sys\n\n"
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    sys.stderr.buffer.write(b'\\xff\\xfe\\n')\n"
        "    sys.stderr.flush()\n"
        "    return content\n"
    )
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    (tmp_path / "uv.lock").unlink()
    subprocess.run(["uv", "lock", "--project", str(tmp_path)], check=True, env=env)
    target = tmp_path / "target"
    target.mkdir()
    worker = UvHookWorker(tmp_path)
    try:
        result = worker.request(
            {
                "kind": "transform",
                "module": "project_hooks.hooks.noisy",
                "content": "before",
                "target": str(target),
                "file": str(target / "config.txt"),
                "inputs": {},
                "args": {},
            },
            diagnostic_limit=None,
            settle_seconds=0.1,
        )
    finally:
        worker.close()

    assert result.result == "before"
    assert "\ufffd\ufffd" in result.diagnostics


def test_uv_hook_worker_diagnostics_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProcess(stdout='{"id": "1", "ok": true, "result": "after"}\n')
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    worker = UvHookWorker(tmp_path)
    worker._stderr.put("12345\n")
    worker._stderr.put("67890\n")

    result = worker.request(
        {"kind": "transform", "module": "hooks.sample"},
        diagnostic_limit=7,
    )

    assert result.result == "after"
    assert len(result.diagnostics) <= 7
    assert "12345" not in result.diagnostics


def test_uv_hook_worker_pool_leases_parallel_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(2)
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float = 60,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            self.worker_id = len(workers) + 1
            workers.append(self)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            barrier.wait(timeout=5)
            return HookWorkerCallResult(result=self.worker_id, diagnostics="")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with (
        UvHookWorkerPool(max_workers_per_project=2) as pool,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        results = [
            result.result
            for result in executor.map(lambda _: pool.request(ref, {"kind": "transform"}), range(2))
        ]

    assert sorted(results) == [1, 2]
    assert len(workers) == 2
    assert all(worker.closed for worker in workers)


def test_uv_hook_worker_pool_reuses_idle_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float = 60,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            self.worker_id = len(workers) + 1
            workers.append(self)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            return HookWorkerCallResult(result=self.worker_id, diagnostics="")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with UvHookWorkerPool(max_workers_per_project=3) as pool:
        results = [pool.request(ref, {"kind": "transform"}).result for _ in range(3)]

    assert results == [1, 1, 1]
    assert len(workers) == 1
    assert all(worker.closed for worker in workers)


def test_uv_hook_worker_pool_passes_timeout_to_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeouts: list[float] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            timeouts.append(hook_timeout_seconds)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            return HookWorkerCallResult(result="ok", diagnostics="")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with UvHookWorkerPool(max_workers_per_project=1, hook_timeout_seconds=12) as pool:
        assert pool.request(ref, {"kind": "transform"}).result == "ok"

    assert timeouts == [12]


def test_uv_hook_worker_pool_retires_workers_after_fatal_protocol_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float = 60,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            self.worker_id = len(workers) + 1
            workers.append(self)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            if self.worker_id == 1:
                raise worker_client.FatalHookWorkerError("malformed hook worker response")
            return HookWorkerCallResult(result=self.worker_id, diagnostics="")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with UvHookWorkerPool(max_workers_per_project=1) as pool:
        with pytest.raises(ValueError, match="malformed hook worker response"):
            pool.request(ref, {"kind": "transform"})
        assert pool.request(ref, {"kind": "transform"}).result == 2

    assert len(workers) == 2
    assert workers[0].closed
    assert workers[1].closed


def test_uv_hook_worker_pool_wakes_waiters_after_fatal_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_request_started = Event()
    fail_first_request = Event()
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float = 60,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            self.worker_id = len(workers) + 1
            workers.append(self)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            if self.worker_id == 1:
                first_request_started.set()
                assert fail_first_request.wait(timeout=5)
                raise worker_client.FatalHookWorkerError("hook worker timed out")
            return HookWorkerCallResult(result=self.worker_id, diagnostics="")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with (
        UvHookWorkerPool(max_workers_per_project=1) as pool,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        first = executor.submit(pool.request, ref, {"kind": "transform"})
        assert first_request_started.wait(timeout=5)
        second = executor.submit(pool.request, ref, {"kind": "transform"})
        time.sleep(0.05)

        fail_first_request.set()

        with pytest.raises(worker_client.FatalHookWorkerError, match="timed out"):
            first.result(timeout=5)
        try:
            assert second.result(timeout=1).result == 2
        except FutureTimeoutError as exc:
            msg = "waiting request was not woken after fatal retirement"
            raise AssertionError(msg) from exc

    assert len(workers) == 2
    assert workers[0].closed
    assert workers[1].closed


def test_uv_hook_worker_pool_reuses_workers_after_hook_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float = 60,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            self.worker_id = len(workers) + 1
            self.calls = 0
            workers.append(self)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            self.calls += 1
            if self.calls == 1:
                raise ValueError("validate hook failed")
            return HookWorkerCallResult(result=self.worker_id, diagnostics="")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )

    with UvHookWorkerPool(max_workers_per_project=1) as pool:
        with pytest.raises(ValueError, match="validate hook failed"):
            pool.request(ref, {"kind": "validate"})
        assert pool.request(ref, {"kind": "validate"}).result == 1

    assert len(workers) == 1
    assert workers[0].closed


def test_uv_hook_worker_pool_close_survives_one_failing_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = Barrier(3)
    workers: list[object] = []

    class FakeWorker:
        def __init__(
            self,
            project_root: Path,
            *,
            hook_timeout_seconds: float = 60,
            startup_timeout_seconds: float = 300,
            startup_notice: object = None,
        ) -> None:
            self.project_root = project_root
            self.closed = False
            self.worker_id = len(workers) + 1
            workers.append(self)

        def request(
            self,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            barrier.wait(timeout=5)
            return HookWorkerCallResult(result=self.worker_id, diagnostics="")

        def close(self) -> None:
            self.closed = True
            if self.worker_id == 2:
                raise BrokenPipeError("worker stdin already closed")

    monkeypatch.setattr(worker_client, "UvHookWorker", FakeWorker)
    ref = UvHookRef(
        name="sample",
        exports=frozenset({"transform"}),
        project_root=tmp_path,
        module="hooks.sample",
    )
    pool = UvHookWorkerPool(max_workers_per_project=3)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = [
            result.result
            for result in executor.map(lambda _: pool.request(ref, {"kind": "transform"}), range(3))
        ]

    assert sorted(results) == [1, 2, 3]
    with pytest.raises(BrokenPipeError, match="worker stdin already closed"):
        pool.close()
    assert [worker.closed for worker in workers] == [True, True, True]


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_close_kills_the_whole_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = tmp_path / "child.pid"
    script = tmp_path / "ignore_term.py"
    script.write_text(
        "import pathlib\n"
        "import signal\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n\n"
        "signal.signal(signal.SIGTERM, lambda *_: None)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "while True:\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )

    def start(self: UvHookWorker) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(script), str(marker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )

    monkeypatch.setattr(UvHookWorker, "_start", start)
    worker = UvHookWorker(tmp_path)
    pgid = os.getpgid(worker._process.pid)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()

        worker.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)


def test_hook_executor_dispatches_builtin_without_worker(tmp_path: Path) -> None:
    class ExplodingWorkers:
        def request(
            self,
            ref: UvHookRef,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            raise AssertionError("worker should not be used for built-ins")

    executor = HookExecutor(
        HookResolver(),
        workers=ExplodingWorkers(),
        helpers=HookHelpers(),
    )

    result = executor.transform(
        "yaml_edit",
        "enabled: false\n",
        local_hook_project=None,
        target=tmp_path,
        file=tmp_path / "config.yml",
        inputs={},
        args={"edits": [{"op": "set", "path": ["enabled"], "value": True}]},
    )

    assert "enabled: true" in result.result
    assert result.diagnostics == ""


def test_hook_executor_sends_external_transform_to_worker(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"suffix": "project_hooks.hooks.suffix"})
    calls: list[dict[str, object]] = []

    class RecordingWorkers:
        def request(
            self,
            ref: UvHookRef,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            calls.append({"ref": ref, "payload": payload})
            return HookWorkerCallResult(result="after\n", diagnostics="discarded\n")

    executor = HookExecutor(
        HookResolver(),
        workers=RecordingWorkers(),
        helpers=HookHelpers(),
    )

    result = executor.transform(
        "suffix",
        "before\n",
        local_hook_project=recipe_dir,
        target=tmp_path / "target",
        file=tmp_path / "target" / "local.yml",
        inputs={"service": "api"},
        args={"flag": True},
    )

    assert result.result == "after\n"
    assert result.diagnostics == ""
    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert payload["kind"] == "transform"
    assert payload["content"] == "before\n"
    assert payload["target"] == str(tmp_path / "target")
    assert payload["file"] == str(tmp_path / "target" / "local.yml")


def test_hook_executor_debug_returns_external_diagnostics(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(recipe_dir, hooks={"suffix": "project_hooks.hooks.suffix"})

    class RecordingWorkers:
        def request(
            self,
            ref: UvHookRef,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            return HookWorkerCallResult(result="after\n", diagnostics="diagnostic\n")

    executor = HookExecutor(
        HookResolver(),
        workers=RecordingWorkers(),
        helpers=HookHelpers(),
    )

    result = executor.transform(
        "suffix",
        "before\n",
        local_hook_project=recipe_dir,
        target=tmp_path / "target",
        file=tmp_path / "target" / "local.yml",
        inputs={},
        args={},
        capture_diagnostics=True,
    )

    assert result.result == "after\n"
    assert result.diagnostics == "diagnostic\n"


def test_worker_script_executes_hooks_and_redirects_prints_to_stderr(tmp_path: Path) -> None:
    package = tmp_path / "src" / "worker_hooks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sample.py").write_text(
        "print('diagnostic from import')\n"
        "def transform(content, *, inputs, target, file, args, helpers):\n"
        "    print('diagnostic from hook')\n"
        "    return helpers.dump_yaml(\n"
        "        {'content': content, 'suffix': inputs['suffix']},\n"
        "        options={'explicit_start': True, 'width': 4096},\n"
        "    )\n"
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

    assert json.loads(proc.stdout.readline()) == {"ready": True}
    response = json.loads(proc.stdout.readline())
    proc.stdin.close()
    stderr = proc.stderr.read()
    proc.wait(timeout=10)

    assert response == {
        "id": "1",
        "ok": True,
        "result": "---\ncontent: 'before '\nsuffix: after\n",
    }
    assert "diagnostic from import" in stderr
    assert "diagnostic from hook" in stderr


def test_worker_script_prefers_cli_sibling_modules_over_hook_env_package(tmp_path: Path) -> None:
    package = tmp_path / "src" / "worker_hooks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "sample.py").write_text(
        "def validate(*, inputs, target, args, helpers):\n"
        "    return helpers.pass_('from real worker protocol')\n"
    )
    fake_engine = tmp_path / "src" / "untaped_recipe"
    fake_engine.mkdir()
    (fake_engine / "__init__.py").write_text("")
    (fake_engine / "worker_protocol.py").write_text(
        "ID = 'bad_id'\n"
        "KIND = 'bad_kind'\n"
        "MODULE = 'bad_module'\n"
        "VALIDATE = 'bad_validate'\n"
        "TRANSFORM = 'bad_transform'\n"
        "INPUTS = 'bad_inputs'\n"
        "TARGET = 'bad_target'\n"
        "ARGS = 'bad_args'\n"
        "CONTENT = 'bad_content'\n"
        "FILE = 'bad_file'\n"
    )
    (fake_engine / "yaml_options.py").write_text(
        "def apply_yaml_dump_options(yaml, options):\n"
        "    raise RuntimeError('fake yaml_options imported')\n"
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
        "kind": "validate",
        "module": "worker_hooks.sample",
        "inputs": {},
        "target": str(tmp_path),
        "args": {},
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()

    assert json.loads(proc.stdout.readline()) == {"ready": True}
    response = json.loads(proc.stdout.readline())
    proc.stdin.close()
    stderr = proc.stderr.read()
    proc.wait(timeout=10)

    assert response == {
        "id": "1",
        "ok": True,
        "result": {"status": "pass", "message": "from real worker protocol"},
    }
    assert "fake yaml_options imported" not in stderr


def test_worker_script_rejects_invalid_validate_return_object(tmp_path: Path) -> None:
    package = tmp_path / "src" / "worker_hooks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "bad_validate.py").write_text(
        "def validate(*, inputs, target, args, helpers):\n    return object()\n"
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
        "kind": "validate",
        "module": "worker_hooks.bad_validate",
        "inputs": {},
        "target": str(tmp_path),
        "args": {},
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()

    assert json.loads(proc.stdout.readline()) == {"ready": True}
    response = json.loads(proc.stdout.readline())
    proc.stdin.close()
    stderr = proc.stderr.read()
    proc.wait(timeout=10)

    assert response["id"] == "1"
    assert response["ok"] is False
    assert "invalid validate verdict" in response["error"]
    assert "invalid validate verdict" in stderr


def test_hook_executor_coerces_external_validate_verdict(tmp_path: Path) -> None:
    recipe_dir = tmp_path / "recipe"
    _write_hook_project(
        recipe_dir,
        hooks={"check": "project_hooks.hooks.check"},
        exports=("validate",),
    )

    class WarningWorkers:
        def request(
            self,
            ref: UvHookRef,
            payload: dict[str, object],
            *,
            diagnostic_limit: int | None = 4000,
            settle_seconds: float = 0,
        ) -> HookWorkerCallResult:
            return HookWorkerCallResult(
                result={"status": "warn", "message": "check this"},
                diagnostics="discarded\n",
            )

    executor = HookExecutor(
        HookResolver(),
        workers=WarningWorkers(),
        helpers=HookHelpers(),
    )

    result = executor.validate(
        "check",
        local_hook_project=recipe_dir,
        target=tmp_path / "target",
        inputs={},
        args={},
    )

    assert result.result == Verdict(status="warn", message="check this")
    assert result.diagnostics == ""


_READY_LINE = '{"ready": true}\n'


class _FakeProcess:
    def __init__(self, *, stdout: str, ready: bool = True) -> None:
        self.stdin = StringIO()
        self.stdout = StringIO((_READY_LINE if ready else "") + stdout)
        self.stderr = StringIO()

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class _BrokenStdin:
    def write(self, line: str) -> int:
        raise BrokenPipeError

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _SlowStdout:
    def __init__(self, delay: float, *, ready: bool = True) -> None:
        self._delay = delay
        self._ready_pending = ready

    def readline(self) -> str:
        if self._ready_pending:
            self._ready_pending = False
            return _READY_LINE
        time.sleep(self._delay)
        return ""


class _DelayedReadyStdout:
    """Ready arrives late (slow env sync), then responses flow instantly."""

    def __init__(self, ready_delay: float, lines: list[str]) -> None:
        self._ready_delay = ready_delay
        self._ready_pending = True
        self._lines = list(lines)

    def readline(self) -> str:
        if self._ready_pending:
            self._ready_pending = False
            time.sleep(self._ready_delay)
            return _READY_LINE
        if self._lines:
            return self._lines.pop(0)
        time.sleep(60)
        return ""


class _DelayedReadyProcess:
    def __init__(self, *, ready_delay: float, lines: list[str]) -> None:
        self.stdin = StringIO()
        self.stdout = _DelayedReadyStdout(ready_delay, lines)
        self.stderr = StringIO()
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self.killed:
            return 0
        raise subprocess.TimeoutExpired("delayed", timeout)

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True


class _SlowProcess:
    def __init__(self, *, delay: float, ready: bool = True) -> None:
        self.stdin = StringIO()
        self.stdout = _SlowStdout(delay, ready=ready)
        self.stderr = StringIO()
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self.killed:
            return 0
        raise subprocess.TimeoutExpired("slow", timeout)

    def terminate(self) -> None:
        self.killed = True

    def kill(self) -> None:
        self.killed = True
