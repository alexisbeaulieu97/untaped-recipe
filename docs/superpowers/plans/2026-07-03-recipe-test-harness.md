# Recipe Test Harness (0.10.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `untaped-recipe test` — golden-fixture, plan-only test cases that live
inside packs — plus the `check` orphaned-tests rule, the `new recipe` test scaffold,
and the deferred check-machinery move to the application layer.

**Architecture:** A test case is a directory (`tests/<recipe>/<case>/` with `given/`,
optional `expected/`, optional `case.yml`) compared by full-tree content. The harness
reuses the exact production planner (`RunBulkApply` → `ApplyRecipe`) against a temp
copy of `given/`; verdict assertions are observed through a `RecordingHookExecutor`
decorator around `HookExecutorPort`, so no planner or domain model changes. Two
mechanical extractions land first: recipe-ref resolution and the `check` machinery
move from `cli/commands.py` (1091-line composition root) into the application layer,
because the harness and the orphan rule build on them.

**Tech Stack:** Python 3.14, pydantic v2, cyclopts via `untaped.api` (SDK 3.x),
pyyaml, pytest + `untaped.testing.CliInvoker`.

**Spec:** `docs/superpowers/specs/2026-07-02-recipe-test-harness-design.md`
(as amended 2026-07-03 — `targets:` dropped, `given/` is the single target
directory; read the spec's §Amendments before starting).

## Global Constraints

- Dependency pin stays `untaped>=3.0.0,<4`; `requires-python = ">=3.14"`. No new
  runtime dependencies.
- Version bump to `0.10.0` in root `pyproject.toml` AND `src/untaped_recipe/_version.py`
  (Task 12 only). `HOOK_API_VERSION` stays `0.9.0` — the helper contract is unchanged.
- **Do not touch `.github/workflows/release.yml`** or anything under `.github/`.
- Behavior freeze on moved code: Tasks 1–2 must not change any error string, row
  shape, emit kind, or CLI contract. The proof is that the existing test suite
  passes **unmodified** after each of those tasks.
- Four-layer layout: `cli/` command signatures, `application/` use cases and ports,
  `domain/` pure models, `infrastructure/` adapters. Absolute imports only. SDK
  imports only from `untaped.api` (tests may use `untaped.testing`).
- stdout is data only; diffs, summaries, and progress go to stderr.
- New emit kind: `recipe.test` with row fields `pack`, `recipe`, `case`, `status`,
  `detail` — exactly these, in this order.
- Work on branch `test-harness` cut from `origin/main`. Commit after every task.
- Run tests with `uv run pytest` (add `-q` for brevity). If color-stripping
  assertions fail in a shell that exports `FORCE_COLOR`, rerun that file with
  `env -u FORCE_COLOR uv run pytest ...`.
- From the `untaped-dev` symlinked workspace use `uv --cache-dir .uv-cache run ...`.

## File Structure

| File | Responsibility |
|---|---|
| `src/untaped_recipe/application/resolution.py` (new) | `ResolvedRecipe`, explicit-path predicate, apply-recipe ref resolution (moved from `cli/commands.py`) |
| `src/untaped_recipe/application/check_pack.py` (new) | `check_library` / `check_ref` use case (moved from `cli/commands.py`) + orphaned-tests rule |
| `src/untaped_recipe/domain/testcase.py` (new) | `CaseSpec` / `VerdictExpectation` pure models |
| `src/untaped_recipe/application/harness.py` (new) | case discovery, `RecordingHookExecutor`, `run_case`, `update_case`, tree snapshot/compare |
| `src/untaped_recipe/cli/test_commands.py` (new) | `test` command: grammar, rows, stderr diffs/summary, exit codes, `--update` |
| `src/untaped_recipe/cli/commands.py` | shrinks (moves out); registers `test`; `check` gets a thin body |
| `src/untaped_recipe/infrastructure/pack_scaffold.py` | `scaffold_recipe` additionally scaffolds `tests/<recipe>/basic/` |
| `tests/test_testcase_spec.py`, `tests/test_harness.py`, `tests/test_cli_test_command.py` (new) | per-layer tests |
| `tests/test_cli_unified.py`, `tests/test_pack_scaffold.py`, `tests/test_hook_api_contract.py` | targeted additions/updates |
| `AGENTS.md`, `README.md`, `docs/packs.md`, `src/untaped_recipe/skills/untaped-recipe/SKILL.md` | docs kept current (Hard Rule 1) |

---

### Task 0: Branch

- [ ] **Step 1: Cut the branch**

```bash
cd /Users/alexisbeaulieu/Projects/untaped-recipe
git fetch origin
git checkout -b test-harness origin/main
uv sync
```

---

### Task 1: Move recipe-ref resolution to `application/resolution.py`

Pure mechanical move. `cli/commands.py` currently owns `ResolvedRecipe`,
`_resolve_apply_recipe`, `_resolve_explicit_recipe`, and `_is_explicit_recipe_path`;
the check machinery (Task 2) and the `test` command (Task 8) also need them, and
they are use-case logic, not CLI signatures.

**Files:**
- Create: `src/untaped_recipe/application/resolution.py`
- Modify: `src/untaped_recipe/cli/commands.py`

**Interfaces:**
- Produces: `ResolvedRecipe` (frozen dataclass: `path: Path`, `ref: str`,
  `local_hook_project: Path | None`); `is_explicit_recipe_path(value: str) -> bool`;
  `resolve_apply_recipe(root: Path, ref_text: str, *, recipe_id: str | None) -> ResolvedRecipe`;
  `resolve_explicit_recipe(path: Path, *, recipe_id: str | None) -> ResolvedRecipe`.
- Consumes: `PackLibrary`, `PackManifest`, `parse_ref` (existing).

- [ ] **Step 1: Create the module** — bodies are copied verbatim from
  `cli/commands.py` (only the leading underscores drop and the
  `UnifiedPackLibrary` alias becomes `PackLibrary`):

```python
"""Resolve recipe references to concrete recipe files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.infrastructure.pack_store import PackLibrary


@dataclass(frozen=True)
class ResolvedRecipe:
    """A recipe file resolved from an explicit path or installed pack."""

    path: Path
    ref: str
    local_hook_project: Path | None


def is_explicit_recipe_path(value: str) -> bool:
    """Classify a ref as an explicit filesystem path (never a library ref)."""
    return value.startswith(("/", "./", "../", "~")) or value.endswith((".yml", ".yaml"))


def resolve_apply_recipe(root: Path, ref_text: str, *, recipe_id: str | None) -> ResolvedRecipe:
    """Resolve an apply ref: explicit path, pack path + --recipe, or library ref."""
    if recipe_id is not None:
        if not is_explicit_recipe_path(ref_text):
            raise ValueError("--recipe requires an explicit pack path")
        return resolve_explicit_recipe(Path(ref_text).expanduser(), recipe_id=recipe_id)
    if is_explicit_recipe_path(ref_text):
        return resolve_explicit_recipe(Path(ref_text).expanduser(), recipe_id=None)
    ref = parse_ref(ref_text)
    pack, recipe = PackLibrary(library_root=root).find_recipe(ref)
    return ResolvedRecipe(
        path=pack.root / recipe.path,
        ref=f"{pack.name}/{ref.name}",
        local_hook_project=pack.root,
    )


def resolve_explicit_recipe(path: Path, *, recipe_id: str | None) -> ResolvedRecipe:
    """Resolve an explicit path to a recipe file, pack recipe, or bare recipe.yml."""
    if path.is_dir():
        if recipe_id is not None:
            manifest = PackManifest.from_pyproject(path)
            entry = manifest.recipes.get(recipe_id)
            if entry is None:
                raise ValueError(f"recipe not found: {recipe_id}")
            return ResolvedRecipe(
                path=path / entry.path,
                ref=f"{path.name}/{recipe_id}",
                local_hook_project=path,
            )
        recipe_path = path / "recipe.yml"
        if not recipe_path.is_file():
            raise ValueError(f"recipe file not found: {recipe_path}")
        return ResolvedRecipe(
            path=recipe_path,
            ref=path.name,
            local_hook_project=path if (path / "pyproject.toml").is_file() else None,
        )
    return ResolvedRecipe(
        path=path,
        ref=path.name,
        local_hook_project=None,
    )
```

- [ ] **Step 2: Update `cli/commands.py`**
  - Delete the `ResolvedRecipe` dataclass and the functions
    `_resolve_apply_recipe`, `_resolve_explicit_recipe`, `_is_explicit_recipe_path`.
  - Add the import:

```python
from untaped_recipe.application.resolution import (
    is_explicit_recipe_path,
    resolve_apply_recipe,
    resolve_explicit_recipe,
)
```

  - In `_apply_context`, change
    `recipe_resolution = _resolve_apply_recipe(root, recipe, recipe_id=recipe_id)`
    to `recipe_resolution = resolve_apply_recipe(root, recipe, recipe_id=recipe_id)`.
  - In `_check_ref` (still in commands.py until Task 2), rename the two call sites:
    `_is_explicit_recipe_path(ref_text)` → `is_explicit_recipe_path(ref_text)` and
    `_resolve_explicit_recipe(path, recipe_id=None)` →
    `resolve_explicit_recipe(path, recipe_id=None)`.
  - Do NOT touch `_is_explicit_new_path` — the `new` grammar is deliberately
    different (no `.yml` suffix rule) and stays in the CLI.

- [ ] **Step 3: Verify unchanged behavior**

Run: `uv run pytest -q`
Expected: full suite passes with **zero test modifications** (332+ tests).

Run: `uv run mypy && uv run ruff check`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add src/untaped_recipe/application/resolution.py src/untaped_recipe/cli/commands.py
git commit -m "refactor: move recipe-ref resolution to application/resolution"
```

---

### Task 2: Move `check` machinery to `application/check_pack.py`

The deferred 0.9 cleanup item: ~160 lines of check logic live at CLI altitude.
Mechanical move; the CLI keeps only the command signature.

**Files:**
- Create: `src/untaped_recipe/application/check_pack.py`
- Modify: `src/untaped_recipe/cli/commands.py`

**Interfaces:**
- Produces: `check_library(root: Path) -> list[dict[str, object]]`;
  `check_ref(root: Path, ref_text: str) -> dict[str, object]`. Row dicts keep the
  exact 0.9 shapes: pack rows `{pack,status,path,recipes,hooks,error}`, recipe rows
  `{recipe,status,path,error}`.
- Consumes: Task 1's `is_explicit_recipe_path` / `resolve_explicit_recipe`.

- [ ] **Step 1: Create the module** — bodies copied verbatim from `cli/commands.py`
  except: public entry points drop the underscore, and the internal
  `_load_recipe(...)` call becomes a direct `load_recipe_file(...)` (its
  `ValueError` is already caught by `_check_recipe`'s handler, and the row error
  string is `str(exc)` either way — byte-identical rows):

```python
"""Validate packs, recipes, and the installed library (check use case)."""

from __future__ import annotations

from pathlib import Path

from untaped.api import ConfigError

from untaped_recipe.application.inputs import validate_recipe_input_sources
from untaped_recipe.application.resolution import (
    is_explicit_recipe_path,
    resolve_explicit_recipe,
)
from untaped_recipe.domain.hook_project import (
    read_hook_metadata,
    validate_hook_modules,
    validate_hook_project_contract,
)
from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.domain.paths import confined_path
from untaped_recipe.domain.recipe import (
    CopyStep,
    Recipe,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_recipe.infrastructure import HookResolver
from untaped_recipe.infrastructure.hook_resolver import ensure_hook_supports
from untaped_recipe.infrastructure.pack_store import InstalledPack, PackLibrary
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file


def check_ref(root: Path, ref_text: str) -> dict[str, object]:
    """Check one installed pack, recipe ref, or explicit path."""
    library = PackLibrary(library_root=root)
    if is_explicit_recipe_path(ref_text):
        path = Path(ref_text).expanduser()
        if path.is_dir() and (path / "pyproject.toml").is_file():
            try:
                manifest = PackManifest.from_pyproject(path)
            except (ValueError, OSError) as exc:
                return _pack_check_row(path.name, path, status="error", error=str(exc))
            return _check_pack(root, InstalledPack.local(path, manifest))
        resolved = resolve_explicit_recipe(path, recipe_id=None)
        return _check_recipe(root, resolved.path, resolved.ref, resolved.local_hook_project)
    pack = library.find_pack(ref_text)
    if pack is not None:
        return _check_pack(root, pack)
    ref = parse_ref(ref_text)
    pack, recipe = library.find_recipe(ref)
    return _check_recipe(root, pack.root / recipe.path, f"{pack.name}/{ref.name}", pack.root)


def check_library(root: Path) -> list[dict[str, object]]:
    """Check every installed pack plus index/directory reconciliation."""
    library = PackLibrary(library_root=root)
    rows = [_check_reconcile_problem(root, problem) for problem in library.reconcile()]
    rows.extend(_check_pack(root, pack) for pack in library.packs())
    return rows


def _check_reconcile_problem(root: Path, problem: str) -> dict[str, object]:
    name = _quoted_name(problem)
    return _pack_check_row(
        name,
        root / "packs" / name if name else None,
        status="error",
        error=problem,
    )


def _quoted_name(message: str) -> str:
    parts = message.split("'", maxsplit=2)
    return parts[1] if len(parts) == 3 else ""


def _pack_check_row(
    name: str,
    path: Path | None,
    *,
    status: str,
    recipes: int = 0,
    hooks: int = 0,
    error: str = "",
) -> dict[str, object]:
    return {
        "pack": name,
        "status": status,
        "path": str(path) if path is not None else "",
        "recipes": recipes,
        "hooks": hooks,
        "error": error,
    }


def _check_pack(root: Path, pack: InstalledPack) -> dict[str, object]:
    try:
        if not (pack.root / "uv.lock").is_file():
            raise ValueError(f"pack project is missing uv.lock: {pack.root}")
        validate_hook_project_contract(pack.root, pack.manifest)
        validate_hook_modules(pack.root, pack.manifest)
        for recipe_name, recipe in sorted(pack.manifest.recipes.items()):
            row = _check_recipe(
                root, pack.root / recipe.path, f"{pack.name}/{recipe_name}", pack.root
            )
            if row["status"] == "error":
                raise ValueError(f"{recipe_name}: {row['error']}")
    except (ConfigError, ValueError, OSError) as exc:
        return _pack_check_row(
            pack.name,
            pack.root,
            status="error",
            recipes=len(pack.manifest.recipes),
            hooks=len(pack.manifest.hooks),
            error=str(exc),
        )
    return _pack_check_row(
        pack.name,
        pack.root,
        status="pass",
        recipes=len(pack.manifest.recipes),
        hooks=len(pack.manifest.hooks),
    )


def _check_recipe(
    root: Path,
    recipe_path: Path,
    recipe_ref: str,
    local_hook_project: Path | None,
) -> dict[str, object]:
    try:
        recipe = load_recipe_file(recipe_path)
        validate_recipe_input_sources(recipe)
        _check_project_lock(local_hook_project)
        _check_assets(recipe, recipe_path.parent)
        _check_local_hook_project(local_hook_project)
        _check_hooks(recipe, root, local_hook_project)
    except (ConfigError, ValueError, OSError) as exc:
        return {
            "recipe": recipe_ref,
            "status": "error",
            "path": str(recipe_path),
            "error": str(exc),
        }
    return {
        "recipe": recipe_ref,
        "status": "pass",
        "path": str(recipe_path),
        "error": "",
    }


def _check_project_lock(local_hook_project: Path | None) -> None:
    if local_hook_project is not None and not (local_hook_project / "uv.lock").is_file():
        raise ValueError(f"recipe project is missing uv.lock: {local_hook_project}")


def _check_assets(recipe: Recipe, recipe_dir: Path) -> None:
    for step in recipe.steps:
        if isinstance(step, TemplateStep):
            source = confined_path(recipe_dir, step.template, field="template")
            if not source.is_file():
                raise ValueError(f"template not found: {step.template}")
        elif isinstance(step, CopyStep):
            source = confined_path(recipe_dir, step.source, field="source")
            if not source.is_file():
                raise ValueError(f"copy source not found: {step.source}")


def _check_local_hook_project(local_hook_project: Path | None) -> None:
    if local_hook_project is None or not (local_hook_project / "pyproject.toml").is_file():
        return
    metadata = read_hook_metadata(local_hook_project)
    if not metadata.hooks:
        return
    validate_hook_project_contract(local_hook_project, metadata)
    if not (local_hook_project / "uv.lock").is_file():
        raise ValueError(f"hook project is missing uv.lock: {local_hook_project}")
    validate_hook_modules(local_hook_project, metadata)


def _check_hooks(recipe: Recipe, root: Path, local_hook_project: Path | None) -> None:
    resolver = HookResolver(library_root=root)
    for step in recipe.steps:
        if isinstance(step, TransformStep):
            ref = resolver.resolve(step.hook, local_hook_project)
            ensure_hook_supports(ref, step.hook, verb="transform")
        elif isinstance(step, ValidateStep):
            ref = resolver.resolve(step.hook, local_hook_project)
            ensure_hook_supports(ref, step.hook, verb="validate")
```

(The `_check_pack` recipe loop inlines `sorted(pack.manifest.recipes.items())`
instead of calling commands.py's `_recipes` helper — identical iteration order.)

- [ ] **Step 2: Thin the CLI**
  - In `cli/commands.py`, delete: `_check_ref`, `_check_library`,
    `_check_reconcile_problem`, `_quoted_name`, `_pack_check_row`, `_check_pack`,
    `_check_recipe`, `_check_project_lock`, `_check_assets`,
    `_check_local_hook_project`, `_check_hooks`.
  - `check_command`'s body becomes:

```python
    with report_config_errors():
        root = library_root()
        rows = check_library(root) if ref_text is None else [check_ref(root, ref_text)]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.check")
        if rendered:
            echo(rendered)
        finish(any(row["status"] == "error" for row in rows))
```

  - Add `from untaped_recipe.application.check_pack import check_library, check_ref`.
  - Prune now-unused commands.py imports: `validate_recipe_input_sources` (keep
    `PromptFunc`), `confined_path` (keep `safe_library_name`),
    `validate_hook_modules` / `validate_hook_project_contract` /
    `read_hook_metadata` (keep `hook_module_file`), `CopyStep` / `TemplateStep` /
    `TransformStep` / `ValidateStep` (keep `Recipe`), `ensure_hook_supports`, and
    the Task-1 imports `is_explicit_recipe_path` / `resolve_explicit_recipe`
    (only `resolve_apply_recipe` remains in use). `ruff check` will confirm.

- [ ] **Step 3: Verify unchanged behavior**

Run: `uv run pytest -q && uv run mypy && uv run ruff check`
Expected: full suite passes with zero test modifications; lints clean.

- [ ] **Step 4: Commit**

```bash
git add src/untaped_recipe/application/check_pack.py src/untaped_recipe/cli/commands.py
git commit -m "refactor: move check machinery to application/check_pack"
```

---

### Task 3: `domain/testcase.py` — case models

**Files:**
- Create: `src/untaped_recipe/domain/testcase.py`
- Test: `tests/test_testcase_spec.py`

**Interfaces:**
- Produces: `CaseSpec` (frozen, `extra="forbid"`: `inputs: dict[str, object]`,
  `expect: Literal["success","error"]`, `error_contains: str | None`,
  `verdict: VerdictExpectation | None`); `VerdictExpectation` (frozen,
  `extra="forbid"`: `status: Literal["pass","warn","fail"] | None`,
  `message_contains: str | None`, at least one required).

- [ ] **Step 1: Write the failing tests** — `tests/test_testcase_spec.py`:

```python
"""Tests for golden-case spec models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped_recipe.domain.testcase import CaseSpec, VerdictExpectation


def test_case_spec_defaults_to_success_with_no_inputs() -> None:
    spec = CaseSpec()

    assert spec.expect == "success"
    assert spec.inputs == {}
    assert spec.error_contains is None
    assert spec.verdict is None


def test_expect_error_requires_error_contains() -> None:
    with pytest.raises(ValidationError, match="expect: error requires error_contains"):
        CaseSpec(expect="error")


def test_error_contains_is_forbidden_on_success_cases() -> None:
    with pytest.raises(ValidationError, match="error_contains is only valid"):
        CaseSpec(error_contains="boom")


def test_verdict_expectation_requires_an_assertion() -> None:
    with pytest.raises(ValidationError, match="status or message_contains"):
        VerdictExpectation()


def test_unknown_case_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseSpec.model_validate({"targets": ["src/playbook.yml"]})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_testcase_spec.py -q`
Expected: FAIL with `ModuleNotFoundError: untaped_recipe.domain.testcase`.

- [ ] **Step 3: Write the module**

```python
"""Pure models for golden-fixture recipe test cases."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VerdictStatus = Literal["pass", "warn", "fail"]


class VerdictExpectation(BaseModel):
    """Expected validate-verdict outcome for one case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: VerdictStatus | None = None
    message_contains: str | None = None

    @model_validator(mode="after")
    def _require_assertion(self) -> VerdictExpectation:
        if self.status is None and self.message_contains is None:
            raise ValueError("verdict must declare status or message_contains")
        return self


class CaseSpec(BaseModel):
    """Parsed case.yml contents; every field is optional in the file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inputs: dict[str, object] = Field(default_factory=dict)
    expect: Literal["success", "error"] = "success"
    error_contains: str | None = None
    verdict: VerdictExpectation | None = None

    @model_validator(mode="after")
    def _validate_error_contract(self) -> CaseSpec:
        if self.expect == "error" and not self.error_contains:
            raise ValueError("expect: error requires error_contains")
        if self.expect == "success" and self.error_contains is not None:
            raise ValueError("error_contains is only valid with expect: error")
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_testcase_spec.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/untaped_recipe/domain/testcase.py tests/test_testcase_spec.py
git commit -m "feat: case spec models for the golden test harness"
```

---

### Task 4: Harness discovery + case.yml loading

**Files:**
- Create: `src/untaped_recipe/application/harness.py` (first slice)
- Test: `tests/test_harness.py` (first slice)

**Interfaces:**
- Produces: `DiscoveredCase` (frozen dataclass: `pack_name: str`, `pack_root: Path`,
  `recipe_name: str`, `recipe_path: Path`, `case_name: str`, `case_dir: Path`);
  `discover_cases(pack: InstalledPack, *, recipe: str | None = None) -> list[DiscoveredCase]`;
  `orphaned_test_dirs(pack: InstalledPack) -> list[str]`;
  `load_case_spec(case_dir: Path) -> CaseSpec` (raises `ValueError` on bad YAML).
- Consumes: `InstalledPack` (Task-independent, exists), `CaseSpec` (Task 3).

- [ ] **Step 1: Write the failing tests** — `tests/test_harness.py`:

```python
"""Tests for the golden-fixture harness (discovery, execution, update)."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.application.harness import (
    discover_cases,
    load_case_spec,
    orphaned_test_dirs,
)
from untaped_recipe.domain.pack import PackManifest
from untaped_recipe.infrastructure.pack_store import InstalledPack


def _write_pack(
    root: Path,
    *,
    recipes: dict[str, str],
    recipe_bodies: dict[str, str] | None = None,
) -> InstalledPack:
    """Write a minimal pack project and wrap it as a local InstalledPack."""
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, relative in recipes.items():
        recipe_path = root / relative
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        body = (recipe_bodies or {}).get(name, "version: 1\nsteps: []\n")
        recipe_path.write_text(body, encoding="utf-8")
        rows.append(f'"{name}" = {{ path = "{relative}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "untaped-recipe-demo"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return InstalledPack.local(root, PackManifest.from_pyproject(root))


def _write_case(pack_root: Path, recipe: str, case: str, *, case_yml: str | None = None) -> Path:
    case_dir = pack_root / "tests" / recipe / case
    (case_dir / "given").mkdir(parents=True)
    if case_yml is not None:
        (case_dir / "case.yml").write_text(case_yml, encoding="utf-8")
    return case_dir


def test_discover_cases_lists_cases_per_manifest_recipe_sorted(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"})
    _write_case(pack.root, "two", "beta")
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "one", "gamma")

    cases = discover_cases(pack)

    assert [(case.recipe_name, case.case_name) for case in cases] == [
        ("one", "alpha"),
        ("one", "gamma"),
        ("two", "beta"),
    ]
    assert cases[0].pack_name == "demo"
    assert cases[0].recipe_path == pack.root / "recipes/one.yml"
    assert cases[0].case_dir == pack.root / "tests" / "one" / "alpha"


def test_discover_cases_scopes_to_one_recipe_and_ignores_files_and_hidden_dirs(
    tmp_path: Path,
) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml", "two": "recipes/two.yml"})
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "two", "beta")
    (pack.root / "tests" / "one" / "README.md").write_text("notes\n", encoding="utf-8")
    (pack.root / "tests" / "one" / ".hidden").mkdir()

    cases = discover_cases(pack, recipe="one")

    assert [(case.recipe_name, case.case_name) for case in cases] == [("one", "alpha")]


def test_orphaned_test_dirs_flags_dirs_naming_no_recipe(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml"})
    _write_case(pack.root, "one", "alpha")
    _write_case(pack.root, "renamed", "old")
    (pack.root / "tests" / ".cache").mkdir()

    assert orphaned_test_dirs(pack) == ["renamed"]


def test_orphaned_test_dirs_is_empty_without_tests_dir(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"one": "recipes/one.yml"})

    assert orphaned_test_dirs(pack) == []


def test_load_case_spec_defaults_when_file_missing(tmp_path: Path) -> None:
    assert load_case_spec(tmp_path).expect == "success"


def test_load_case_spec_rejects_non_mapping_and_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "case.yml").write_text("- not\n- a-mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="case.yml must contain a YAML mapping"):
        load_case_spec(tmp_path)

    (tmp_path / "case.yml").write_text("expect: [unclosed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid case.yml"):
        load_case_spec(tmp_path)


def test_load_case_spec_rejects_unknown_fields(tmp_path: Path) -> None:
    (tmp_path / "case.yml").write_text("targets: [a.yml]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid case.yml"):
        load_case_spec(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_harness.py -q`
Expected: FAIL with `ModuleNotFoundError: untaped_recipe.application.harness`.

- [ ] **Step 3: Write the first harness slice**

```python
"""Golden-fixture test harness for recipe packs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from untaped_recipe.domain.testcase import CaseSpec
from untaped_recipe.infrastructure.pack_store import InstalledPack


@dataclass(frozen=True)
class DiscoveredCase:
    """One golden case directory resolved against a pack manifest."""

    pack_name: str
    pack_root: Path
    recipe_name: str
    recipe_path: Path
    case_name: str
    case_dir: Path


def discover_cases(pack: InstalledPack, *, recipe: str | None = None) -> list[DiscoveredCase]:
    """List golden cases for one pack, optionally scoped to one recipe."""
    tests_dir = pack.root / "tests"
    names = [recipe] if recipe is not None else sorted(pack.manifest.recipes)
    cases: list[DiscoveredCase] = []
    for name in names:
        entry = pack.manifest.recipes.get(name)
        if entry is None:
            continue
        recipe_tests = tests_dir / name
        if not recipe_tests.is_dir():
            continue
        for case_dir in sorted(recipe_tests.iterdir(), key=lambda path: path.name):
            if not case_dir.is_dir() or case_dir.name.startswith("."):
                continue
            cases.append(
                DiscoveredCase(
                    pack_name=pack.name,
                    pack_root=pack.root,
                    recipe_name=name,
                    recipe_path=pack.root / entry.path,
                    case_name=case_dir.name,
                    case_dir=case_dir,
                )
            )
    return cases


def orphaned_test_dirs(pack: InstalledPack) -> list[str]:
    """Return tests/ subdirectories that name no recipe in the manifest."""
    tests_dir = pack.root / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(
        entry.name
        for entry in tests_dir.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name not in pack.manifest.recipes
    )


def load_case_spec(case_dir: Path) -> CaseSpec:
    """Parse an optional case.yml; absent file means all defaults."""
    case_file = case_dir / "case.yml"
    if not case_file.is_file():
        return CaseSpec()
    try:
        loaded = yaml.safe_load(case_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid case.yml: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("case.yml must contain a YAML mapping")
    try:
        return CaseSpec.model_validate(loaded)
    except ValidationError as exc:
        raise ValueError(f"invalid case.yml: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_harness.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/untaped_recipe/application/harness.py tests/test_harness.py
git commit -m "feat: harness case discovery and case.yml loading"
```

---

### Task 5: `check` gains the orphaned-tests rule

**Files:**
- Modify: `src/untaped_recipe/application/check_pack.py`
- Test: `tests/test_cli_unified.py` (additions only)

**Interfaces:**
- Consumes: `orphaned_test_dirs` (Task 4).
- Error string (locked): `tests directory names no known recipe: <a>, <b>`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli_unified.py`
  (reuse its existing `_write_pack` / `_install_pack` helpers):

```python
def test_check_flags_orphaned_tests_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    (source / "tests" / "playbook" / "basic" / "given").mkdir(parents=True)
    (source / "tests" / "renamed" / "old" / "given").mkdir(parents=True)
    _install_pack(source)

    result = CliInvoker().invoke(app, ["check", "ansible", "--format", "json"])

    assert result.exit_code == 1, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "error"
    assert row["error"] == "tests directory names no known recipe: renamed"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli_unified.py::test_check_flags_orphaned_tests_directories -q`
Expected: FAIL — exit code 0, status "pass" (rule not implemented).

- [ ] **Step 3: Implement** — in `application/check_pack.py`, add the import
  `from untaped_recipe.application.harness import orphaned_test_dirs` and extend
  `_check_pack`'s `try` block, immediately after the recipe loop:

```python
        orphans = orphaned_test_dirs(pack)
        if orphans:
            raise ValueError(
                "tests directory names no known recipe: " + ", ".join(orphans)
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_unified.py -q`
Expected: all pass (new test plus the existing check tests, which have no tests/
directories and are unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/untaped_recipe/application/check_pack.py tests/test_cli_unified.py
git commit -m "feat: check flags orphaned tests directories"
```

---

### Task 6: Harness execution — `RecordingHookExecutor`, trees, `run_case`

The core of the wave. Planning runs the real `RunBulkApply` → `ApplyRecipe` against
a temp copy of `given/` named after the case; the result tree is materialized in
memory (fixtures are never written); verdicts are observed via a decorator around
the executor port (a fail verdict aborts planning by raising, so verdicts must be
captured at the port, not read off the plan).

**Files:**
- Modify: `src/untaped_recipe/application/harness.py`
- Test: `tests/test_harness.py` (additions)

**Interfaces:**
- Produces:
  - `CaseStatus = Literal["pass", "fail", "error", "updated"]` (`"updated"` is
    produced only by Task 7).
  - `CaseResult` (frozen dataclass): `pack: str`, `recipe: str`, `case: str`,
    `status: CaseStatus`, `detail: str = ""`, `diffs: tuple[FileChange, ...] = ()`.
  - `RecordingHookExecutor(inner: HookExecutorPort)` — implements
    `HookExecutorPort`; `.verdicts: list[Verdict]` accumulates every validate
    verdict.
  - `run_case(case: DiscoveredCase, *, executor: HookExecutorPort) -> CaseResult`.
- Consumes: `RunBulkApply`, `ApplyRecipe`, `Target`, `FileChange`, `Verdict`,
  `HookDebugResult`, `HookExecutorPort`, `load_recipe_file` (all existing).
- Locked detail strings (tests assert them):
  - error: `case is missing given/`
  - error: `expected/ is forbidden for expect: error cases`
  - fail: `expected planning to fail; it succeeded`
  - fail: `planning failed with different message: <error>`
  - fail: `files differ: <a>, <b>` (≤5 names, sorted, `, …` suffix beyond 5)
  - fail: `expected no changes; planned changes to: <a>, <b>` (same capping)
  - fail: `no verdicts produced`
  - fail: `expected worst verdict status <want>, got <got>`
  - fail: `no verdict message contains '<needle>'`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_harness.py`:

```python
from untaped_recipe.application.harness import (  # add to existing import
    CaseResult,
    DiscoveredCase,
    RecordingHookExecutor,
    run_case,
)
from untaped_recipe.application.ports import HookDebugResult
from untaped_recipe.domain.plan import Verdict


class _FakeExecutor:
    """In-process HookExecutorPort: uppercases content, replays queued verdicts."""

    def __init__(self, verdicts: tuple[Verdict, ...] = ()) -> None:
        self._verdicts = list(verdicts)

    def transform(
        self,
        hook,
        content,
        *,
        local_hook_project,
        target,
        file,
        inputs,
        args,
        capture_diagnostics=False,
    ):
        return HookDebugResult(result=content.upper(), diagnostics="")

    def validate(
        self,
        hook,
        *,
        local_hook_project,
        target,
        inputs,
        args,
        capture_diagnostics=False,
    ):
        verdict = self._verdicts.pop(0) if self._verdicts else Verdict(status="pass")
        return HookDebugResult(result=verdict, diagnostics="")


_COPY_RECIPE = (
    "version: 1\n"
    "steps:\n"
    "  - type: copy\n"
    "    source: assets/payload.txt\n"
    "    dest: out.txt\n"
)

_TRANSFORM_RECIPE = (
    "version: 1\n"
    "steps:\n"
    "  - type: transform\n"
    "    file: note.txt\n"
    "    hook: shout\n"
)

_VALIDATE_RECIPE = "version: 1\nsteps:\n  - type: validate\n    hook: probe\n"


def _copy_pack(tmp_path: Path) -> InstalledPack:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"emit": "recipes/emit/recipe.yml"},
        recipe_bodies={"emit": _COPY_RECIPE},
    )
    asset = pack.root / "recipes" / "emit" / "assets" / "payload.txt"
    asset.parent.mkdir(parents=True)
    asset.write_text("payload\n", encoding="utf-8")
    return pack


def _case(pack: InstalledPack, recipe: str, case: str) -> DiscoveredCase:
    return next(
        found for found in discover_cases(pack, recipe=recipe) if found.case_name == case
    )


def test_run_case_passes_when_result_tree_matches_expected(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "given" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (case_dir / "expected" / "out.txt").write_text("payload\n", encoding="utf-8")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result == CaseResult(pack="demo", recipe="emit", case="basic", status="pass")


def test_run_case_fails_on_full_tree_mismatch_with_diffs(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "out.txt").write_text("different\n", encoding="utf-8")
    (case_dir / "expected" / "extra.txt").write_text("only-expected\n", encoding="utf-8")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "files differ: extra.txt, out.txt"
    assert {change.relative_path.as_posix() for change in result.diffs} == {
        "extra.txt",
        "out.txt",
    }
    extra = next(c for c in result.diffs if c.relative_path.as_posix() == "extra.txt")
    assert extra.before == "only-expected\n"
    assert extra.after is None


def test_run_case_omitted_expected_asserts_no_changes(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(pack.root, "emit", "basic")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "expected no changes; planned changes to: out.txt"


def test_run_case_expect_error_matches_message(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"shout": "recipes/shout.yml"},
        recipe_bodies={"shout": _TRANSFORM_RECIPE},
    )
    _write_case(
        pack.root,
        "shout",
        "missing-file",
        case_yml='expect: error\nerror_contains: "transform file not found"\n',
    )

    result = run_case(_case(pack, "shout", "missing-file"), executor=_FakeExecutor())

    assert result.status == "pass"


def test_run_case_expect_error_fails_on_unexpected_success(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(
        pack.root,
        "emit",
        "basic",
        case_yml='expect: error\nerror_contains: "boom"\n',
    )

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "expected planning to fail; it succeeded"


def test_run_case_expected_dir_forbidden_for_error_cases(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(
        pack.root,
        "emit",
        "basic",
        case_yml='expect: error\nerror_contains: "boom"\n',
    )
    (case_dir / "expected").mkdir()

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "error"
    assert result.detail == "expected/ is forbidden for expect: error cases"


def test_run_case_verdict_worst_of_and_message(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"lint": "recipes/lint.yml"},
        recipe_bodies={"lint": _VALIDATE_RECIPE},
    )
    _write_case(
        pack.root,
        "lint",
        "warns",
        case_yml="verdict:\n  status: warn\n  message_contains: tabs\n",
    )
    executor = _FakeExecutor(verdicts=(Verdict(status="warn", message="uses tabs"),))

    result = run_case(_case(pack, "lint", "warns"), executor=executor)

    assert result.status == "pass"


def test_run_case_verdict_mismatch_fails(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / "demo",
        recipes={"lint": "recipes/lint.yml"},
        recipe_bodies={"lint": _VALIDATE_RECIPE},
    )
    _write_case(pack.root, "lint", "warns", case_yml="verdict:\n  status: warn\n")

    result = run_case(_case(pack, "lint", "warns"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "expected worst verdict status warn, got pass"


def test_run_case_verdict_with_no_verdicts_fails(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(pack.root, "emit", "basic", case_yml="verdict:\n  status: pass\n")

    result = run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "fail"
    assert result.detail == "no verdicts produced"


def test_run_case_missing_given_is_an_error(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    (pack.root / "tests" / "emit" / "broken").mkdir(parents=True)

    result = run_case(_case(pack, "emit", "broken"), executor=_FakeExecutor())

    assert result.status == "error"
    assert result.detail == "case is missing given/"


def test_run_case_never_mutates_fixtures(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "given" / "keep.txt").write_text("keep\n", encoding="utf-8")

    run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert sorted(p.name for p in (case_dir / "given").iterdir()) == ["keep.txt"]


def test_recording_executor_records_validate_verdicts_only(tmp_path: Path) -> None:
    recorder = RecordingHookExecutor(_FakeExecutor(verdicts=(Verdict(status="warn"),)))

    recorder.transform(
        "shout",
        "hi",
        local_hook_project=None,
        target=tmp_path,
        file=tmp_path / "f",
        inputs={},
        args={},
    )
    recorder.validate("probe", local_hook_project=None, target=tmp_path, inputs={}, args={})

    assert [verdict.status for verdict in recorder.verdicts] == ["warn"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_harness.py -q`
Expected: FAIL with `ImportError` on `CaseResult` / `run_case`.

- [ ] **Step 3: Implement** — append to `application/harness.py` (new imports at top:
  `shutil`, `tempfile`, `Iterable` from `collections.abc`, `Literal` from `typing`,
  and the project imports shown):

```python
import shutil
import tempfile
from collections.abc import Iterable
from typing import Literal

from untaped_recipe.application.apply_recipe import ApplyRecipe
from untaped_recipe.application.ports import HookDebugResult, HookExecutorPort
from untaped_recipe.application.run_bulk import RunBulkApply
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.plan import FileChange, Verdict
from untaped_recipe.domain.testcase import VerdictExpectation
from untaped_recipe.infrastructure.recipe_loader import load_recipe_file

CaseStatus = Literal["pass", "fail", "error", "updated"]

_VERDICT_RANK = {"pass": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True)
class CaseResult:
    """Outcome of running (or updating) one golden case."""

    pack: str
    recipe: str
    case: str
    status: CaseStatus
    detail: str = ""
    diffs: tuple[FileChange, ...] = ()


class RecordingHookExecutor:
    """HookExecutorPort decorator that records every validate verdict."""

    def __init__(self, inner: HookExecutorPort) -> None:
        self._inner = inner
        self.verdicts: list[Verdict] = []

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        return self._inner.transform(
            hook,
            content,
            local_hook_project=local_hook_project,
            target=target,
            file=file,
            inputs=inputs,
            args=args,
            capture_diagnostics=capture_diagnostics,
        )

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        execution = self._inner.validate(
            hook,
            local_hook_project=local_hook_project,
            target=target,
            inputs=inputs,
            args=args,
            capture_diagnostics=capture_diagnostics,
        )
        self.verdicts.append(execution.result)
        return execution


@dataclass(frozen=True)
class _Trees:
    """Fixture tree before planning and materialized tree after."""

    base: dict[str, str]
    result: dict[str, str]


def run_case(case: DiscoveredCase, *, executor: HookExecutorPort) -> CaseResult:
    """Run one golden case; fixtures and the pack are never written."""
    given = case.case_dir / "given"
    if not given.is_dir():
        return _result(case, "error", "case is missing given/")
    try:
        spec = load_case_spec(case.case_dir)
    except ValueError as exc:
        return _result(case, "error", str(exc))
    expected_dir = case.case_dir / "expected"
    if spec.expect == "error" and expected_dir.exists():
        return _result(case, "error", "expected/ is forbidden for expect: error cases")

    recorder = RecordingHookExecutor(executor)
    trees, error = _plan_case(case, spec, given, recorder)
    verdict_problem = (
        _verdict_problem(spec.verdict, recorder.verdicts) if spec.verdict is not None else ""
    )

    if spec.expect == "error":
        needle = spec.error_contains or ""
        if error is None:
            return _result(case, "fail", "expected planning to fail; it succeeded")
        if needle not in error:
            return _result(case, "fail", f"planning failed with different message: {error}")
        if verdict_problem:
            return _result(case, "fail", verdict_problem)
        return _result(case, "pass")

    if error is not None:
        return _result(case, "error", error)
    assert trees is not None
    if expected_dir.is_dir():
        mismatches = _tree_mismatches(_read_tree(expected_dir), trees.result, case=case)
        if mismatches:
            return _result(
                case, "fail", _mismatch_detail("files differ", mismatches), diffs=mismatches
            )
    else:
        mismatches = _tree_mismatches(trees.base, trees.result, case=case)
        if mismatches:
            return _result(
                case,
                "fail",
                _mismatch_detail("expected no changes; planned changes to", mismatches),
                diffs=mismatches,
            )
    if verdict_problem:
        return _result(case, "fail", verdict_problem)
    return _result(case, "pass")


def _plan_case(
    case: DiscoveredCase,
    spec: CaseSpec,
    given: Path,
    recorder: RecordingHookExecutor,
) -> tuple[_Trees | None, str | None]:
    """Plan against a temp copy of given/ and return (trees, error)."""
    try:
        recipe = load_recipe_file(case.recipe_path)
    except ValueError as exc:
        return None, str(exc)
    with tempfile.TemporaryDirectory() as temp_root:
        target_dir = Path(temp_root) / case.case_name
        shutil.copytree(given, target_dir)
        base = _read_tree(target_dir)
        runner = RunBulkApply(ApplyRecipe(recorder))
        try:
            plans = runner.plan(
                recipe=recipe,
                recipe_dir=case.recipe_path.parent,
                local_hook_project=case.pack_root,
                targets=[Target(path=target_dir)],
                inputs=dict(spec.inputs),
            )
        except ValueError as exc:
            return None, str(exc)
        plan = plans[0]
        if plan.status == "error":
            return None, plan.error
        return _Trees(base=base, result=_materialize(base, plan.changes)), None


def _read_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8", newline="")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _materialize(base: dict[str, str], changes: Iterable[FileChange]) -> dict[str, str]:
    tree = dict(base)
    for change in changes:
        key = change.relative_path.as_posix()
        if change.after is None:
            tree.pop(key, None)
        else:
            tree[key] = change.after
    return tree


def _tree_mismatches(
    expected: dict[str, str],
    actual: dict[str, str],
    *,
    case: DiscoveredCase,
) -> tuple[FileChange, ...]:
    changes: list[FileChange] = []
    for key in sorted(expected.keys() | actual.keys()):
        before = expected.get(key)
        after = actual.get(key)
        if before != after:
            changes.append(
                FileChange(
                    target=case.case_dir,
                    relative_path=Path(key),
                    before=before,
                    after=after,
                )
            )
    return tuple(changes)


def _mismatch_detail(prefix: str, mismatches: tuple[FileChange, ...]) -> str:
    names = [change.relative_path.as_posix() for change in mismatches]
    listed = ", ".join(names[:5]) + (", …" if len(names) > 5 else "")
    return f"{prefix}: {listed}"


def _verdict_problem(expectation: VerdictExpectation, verdicts: list[Verdict]) -> str:
    if not verdicts:
        return "no verdicts produced"
    if expectation.status is not None:
        worst = max(verdicts, key=lambda verdict: _VERDICT_RANK[verdict.status]).status
        if worst != expectation.status:
            return f"expected worst verdict status {expectation.status}, got {worst}"
    if expectation.message_contains is not None and not any(
        expectation.message_contains in verdict.message for verdict in verdicts
    ):
        return f"no verdict message contains {expectation.message_contains!r}"
    return ""


def _result(
    case: DiscoveredCase,
    status: CaseStatus,
    detail: str = "",
    *,
    diffs: tuple[FileChange, ...] = (),
) -> CaseResult:
    return CaseResult(
        pack=case.pack_name,
        recipe=case.recipe_name,
        case=case.case_name,
        status=status,
        detail=detail,
        diffs=diffs,
    )
```

Notes for the implementer:
- The temp target dir is named after the case so `from:` expressions over
  `target.name` are deterministic (spec amendment 1).
- A validate hook returning a fail verdict makes `ApplyRecipe` raise, which
  `RunBulkApply` converts to an error `TargetPlan` — that surfaces here through the
  `error` channel, while the fail verdict itself is already in `recorder.verdicts`.
  This is why the recorder exists; do not add verdict fields to `TargetPlan`.
- `_plan_case` treats a recipe-load failure as a planning error so `expect: error`
  cases can assert on it, mirroring how `apply` reports it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_harness.py -q && uv run mypy`
Expected: all pass; mypy clean (the `_FakeExecutor` satisfies `HookExecutorPort`
structurally).

- [ ] **Step 5: Commit**

```bash
git add src/untaped_recipe/application/harness.py tests/test_harness.py
git commit -m "feat: golden case execution with verdict recording"
```

---

### Task 7: `update_case` — golden regeneration

**Files:**
- Modify: `src/untaped_recipe/application/harness.py`
- Test: `tests/test_harness.py` (additions)

**Interfaces:**
- Produces: `update_case(case: DiscoveredCase, *, executor: HookExecutorPort) -> CaseResult`
  with `status` ∈ {`updated`, `pass`, `error`}: `updated` when `expected/` was
  rewritten or deleted, `pass` when the golden already matched (nothing written),
  `error` on planning failure, bad case.yml, missing `given/`, or an
  `expect: error` case.
- Locked detail string: `cannot --update an expect: error case`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_harness.py`
  (add `update_case` to the harness import):

```python
def test_update_case_writes_expected_tree_from_plan(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    (case_dir / "given" / "keep.txt").write_text("keep\n", encoding="utf-8")

    result = update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "updated"
    assert (case_dir / "expected" / "out.txt").read_text(encoding="utf-8") == "payload\n"
    assert (case_dir / "expected" / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert run_case(_case(pack, "emit", "basic"), executor=_FakeExecutor()).status == "pass"


def test_update_case_reports_pass_when_golden_already_matches(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    case_dir = _write_case(pack.root, "emit", "basic")
    update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())
    mtime = (case_dir / "expected" / "out.txt").stat().st_mtime_ns

    result = update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "pass"
    assert (case_dir / "expected" / "out.txt").stat().st_mtime_ns == mtime


def test_update_case_deletes_expected_when_plan_is_empty(tmp_path: Path) -> None:
    pack = _write_pack(tmp_path / "demo", recipes={"noop": "recipes/noop.yml"})
    case_dir = _write_case(pack.root, "noop", "basic")
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "stale.txt").write_text("stale\n", encoding="utf-8")

    result = update_case(_case(pack, "noop", "basic"), executor=_FakeExecutor())

    assert result.status == "updated"
    assert not (case_dir / "expected").exists()


def test_update_case_rejects_error_cases(tmp_path: Path) -> None:
    pack = _copy_pack(tmp_path)
    _write_case(
        pack.root,
        "emit",
        "basic",
        case_yml='expect: error\nerror_contains: "boom"\n',
    )

    result = update_case(_case(pack, "emit", "basic"), executor=_FakeExecutor())

    assert result.status == "error"
    assert result.detail == "cannot --update an expect: error case"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_harness.py -q`
Expected: FAIL with `ImportError: update_case`.

- [ ] **Step 3: Implement** — append to `application/harness.py`:

```python
def update_case(case: DiscoveredCase, *, executor: HookExecutorPort) -> CaseResult:
    """Regenerate expected/ from the current plan; report what changed."""
    given = case.case_dir / "given"
    if not given.is_dir():
        return _result(case, "error", "case is missing given/")
    try:
        spec = load_case_spec(case.case_dir)
    except ValueError as exc:
        return _result(case, "error", str(exc))
    if spec.expect == "error":
        return _result(case, "error", "cannot --update an expect: error case")

    trees, error = _plan_case(case, spec, given, RecordingHookExecutor(executor))
    if error is not None:
        return _result(case, "error", error)
    assert trees is not None
    expected_dir = case.case_dir / "expected"
    if trees.result == trees.base:
        if expected_dir.is_dir():
            shutil.rmtree(expected_dir)
            return _result(case, "updated")
        return _result(case, "pass")
    if expected_dir.is_dir():
        if _read_tree(expected_dir) == trees.result:
            return _result(case, "pass")
        shutil.rmtree(expected_dir)
    for key, content in trees.result.items():
        path = expected_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
    return _result(case, "updated")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_harness.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/untaped_recipe/application/harness.py tests/test_harness.py
git commit -m "feat: update_case golden regeneration"
```

---

### Task 8: CLI `test` command (run mode)

**Files:**
- Create: `src/untaped_recipe/cli/test_commands.py`
- Modify: `src/untaped_recipe/cli/commands.py` (registration only)
- Test: `tests/test_cli_test_command.py`

**Interfaces:**
- Grammar mirrors `check`: `test` (whole library) | `test <path>` (pack dir) |
  `test <pack>` | `test <pack>/<recipe>` (bare unique recipe refs also resolve,
  same as `check`). `test <x>.yml` is a usage error.
- Consumes: `discover_cases`, `orphaned_test_dirs`, `run_case`, `CaseResult`,
  `is_explicit_recipe_path`, `hook_timeout_seconds`, `unified_diff`,
  `HookExecutor` / `HookResolver` / `HookHelpers` / `UvHookWorkerPool`.
- Locked strings:
  - ConfigError: `test requires a pack directory or ref, not a recipe file`
  - synthetic row detail: `no test cases found`
  - orphan row detail: `tests directory names no known recipe`
  - summary: `Recipe tests: {passed} passed, {failed} failed, {errors} errored`
  - packs-without-tests stderr info: `packs without tests: a, b` (bare form only;
    a pack counts when it has no `tests/` directory)

- [ ] **Step 1: Write the failing tests** — `tests/test_cli_test_command.py`.
  CLI tests use hook-free step types (copy/template/remove) so no uv workers spawn;
  verdict paths are covered at the harness layer (Task 6):

```python
"""Tests for the test command (golden-case runner)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from untaped.testing import CliInvoker

from untaped_recipe import app
from untaped_recipe.cli.common import library_root
from untaped_recipe.infrastructure.pack_store import PackLibrary

pytestmark = pytest.mark.usefixtures("isolate_config")

_COPY_RECIPE = (
    "version: 1\n"
    "steps:\n"
    "  - type: copy\n"
    "    source: assets/payload.txt\n"
    "    dest: out.txt\n"
)


def _write_pack(root: Path, *, manifest_name: str, recipe_body: str = _COPY_RECIPE) -> None:
    recipe_path = root / "recipes" / "emit" / "recipe.yml"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(recipe_body, encoding="utf-8")
    asset = root / "recipes" / "emit" / "assets" / "payload.txt"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text("payload\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{manifest_name}"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.9,<1"\n\n'
        "[tool.untaped_recipe.recipes]\n"
        '"emit" = { path = "recipes/emit/recipe.yml" }\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")


def _write_passing_case(root: Path) -> Path:
    case_dir = root / "tests" / "emit" / "basic"
    (case_dir / "given").mkdir(parents=True)
    (case_dir / "expected").mkdir()
    (case_dir / "expected" / "out.txt").write_text("payload\n", encoding="utf-8")
    return case_dir


def _install(source: Path) -> None:
    PackLibrary(library_root=library_root()).add(
        source, source=str(source), rev=None, name=None, force=False
    )


def test_test_pack_runs_cases_and_exits_zero_on_pass(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == [
        {"pack": "demo", "recipe": "emit", "case": "basic", "status": "pass", "detail": ""}
    ]
    assert "Recipe tests: 1 passed, 0 failed, 0 errored" in result.stderr


def test_test_failure_renders_diff_on_stderr_and_exits_one(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    case_dir = _write_passing_case(source)
    (case_dir / "expected" / "out.txt").write_text("different\n", encoding="utf-8")
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])

    assert result.exit_code == 1
    row = json.loads(result.stdout)[0]
    assert row["status"] == "fail"
    assert row["detail"] == "files differ: out.txt"
    assert "# demo/emit/basic" in result.stderr
    assert "-different" in result.stderr
    assert "+payload" in result.stderr


def test_test_recipe_scope_and_no_cases_failure(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo/emit", "--format", "json"])

    assert result.exit_code == 1
    row = json.loads(result.stdout)[0]
    assert row == {
        "pack": "demo",
        "recipe": "emit",
        "case": "",
        "status": "error",
        "detail": "no test cases found",
    }


def test_bare_test_reports_packs_without_tests_but_passes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _install(source)

    result = CliInvoker().invoke(app, ["test", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == ""
    assert "packs without tests: demo" in result.stderr


def test_test_reports_orphaned_tests_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)
    (source / "tests" / "renamed" / "old" / "given").mkdir(parents=True)
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])

    assert result.exit_code == 1
    rows = json.loads(result.stdout)
    orphan = next(row for row in rows if row["recipe"] == "renamed")
    assert orphan["status"] == "error"
    assert orphan["detail"] == "tests directory names no known recipe"


def test_test_explicit_path_runs_local_pack(tmp_path: Path) -> None:
    source = tmp_path / "local-pack"
    _write_pack(source, manifest_name="demo")
    _write_passing_case(source)

    result = CliInvoker().invoke(app, ["test", str(source), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "pass"


def test_test_rejects_recipe_file_paths(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["test", "./recipe.yml"])

    assert result.exit_code != 0
    assert "test requires a pack directory or ref, not a recipe file" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_test_command.py -q`
Expected: FAIL — cyclopts reports no `test` command.

- [ ] **Step 3: Write `cli/test_commands.py`**

```python
"""CLI test command: run golden-fixture cases from installed or local packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter
from untaped.api import (
    ColumnsOption,
    ConfigError,
    FormatOption,
    echo,
    finish,
    render_rows,
    ui_context,
)

from untaped_recipe.application.harness import (
    CaseResult,
    DiscoveredCase,
    discover_cases,
    orphaned_test_dirs,
    run_case,
    update_case,
)
from untaped_recipe.application.resolution import is_explicit_recipe_path
from untaped_recipe.cli.common import hook_timeout_seconds, library_root, report_config_errors
from untaped_recipe.domain.pack import PackManifest, parse_ref
from untaped_recipe.infrastructure import HookExecutor, HookResolver
from untaped_recipe.infrastructure.diff import unified_diff
from untaped_recipe.infrastructure.hook_helpers import HookHelpers
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.infrastructure.pack_store import InstalledPack, PackLibrary


@dataclass(frozen=True)
class _Selection:
    """Cases to run plus non-case rows the selection already produced."""

    cases: list[DiscoveredCase] = field(default_factory=list)
    static_results: list[CaseResult] = field(default_factory=list)
    packs_without_tests: list[str] = field(default_factory=list)


def test_command(
    ref_text: Annotated[
        str | None,
        Parameter(help="Installed pack, recipe ref, or pack path."),
    ] = None,
    /,
    *,
    update: Annotated[
        bool,
        Parameter(
            name="--update",
            negative="",
            help="Regenerate expected/ trees from the current plan.",
        ),
    ] = False,
    fmt: FormatOption = "table",
    columns: ColumnsOption = None,
) -> None:
    """Run golden-fixture test cases for packs."""
    with report_config_errors():
        if update and ref_text is None:
            raise ConfigError("--update requires an explicit pack or recipe argument")
        root = library_root()
        selection = _select(root, ref_text)
        results = _execute(root, selection, update=update)
        rows = [_row(result) for result in results]
        rendered = render_rows(rows, fmt=fmt, columns=columns, kind="recipe.test")
        if rendered:
            echo(rendered)
        if not update:
            _render_diffs(results)
        _render_summary(selection, results, update=update)
        if update:
            finish(any(result.status == "error" for result in results))
        else:
            finish(any(result.status in {"fail", "error"} for result in results))


def _select(root: Path, ref_text: str | None) -> _Selection:
    library = PackLibrary(library_root=root)
    if ref_text is None:
        selection = _Selection()
        for pack in library.packs():
            if not (pack.root / "tests").is_dir():
                selection.packs_without_tests.append(pack.name)
                continue
            _extend_for_pack(selection, pack)
        return selection
    if is_explicit_recipe_path(ref_text):
        if ref_text.endswith((".yml", ".yaml")):
            raise ConfigError("test requires a pack directory or ref, not a recipe file")
        path = Path(ref_text).expanduser()
        pack = InstalledPack.local(path, PackManifest.from_pyproject(path))
        return _explicit_selection(pack, recipe=None)
    pack_match = library.find_pack(ref_text)
    if pack_match is not None:
        return _explicit_selection(pack_match, recipe=None)
    ref = parse_ref(ref_text)
    recipe_pack, _entry = library.find_recipe(ref)
    return _explicit_selection(recipe_pack, recipe=ref.name)


def _explicit_selection(pack: InstalledPack, *, recipe: str | None) -> _Selection:
    selection = _Selection()
    if recipe is None:
        _extend_for_pack(selection, pack)
    else:
        selection.cases.extend(discover_cases(pack, recipe=recipe))
    if not selection.cases and not selection.static_results:
        selection.static_results.append(
            CaseResult(
                pack=pack.name,
                recipe=recipe or "",
                case="",
                status="error",
                detail="no test cases found",
            )
        )
    return selection


def _extend_for_pack(selection: _Selection, pack: InstalledPack) -> None:
    selection.cases.extend(discover_cases(pack))
    selection.static_results.extend(
        CaseResult(
            pack=pack.name,
            recipe=name,
            case="",
            status="error",
            detail="tests directory names no known recipe",
        )
        for name in orphaned_test_dirs(pack)
    )


def _execute(root: Path, selection: _Selection, *, update: bool) -> list[CaseResult]:
    results = list(selection.static_results)
    if not selection.cases:
        return results
    runner = update_case if update else run_case
    ui = ui_context(strict=False)
    with UvHookWorkerPool(
        max_workers_per_project=1,
        hook_timeout_seconds=hook_timeout_seconds(None),
    ) as workers:
        executor = HookExecutor(
            HookResolver(library_root=root),
            workers=workers,
            helpers=HookHelpers(),
        )
        with ui.progress("Running test cases") as progress:
            total = len(selection.cases)
            for index, case in enumerate(selection.cases, start=1):
                results.append(runner(case, executor=executor))
                progress.update(f"{index}/{total}", fraction=index / total)
    return results


def _row(result: CaseResult) -> dict[str, object]:
    return {
        "pack": result.pack,
        "recipe": result.recipe,
        "case": result.case,
        "status": result.status,
        "detail": result.detail,
    }


def _render_diffs(results: list[CaseResult]) -> None:
    for result in results:
        if not result.diffs:
            continue
        echo(f"# {result.pack}/{result.recipe}/{result.case}", err=True)
        for change in result.diffs:
            diff = unified_diff(change)
            if diff:
                echo(diff, err=True, nl=False)


def _render_summary(selection: _Selection, results: list[CaseResult], *, update: bool) -> None:
    ui = ui_context(strict=False)
    errors = sum(1 for result in results if result.status == "error")
    if update:
        updated = sum(1 for result in results if result.status == "updated")
        unchanged = sum(1 for result in results if result.status == "pass")
        kind = "warning" if errors else "info"
        ui.message(
            kind,
            f"Recipe test update: {updated} updated, {unchanged} unchanged, {errors} errored",
        )
        return
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    kind = "warning" if failed or errors else "info"
    ui.message(kind, f"Recipe tests: {passed} passed, {failed} failed, {errors} errored")
    if selection.packs_without_tests:
        ui.message(
            "info",
            "packs without tests: " + ", ".join(selection.packs_without_tests),
        )
```

- [ ] **Step 4: Register the command** — in `cli/commands.py`, next to the existing
  sub-app registrations:

```python
from untaped_recipe.cli.test_commands import test_command
```

```python
app.command(test_command, name="test")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_test_command.py -q && uv run pytest -q`
Expected: new file passes; full suite still green.

Note: the diff comparison in a `given/`-empty + expected-mismatch case exercises
`unified_diff` with `before`/`after` = expected/actual — patch-style `a/`, `b/`
headers land on stderr, matching `apply --preview diff` conventions.

- [ ] **Step 6: Commit**

```bash
git add src/untaped_recipe/cli/test_commands.py src/untaped_recipe/cli/commands.py tests/test_cli_test_command.py
git commit -m "feat: test command runs golden pack cases"
```

---

### Task 9: CLI `--update` mode

The flag and plumbing already exist from Task 8; this task locks their behavior
with tests.

**Files:**
- Test: `tests/test_cli_test_command.py` (additions)

- [ ] **Step 1: Write the failing tests** (they may partially pass — treat as
  behavior locks; fix `test_commands.py` if any fails):

```python
def test_update_requires_an_explicit_argument(tmp_path: Path) -> None:
    result = CliInvoker().invoke(app, ["test", "--update"])

    assert result.exit_code != 0
    assert "--update requires an explicit pack or recipe argument" in result.output


def test_update_writes_goldens_into_installed_pack(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    (source / "tests" / "emit" / "basic" / "given").mkdir(parents=True)
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--update", "--format", "json"])

    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)[0]
    assert row["status"] == "updated"
    golden = library_root() / "packs" / "demo" / "tests" / "emit" / "basic" / "expected"
    assert (golden / "out.txt").read_text(encoding="utf-8") == "payload\n"
    assert "Recipe test update: 1 updated, 0 unchanged, 0 errored" in result.stderr

    rerun = CliInvoker().invoke(app, ["test", "demo", "--format", "json"])
    assert rerun.exit_code == 0, rerun.output
    assert json.loads(rerun.stdout)[0]["status"] == "pass"


def test_update_rejects_expect_error_cases(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="demo")
    case_dir = source / "tests" / "emit" / "basic"
    (case_dir / "given").mkdir(parents=True)
    (case_dir / "case.yml").write_text(
        'expect: error\nerror_contains: "boom"\n', encoding="utf-8"
    )
    _install(source)

    result = CliInvoker().invoke(app, ["test", "demo", "--update", "--format", "json"])

    assert result.exit_code == 1
    row = json.loads(result.stdout)[0]
    assert row["status"] == "error"
    assert row["detail"] == "cannot --update an expect: error case"
```

- [ ] **Step 2: Run and fix until green**

Run: `uv run pytest tests/test_cli_test_command.py -q`
Expected: all pass (Task 8's implementation already routes `--update` through
`update_case`; these tests pin exit codes, the summary line, and golden placement).

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_test_command.py
git commit -m "test: lock test --update CLI behavior"
```

---

### Task 10: `new recipe` scaffolds a starter case

**Files:**
- Modify: `src/untaped_recipe/infrastructure/pack_scaffold.py`
- Test: `tests/test_pack_scaffold.py` (additions)

**Interfaces:**
- `scaffold_recipe(pack_dir, name)` additionally creates
  `tests/<recipe>/basic/given/` (empty) and `tests/<recipe>/basic/case.yml`
  (fully commented, parses as all-defaults). Rollback on lock failure removes
  whatever tests path this call created. A pre-existing `tests/<recipe>/basic/`
  is an error: `recipe tests already exist: <name>`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_pack_scaffold.py`:

```python
def test_scaffold_recipe_creates_starter_test_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")

    case_dir = tmp_path / "ansible" / "tests" / "playbook" / "basic"
    assert (case_dir / "given").is_dir()
    case_yml = (case_dir / "case.yml").read_text(encoding="utf-8")
    assert case_yml.startswith("#")
    from untaped_recipe.application.harness import load_case_spec

    assert load_case_spec(case_dir).expect == "success"


def test_scaffold_recipe_rolls_back_test_case_on_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pack_scaffold, "lock_project", lambda project_root: None)
    pack_scaffold.scaffold_pack(tmp_path / "ansible", "ansible")

    def _boom(project_root: Path) -> None:
        raise ValueError("lock failed")

    monkeypatch.setattr(pack_scaffold, "lock_project", _boom)
    with pytest.raises(ValueError, match="lock failed"):
        pack_scaffold.scaffold_recipe(tmp_path / "ansible", "playbook")

    assert not (tmp_path / "ansible" / "tests").exists()
    assert not (tmp_path / "ansible" / "recipes" / "playbook").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pack_scaffold.py -q`
Expected: the two new tests FAIL (no tests/ directory is scaffolded).

- [ ] **Step 3: Implement** — in `pack_scaffold.py`:

Add the template constant:

```python
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
#   status: warn              # expected worst status: pass | warn | fail
#   message_contains: "..."   # substring of at least one verdict message
"""
```

Rewrite `scaffold_recipe` (existing body plus the tests scaffold and rollback):

```python
def scaffold_recipe(pack_dir: Path, name: str) -> Path:
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
    _append_recipe_row(pack_dir / "pyproject.toml", recipe_name, recipe_path.relative_to(pack_dir))
    try:
        lock_project(pack_dir)
    except Exception:
        _remove_manifest_row(pack_dir / "pyproject.toml", "recipes", recipe_name)
        shutil.rmtree(recipe_path.parent, ignore_errors=True)
        shutil.rmtree(created_tests_path, ignore_errors=True)
        raise
    return recipe_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pack_scaffold.py -q && uv run pytest -q`
Expected: scaffold tests pass; full suite green (the scaffolded case is inert:
empty `given/`, no `expected/`, empty plan → passes as "no changes").

- [ ] **Step 5: Commit**

```bash
git add src/untaped_recipe/infrastructure/pack_scaffold.py tests/test_pack_scaffold.py
git commit -m "feat: new recipe scaffolds a starter golden case"
```

---

### Task 11: Docs and packaged skill

Hard Rule 1: AGENTS.md, README, docs, and SKILL.md move with behavior, same change.

**Files:**
- Modify: `AGENTS.md`, `README.md`, `docs/packs.md`,
  `src/untaped_recipe/skills/untaped-recipe/SKILL.md`

- [ ] **Step 1: AGENTS.md**
  - In §Architecture's module list, note the new application modules:
    `application/` line gains "check + golden-test harness use cases"; add
    `cli/test_commands.py` alongside the other CLI modules if the tree is shown.
  - Add a new section after "Recipe Schema":

```markdown
## Testing Packs

`test [pack|path|pack/recipe]` mirrors `check`'s grammar and runs golden-fixture
cases stored inside packs at `tests/<recipe>/<case>/`:

- `given/` is the single fixture target directory; the plan runs against a temp
  copy named after the case. Fixtures and packs are never written by a test run.
- `expected/` is the full expected tree after the plan (extra, missing, and
  changed files all fail); omitting it asserts the plan makes no changes.
- `case.yml` is optional data-only config: `inputs`, `expect: success|error`,
  `error_contains` (required with `expect: error`), and `verdict`
  (`status`: expected worst-of across produced verdicts; `message_contains`).
  No assertion language beyond this exists or will exist; logic in tests is
  pytest's job at the hook level.
- Planning is the only execution: the harness runs the same planner as `apply`
  with the normal hook resolution order. `--update` regenerates `expected/`
  (deleting it when the plan is empty), requires an explicit pack or recipe
  argument, and rejects `expect: error` cases.
- One `recipe.test` record per case (`pack`, `recipe`, `case`, `status`,
  `detail`) on stdout; unified diffs per mismatched file and a summary line on
  stderr. Exit 1 on any fail/error, including "no test cases found" for an
  explicitly named pack or recipe; the bare `test` reports packs without tests
  but does not fail on them.
- `check` fails a pack whose `tests/` contains a directory naming no manifest
  recipe; `test` also reports such directories as error rows.
- `new recipe` scaffolds `tests/<recipe>/basic/` with an empty `given/` and a
  fully commented `case.yml`.
```

- [ ] **Step 2: README.md** — add `test` to the command overview (one line:
  "`test` — run golden-fixture cases packs ship under `tests/`; `--update`
  regenerates goldens"), matching the style of the surrounding entries.

- [ ] **Step 3: docs/packs.md** — add a "Testing packs" section documenting the
  case layout, `case.yml` fields, `--update`, and the orphaned-tests `check` rule,
  with one worked example (case dir listing + `test` invocation + sample row
  output). Reuse the wording from the AGENTS.md section; expand the example.

- [ ] **Step 4: SKILL.md** — add the `test` verb to the command list and a short
  "testing packs" workflow snippet (scaffold → fill `given/` → `test <pack> --update`
  → review the generated `expected/` → commit). Mention `recipe.test` rows.

- [ ] **Step 5: Verify docs formatting**

Run: `uv run pre-commit run --all-files --show-diff-on-failure`
Expected: clean (or auto-fixed; re-stage and re-run).

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md README.md docs/packs.md src/untaped_recipe/skills/untaped-recipe/SKILL.md
git commit -m "docs: test harness docs, skill, and AGENTS section"
```

---

### Task 12: Version 0.10.0 + full gate

**Files:**
- Modify: `pyproject.toml`, `src/untaped_recipe/_version.py`,
  `tests/test_hook_api_contract.py`, `tests/test_pack_scaffold.py`,
  `src/untaped_recipe/skills/untaped-recipe/SKILL.md`, `uv.lock`

- [ ] **Step 1: Bump versions**
  - `pyproject.toml`: `version = "0.10.0"`.
  - `src/untaped_recipe/_version.py`: `PACKAGE_VERSION = "0.10.0"`.
  - `HOOK_API_VERSION` stays `"0.9.0"` (helper contract unchanged), so the
    scaffold's `requires_hook_api = ">=0.9,<1"` floor is unchanged — but the
    derived dev dependency becomes `untaped-recipe>=0.10`:
    - `tests/test_hook_api_contract.py`: expected `dev_requirement` becomes
      `"untaped-recipe>=0.10"` (the `project_requirement` assertion stays
      `">=0.9,<1"`).
    - `tests/test_pack_scaffold.py`: the verbatim pyproject assertion's dev line
      becomes `'dev = ["untaped-recipe>=0.10"]\n'`.
    - `SKILL.md`: the sentence naming the auto-added dev dependency updates from
      `untaped-recipe>=0.9` to `untaped-recipe>=0.10`.

- [ ] **Step 2: Full gate**

```bash
uv lock
uv run pre-commit run --all-files --show-diff-on-failure
uv run mypy
uv run pytest
uv build --no-sources
uv run python scripts/release.py verify-versions 0.10.0
```

Expected: all green; pytest count grows by roughly 30 tests over 0.9.0's 332.

- [ ] **Step 3: End-to-end smoke** (manual, from a temp dir, using the project
  venv so `UNTAPED_CONFIG` is honored):

```bash
cd "$(mktemp -d)"
export UNTAPED_CONFIG="$PWD/config.yml"
export UNTAPED_RECIPE__LIBRARY_ROOT="$PWD/library"
uv run --project /Users/alexisbeaulieu/Projects/untaped-recipe untaped-recipe new pack demo
uv run --project /Users/alexisbeaulieu/Projects/untaped-recipe untaped-recipe new recipe ./demo/hello
uv run --project /Users/alexisbeaulieu/Projects/untaped-recipe untaped-recipe test ./demo
uv run --project /Users/alexisbeaulieu/Projects/untaped-recipe untaped-recipe add ./demo --yes
uv run --project /Users/alexisbeaulieu/Projects/untaped-recipe untaped-recipe test demo
uv run --project /Users/alexisbeaulieu/Projects/untaped-recipe untaped-recipe check demo
```

Expected: the scaffolded case passes locally and after install; `check` passes;
exit codes 0 throughout (the `demo-inner` line is noise-tolerant).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/untaped_recipe/_version.py uv.lock tests/test_hook_api_contract.py tests/test_pack_scaffold.py src/untaped_recipe/skills/untaped-recipe/SKILL.md
git commit -m "chore: version 0.10.0"
```

- [ ] **Step 5: Open the draft PR** — push `test-harness`, open a draft PR titled
  "Recipe 0.10.0: golden test harness" against `main`. Release (PyPI 0.10.0 + gh
  release) follows the standard runbook after merge; the human operator dispatches.

---

## Deferred / out of scope (do not do these)

- Pack-validation single-surface consolidation (0.9 deferred item b) — separate
  follow-up; do not fold into this wave.
- `packs()` defaulting-vs-reconcile drift question (item c) — needs a behavior
  decision first.
- Parallel case execution, watch mode, apply-through testing, `expected.diff`
  goldens — spec non-goals.
- `ensure` step semantics and follow-up declarations — next minor, separate spec
  section already locked.
