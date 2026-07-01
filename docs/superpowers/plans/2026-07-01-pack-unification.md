# Pack Unification (0.9.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse recipe projects, packs, and hook projects into a single "pack" concept with one library, one flattened CLI, git-URL sharing, and a function-name hook contract, per `docs/superpowers/specs/2026-07-01-pack-unification-design.md`.

**Architecture:** A pack is a directory whose `pyproject.toml` declares `[tool.untaped_recipe]` with explicit `recipes` and `hooks` tables. One `PackLibrary` (at `<library_root>/packs/<name>/` plus a `packs.toml` source index) replaces the three existing libraries. Hook `kind` disappears from manifests; the exported function name (`transform`/`validate`) is the contract, checked without importing via an AST scan. The worker protocol, helpers, and `--locked --no-dev` execution are unchanged.

**Tech Stack:** Python ≥3.14, uv, Pydantic v2, Cyclopts via `untaped.api.create_app`, pytest.

## Global Constraints

- Execution starts ONLY after PR #17 merges and untaped-recipe 0.8.0 is published to PyPI. Branch from fresh `origin/main`.
- `PACKAGE_VERSION = "0.9.0"` in `src/untaped_recipe/_version.py`; `HOOK_API_VERSION = "0.9.0"` in `src/untaped_recipe/hook_api.py`; root `pyproject.toml` `version = "0.9.0"`.
- Scaffold floors derive from those constants: dev dep `untaped-recipe>=0.9`, `requires_hook_api = ">=0.9,<1"` (upper bound is new and mandatory).
- Pack name = `[project].name` with `untaped-recipe-` prefix stripped. Qualified refs use `pack/name`. Ambiguous bare names are errors listing qualified candidates — never first-match.
- No compat shims, no migration command (sole user). No auto-discovery of recipes — manifest tables are the identity.
- Repo conventions (AGENTS.md): four-layer imports `cli → application → domain`, `infrastructure` implements `application/ports.py`; absolute imports only; SDK imports only from `untaped.api`; run tests with `uv run pytest -p no:cacheprovider`; FORCE_COLOR gotcha handled by conftest.
- Full gate before any release: `uv lock`, `uv run pre-commit run --all-files --show-diff-on-failure`, `uv run mypy`, `uv run pytest`, `uv build --no-sources`, `uv run python scripts/release.py verify-versions 0.9.0`.

---

### Task 0: Preflight — verify post-merge reality

**Files:**
- Read: `src/untaped_recipe/domain/hook_project.py`, `infrastructure/hook_resolver.py`, `infrastructure/hook_library.py`, `infrastructure/recipe_library.py`, `infrastructure/pack_library.py`, `infrastructure/hook_executor.py`, `cli/commands.py`, `cli/hook_commands.py`, `scripts/release.py`

- [ ] **Step 1:** Confirm PR #17 is merged and `untaped-recipe==0.8.0` resolves from PyPI (`uv run --no-project --with untaped-recipe==0.8.0 python -c "import untaped_recipe.hook_api as a; print(a.HOOK_API_VERSION)"` prints `0.8.0`). If not, STOP — this plan is premature.
- [ ] **Step 2:** `git fetch origin && git checkout -b pack-unification origin/main`.
- [ ] **Step 3:** Read every file listed above end to end. This plan cites symbols as of 2026-07-01; PR #17's follow-up may have moved lines. If a cited symbol is gone or renamed, adapt the task to the current symbol and note the delta in the commit message.
- [ ] **Step 4:** Run `uv run pytest -p no:cacheprovider` — must be green before any change.

### Task 1: AST-based hook export discovery

**Files:**
- Create: `src/untaped_recipe/domain/hook_exports.py`
- Test: `tests/test_hook_exports.py`

**Interfaces:**
- Produces: `hook_exports_from_source(source: str) -> frozenset[str]` and `hook_exports(module_file: Path) -> frozenset[str]`, each returning a subset of `{"transform", "validate"}`. `hook_exports` raises `ValueError` on unreadable/unparseable files with the file path in the message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hook_exports.py
from pathlib import Path

import pytest

from untaped_recipe.domain.hook_exports import hook_exports, hook_exports_from_source


def test_detects_transform_only() -> None:
    src = "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
    assert hook_exports_from_source(src) == frozenset({"transform"})


def test_detects_dual_exports() -> None:
    src = "def transform(c, **kw):\n    return c\n\ndef validate(**kw):\n    return None\n"
    assert hook_exports_from_source(src) == frozenset({"transform", "validate"})


def test_ignores_nested_and_other_functions() -> None:
    src = "def helper():\n    def transform():\n        pass\n"
    assert hook_exports_from_source(src) == frozenset()


def test_detects_async_defs_as_exports() -> None:
    # async hooks are invalid at runtime, but the scan reports the name;
    # the worker rejects them loudly at call time.
    src = "async def validate(**kw):\n    return None\n"
    assert hook_exports_from_source(src) == frozenset({"validate"})


def test_file_variant_raises_with_path_on_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "hook.py"
    bad.write_text("def transform(:\n", encoding="utf-8")
    with pytest.raises(ValueError, match=str(bad)):
        hook_exports(bad)
```

- [ ] **Step 2:** Run `uv run pytest tests/test_hook_exports.py -p no:cacheprovider -v` — expected: FAIL (module not found).
- [ ] **Step 3: Implement**

```python
# src/untaped_recipe/domain/hook_exports.py
"""AST-level discovery of hook entry points, without importing hook code."""

from __future__ import annotations

import ast
from pathlib import Path

HOOK_FUNCTION_NAMES = frozenset({"transform", "validate"})


def hook_exports_from_source(source: str) -> frozenset[str]:
    """Return which hook entry points a module's top level defines."""
    tree = ast.parse(source)
    found = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in HOOK_FUNCTION_NAMES
    }
    return frozenset(found)


def hook_exports(module_file: Path) -> frozenset[str]:
    """Scan a hook module file for entry points; never imports it."""
    try:
        source = module_file.read_text(encoding="utf-8")
        return hook_exports_from_source(source)
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError(f"cannot scan hook module {module_file}: {error}") from error
```

- [ ] **Step 4:** Run the tests again — expected: PASS. Run `uv run mypy`.
- [ ] **Step 5:** Commit: `git commit -m "feat: AST-based hook export discovery"`.

### Task 2: Drop `kind` from hook manifests and resolver

**Files:**
- Modify: `src/untaped_recipe/domain/hook_project.py` (the hook-definition model parsed from `[tool.untaped_recipe.hooks]`, and `validate_hook_modules`)
- Modify: `src/untaped_recipe/infrastructure/hook_resolver.py` (`BuiltinHookRef`, `UvHookRef`, `ensure_hook_kind`, `HookResolver._resolve_project`)
- Modify: `src/untaped_recipe/builtins/registry.py` (`BuiltinHook`)
- Modify: callers of `ensure_hook_kind` in `src/untaped_recipe/application/apply_recipe.py` and `application/run_hook.py`
- Test: `tests/test_hook_projects.py`, existing resolver tests

**Interfaces:**
- Consumes: `hook_exports(module_file: Path) -> frozenset[str]` from Task 1.
- Produces: `UvHookRef` and `BuiltinHookRef` each carry `exports: frozenset[str]` instead of `kind`. `ensure_hook_supports(ref: HookRef, hook: str, *, verb: str) -> None` replaces `ensure_hook_kind` and raises `ValueError` when `verb not in ref.exports`, message: `f"{verb} step hook {hook!r} does not export a {verb}() function"`. Manifest hook rows are `{ module = "..." }`; a row still containing `kind` is rejected with: `"hook 'NAME' declares kind; kind was removed in 0.9 — export transform()/validate() instead"`.

- [ ] **Step 1: Write/adjust failing tests.** In `tests/test_hook_projects.py` add:

```python
def test_manifest_kind_is_rejected(tmp_path: Path) -> None:
    # build a hook project pyproject with: "x" = { kind = "transform", module = "m.hooks.x" }
    # (reuse this test file's existing project-fixture helper)
    with pytest.raises(ValueError, match="kind was removed in 0.9"):
        read_hook_metadata(project_root)


def test_resolver_carries_exports_from_ast_scan(...) -> None:
    # scaffolded module defining only transform() -> ref.exports == frozenset({"transform"})
    ...


def test_ensure_hook_supports_rejects_missing_verb(...) -> None:
    # ref with exports={"transform"}; verb="validate" -> ValueError "does not export a validate() function"
    ...
```

Flesh each `...` out with the fixture helpers already used by neighboring tests in this file (they construct hook projects on disk; follow their exact pattern).

- [ ] **Step 2:** Run `uv run pytest tests/test_hook_projects.py -p no:cacheprovider -v` — new tests FAIL; note which old kind-based tests fail too (they get rewritten in Step 3).
- [ ] **Step 3: Implement.**
  - `domain/hook_project.py`: delete the `kind` field from the hook-definition model; in the pre-validation of each hook row, raise the exact "kind was removed in 0.9" error above when the key is present. `HookKind` (the Literal type) survives only if the worker protocol still needs the type alias — the wire `kind` field in `worker_protocol.py` is the *verb to invoke* and is unchanged.
  - `infrastructure/hook_resolver.py`: replace `kind: HookKind` with `exports: frozenset[str]` on both ref dataclasses. In `_resolve_project`, after `validate_hook_modules`, compute `exports = hook_exports(module_path)` for the resolved module file (resolve the path exactly the way `validate_hook_modules` does — extract a shared helper if it currently inlines that mapping) and reject empty exports: `f"hook module for {public_name!r} exports neither transform() nor validate()"`. Replace `ensure_hook_kind` with `ensure_hook_supports` as specified above.
  - `builtins/registry.py`: replace `BuiltinHook.kind` with `exports`; for `yaml_edit` use `frozenset({"transform"})` (derive with `hasattr(module, name)` at registry construction, not a literal, so builtins can't drift).
  - Update the two `ensure_hook_kind` call sites (planning in `apply_recipe.py`, debug run in `run_hook.py`) to `ensure_hook_supports(ref, hook, verb="transform"|"validate")` based on the step type. Delete kind-based assertions in old tests and assert on exports instead.
- [ ] **Step 4:** `uv run pytest -p no:cacheprovider` full suite green; `uv run mypy` clean.
- [ ] **Step 5:** Commit: `git commit -m "feat!: hook contract is the exported function name; manifest kind removed"`.

### Task 3: Dual-verb `hook run` inference

**Files:**
- Modify: `src/untaped_recipe/application/run_hook.py`, `src/untaped_recipe/cli/hook_commands.py` (the `hook run` command)
- Test: `tests/test_run_hook.py` (or wherever `hook run` behavior tests live — find with `grep -rl "hook_run" tests/`)

**Interfaces:**
- Produces: verb selection rule — single export: run it; both exports: `--file` present ⇒ `transform`, else `--kind transform|validate` required, error message: `"hook 'NAME' exports both transform() and validate(); pass --kind or --file"`. New optional `--kind` parameter on `hook run`.

- [ ] **Step 1:** Write failing tests: single-export transform hook runs without `--kind`; dual-export hook with `--file` runs transform; dual-export hook without `--file`/`--kind` exits with the exact error above; `--kind validate` on a dual hook runs validate.
- [ ] **Step 2:** Run them — FAIL.
- [ ] **Step 3:** Implement the selection in `run_hook.py` (pure logic: `select_verb(exports: frozenset[str], *, file_given: bool, kind: str | None) -> str`), thread `--kind` through the CLI command. Keep validate-step flag rejections (`--file`/`--diff`/content options) exactly as today once the verb resolves to validate.
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: hook run infers verb from exports; --kind disambiguates dual hooks"`.

### Task 4: Pack domain model — identity, manifest, refs

**Files:**
- Create: `src/untaped_recipe/domain/pack.py`
- Test: `tests/test_pack_domain.py`

**Interfaces:**
- Produces:
  - `PACK_PROJECT_PREFIX = "untaped-recipe-"`
  - `pack_name_from_project(project_name: str) -> str` — canonicalizes (PEP 503 dashes), strips the prefix; a bare `untaped-recipe` or empty remainder raises `ValueError`.
  - `RecipeEntry(path: str)`, `HookEntry(module: str)` (Pydantic, `extra="forbid"` — a `kind` key fails validation, which yields the Task 2 error at the metadata layer)
  - `PackManifest(name: str, version: str, requires_hook_api: str | None, recipes: dict[str, RecipeEntry], hooks: dict[str, HookEntry])` with `PackManifest.from_pyproject(project_root: Path) -> PackManifest` (both tables optional; missing `[tool.untaped_recipe]` raises `ValueError` naming the file; `version` comes from `[project].version`, defaulting to `"0"` when absent)
  - `PackRef(pack: str | None, name: str)` with `parse_ref(text: str) -> PackRef` — splits on the first `/`; refs containing path separators beyond one `/`, `..`, or empty segments raise `ValueError`.

- [ ] **Step 1:** Write failing tests covering: prefix stripping (`untaped-recipe-ansible` → `ansible`), underscore/dash canonicalization, bare-prefix rejection, manifest parse with both/one/no tables, `kind` in a hook row rejected, `parse_ref("ansible/set_owner")`, `parse_ref("set_owner")` (pack=None), `parse_ref("a/b/c")` rejected.
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement `domain/pack.py`. Reuse `read_hook_metadata`'s TOML-loading approach from `domain/hook_project.py` (tomllib) and delegate hook-row validation to the Task 2 model so the error text stays single-sourced. Migrate `requires_hook_api` parsing/enforcement (`validate_hook_project_contract`) to accept a `PackManifest`; keep the no-runtime-dep-on-untaped-recipe rule verbatim.
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: pack domain model (identity, manifest, qualified refs)"`.

### Task 5: PackLibrary — one library, packs.toml index, ambiguity-as-error

**Files:**
- Create: `src/untaped_recipe/infrastructure/pack_store.py` (replaces the librarian roles of `recipe_library.py`, `pack_library.py`, `hook_library.py`; those are deleted in Task 8 once nothing imports them)
- Test: `tests/test_pack_store.py`

**Interfaces:**
- Consumes: `PackManifest`, `pack_name_from_project`, `PackRef` from Task 4; `hook_exports` from Task 1.
- Produces `PackLibrary` with:
  - `__init__(self, *, library_root: Path)` — packs live at `library_root / "packs" / name`; index at `library_root / "packs.toml"` mapping name → `{ source = "<path-or-url>", rev = "<rev-or-empty>", version = "<[project].version at add time>" }`
  - `add(self, source_dir: Path, *, source: str, rev: str | None, name: str | None, force: bool) -> PackManifest` — validates the manifest, checks `requires_hook_api`, copies the directory (mirroring the copy/validation behavior in today's `HookLibrary.add`), errors on existing name without `force` (message names the pack and suggests `--force`/`--name`), writes the index entry
  - `remove(self, name: str) -> None`
  - `packs(self) -> list[PackManifest]`
  - `find_recipe(self, ref: PackRef) -> tuple[PackManifest, RecipeEntry]` and `find_hook(self, ref: PackRef) -> tuple[PackManifest, HookEntry]` — bare-name matches across >1 pack raise `ValueError` listing every `pack/name` candidate; zero matches raise `ValueError("recipe not found: ...")` / `("hook not found: ...")`

- [ ] **Step 1:** Failing tests: add a fixture pack then `find_recipe`/`find_hook` by bare and qualified name; two packs sharing a hook name → ambiguity error listing `a/x` and `b/x`; add duplicate name → error, `force=True` replaces; `remove` deletes dir + index row; index round-trips source, rev, and version.
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement. Write `packs.toml` with `tomlkit` if already a dependency, else emit minimal TOML by hand (check `pyproject.toml` first; do not add a dependency without checking). Copy semantics: `shutil.copytree` after validation, excluding `.git`.
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: unified PackLibrary with packs.toml source index"`.

### Task 6: `add` front doors — path and git URL, with confirmation

**Files:**
- Modify: `src/untaped_recipe/infrastructure/pack_store.py` (fetch helper), `src/untaped_recipe/cli/commands.py` (new `add` command)
- Test: `tests/test_pack_store.py`, CLI test module for commands

**Interfaces:**
- Produces: `fetch_pack_source(url: str, *, rev: str | None, dest: Path) -> Path` — `git clone --depth 1` (plus `--branch <rev>` when rev given; fall back to full clone + `git checkout <rev>` for commit SHAs), returns the checkout dir. CLI `add <path|git-url> [--rev] [--name] [--force] [--yes]`: URL detection = starts with `https://`, `git@`, or `ssh://`; before installing, print the pack's recipes and hooks and confirm via the SDK confirmation used by `batch_apply`-style flows (`--yes` skips; refuse piped stdin without `--yes`, same policy as destructive verbs).

- [ ] **Step 1:** Failing tests: URL detection table-test; fetch helper against a local `file://` git fixture repo (create one in tmp_path with `git init`; no network in tests); `add` prints recipe/hook names before confirm.
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement; route both front doors through `PackLibrary.add` so there is exactly one install path.
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: add packs from paths or git URLs with confirmation"`.

### Task 7: `new pack|recipe|hook` scaffolding

**Files:**
- Create: `src/untaped_recipe/infrastructure/pack_scaffold.py` (port `_scaffold`, `_hook_stub`, `hook_api_requirements`, and the recipe scaffold out of `hook_library.py`/`recipe_library.py`)
- Modify: `src/untaped_recipe/cli/commands.py` (a `new` Cyclopts sub-app with `pack`, `recipe`, `hook` commands)
- Test: `tests/test_pack_scaffold.py`

**Interfaces:**
- Consumes: `PACKAGE_VERSION`, `HOOK_API_VERSION`, `pack_name_from_project`, `PackManifest`.
- Produces:
  - `scaffold_pack(dest: Path, name: str) -> None` — pyproject with `[project] name = "untaped-recipe-<name>"`, `requires-python = ">=3.14"`, dev group `["untaped-recipe>=0.9"]`, `[tool.untaped_recipe] requires_hook_api = ">=0.9,<1"`, empty src package `<name>_pack/hooks/`, runs `uv lock`
  - `scaffold_recipe(pack_dir: Path, name: str) -> None` — writes `recipes/<name>/recipe.yml` (version-1 stub with one validate step commented out) and appends the manifest `recipes` row
  - `scaffold_hook(pack_dir: Path, name: str) -> None` — writes the module stub (today's `_hook_stub`, `TYPE_CHECKING` import intact, NO kind anywhere) and appends the manifest `hooks` row
  - Floors computed from constants exactly as `hook_api_requirements` does today, plus the `,<1` cap on `requires_hook_api`.

- [ ] **Step 1:** Failing tests: `scaffold_pack` output parses via `PackManifest.from_pyproject` with the exact floors above; `scaffold_hook` then `hook_exports` on the stub yields `{"transform"}` (or `{"validate"}` with a kind-of-stub flag mirroring today's `--kind` scaffold option); manifest rows appended idempotently (second scaffold of same name errors).
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement, then wire `new pack <name>`, `new recipe <pack>/<name>`, `new hook <pack>/<name>` CLI commands (qualified-arg parsing via `parse_ref`; `pack` resolves to a library pack dir or `./` path).
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: unified new pack/recipe/hook scaffolding"`.

### Task 8: CLI flattening + apply/resolution integration + delete old libraries

**Files:**
- Modify: `src/untaped_recipe/cli/commands.py` (composition root: top-level `add/remove/list/show/check/edit`; `list` default = recipes with source pack, `--hooks`/`--packs` views), `cli/hook_commands.py` (shrinks to `hook run`), `application/apply_recipe.py` + `application/run_bulk.py` + `infrastructure/hook_resolver.py` (hook resolution order: recipe's own pack → library via `PackLibrary.find_hook` → builtins), `settings.py` (library_root unchanged; drop settings for removed namespaces if any)
- Delete: `src/untaped_recipe/cli/recipe_commands.py`, `cli/pack_commands.py`, `infrastructure/recipe_library.py`, `infrastructure/pack_library.py`, `infrastructure/hook_library.py`
- Test: rewrite the corresponding command tests; keep `apply` behavior tests passing with refs resolved through `PackLibrary`

**Interfaces:**
- Consumes: everything above. `apply <ref|path>`: a path (contains `/` AND exists on disk, or ends `.yml`) is loaded directly; otherwise `parse_ref` + `PackLibrary.find_recipe`.
- Produces: final CLI surface exactly as the spec table (`new`, `add`, `remove`, `check`, `edit`, `list`, `show`, `hook run`, `apply`, `backup`, `config`). Emit record kinds consolidate to exactly: `recipe.outcome`, `recipe.backup`, `recipe.hook_run`, `recipe.recipe`, `recipe.hook`, `recipe.pack`, `recipe.check` — `recipe.pack_check` and `recipe.pack_recipe` die with their commands (breaking for pipe consumers; called out in the Task 13 migration note).

- [ ] **Step 1:** Write failing CLI tests for: `list` (recipes with pack column), `list --hooks`, `show pack`, `show pack/recipe`, `check <pack>` (runs manifest + AST export validation for every wired step), ambiguous `apply set_owner`-style ref error text. Add one test pinning the surviving kind names: grep-style assertion that the `kind=` values passed to `emit`/`render_rows` across `src/untaped_recipe/cli/` equal the seven-kind set above (import each command module's constant or scan the source tree — mirror however existing tests pin emit kinds, `grep -rn "recipe\." tests/ | grep kind` first).
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement; migrate resolution inside `HookResolver` to consult `PackLibrary` instead of `<library_root>/hooks`; delete the five dead modules and every import of them; rewrite their tests against the new surface (behavior parity: everything `recipe check`/`pack check` verified before must still be verified by `check`).
- [ ] **Step 4:** Full suite + mypy + `uv run pre-commit run --all-files --show-diff-on-failure` green.
- [ ] **Step 5:** Commit: `git commit -m "feat!: flatten CLI to the pack surface; single library resolution"`.

### Task 9: Structured `show`

**Files:**
- Modify: `src/untaped_recipe/cli/commands.py` (the `show` command from Task 8)
- Create: `src/untaped_recipe/cli/detail.py` (pure record builders, no I/O beyond reading the recipe file)
- Test: `tests/test_show_detail.py`

**Interfaces:**
- Consumes: `PackLibrary.find_recipe`/`find_hook`/`packs` (Task 5), `Recipe`/`InputSpec` from `domain/recipe.py`, `hook_exports` (Task 1).
- Produces record builders emitted via the SDK `emit` (single-object detail view):
  - `recipe_detail(ref: str, recipe: Recipe, path: Path) -> dict` — keys: `ref`, `description`, `inputs` (list of `{name, type, required, default, description, sensitive}` from `InputSpec`), `steps` (list of `{type, file_or_files, hook}` summaries), `hooks` (sorted unique hook names referenced by steps), `path`
  - `hook_detail(ref: str, entry: HookEntry, exports: frozenset[str], module_file: Path) -> dict` — keys: `ref`, `module`, `exports` (sorted list), `path`
  - `pack_detail(manifest: PackManifest, root: Path) -> dict` — keys: `name`, `version`, `recipes` (name + first line of each recipe's description), `hooks` (name + exports), `path`
- `show` never dumps raw file text; `edit <ref>` remains the way to open the file.

- [ ] **Step 1:** Failing tests: build a fixture pack whose recipe declares a typed input (`owner`, type str, required, description, one `sensitive` input) — `recipe_detail` returns the inputs list with all six keys and redacts nothing (redaction is a preview concern; `show` displays the *spec*, not values); `hook_detail` reports `exports` from the AST scan; `pack_detail` lists recipes and hooks. CLI test: `show <pack>/<recipe> --format json` emits one `recipe.recipe` record containing the `inputs` array.
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement `cli/detail.py`; wire `show` to dispatch on what the ref resolves to (pack name alone → `pack_detail`; recipe ref → `recipe_detail`; hook ref → `hook_detail`; ambiguity errors come from `PackLibrary` unchanged). Pass the pre-built dicts to `emit` (dict path preserves key pruning).
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: structured show renders inputs, steps, and hook exports"`.

### Task 10: Library doctor — `check` with no arguments

**Files:**
- Modify: `src/untaped_recipe/cli/commands.py` (`check` argument becomes optional), `src/untaped_recipe/infrastructure/pack_store.py` (index/dir reconciliation helper)
- Test: `tests/test_pack_store.py`, CLI check tests

**Interfaces:**
- Consumes: `PackLibrary.packs()` and the per-pack check machinery from Task 8.
- Produces: `PackLibrary.reconcile(self) -> list[str]` returning problem strings, exactly: `f"pack '{name}' is in packs.toml but missing from packs/"` and `f"pack directory '{name}' is not recorded in packs.toml"`. `check` with no args runs `reconcile()` plus the normal check on every installed pack, emits one `recipe.check` record per finding (with a `pack` field), and exits non-zero if anything failed.

- [ ] **Step 1:** Failing tests: library with two packs, one index row pointing at a deleted dir, one orphan dir → `reconcile()` returns both exact strings; CLI `check` (no args) exits non-zero and reports both plus per-pack results; healthy library exits 0.
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests + mypy green.
- [ ] **Step 5:** Commit: `git commit -m "feat: no-arg check validates the whole library and its index"`.

### Task 11: Versions, release tooling, parity tests

**Files:**
- Modify: `pyproject.toml` (0.9.0), `src/untaped_recipe/_version.py`, `src/untaped_recipe/hook_api.py` (HOOK_API_VERSION 0.9.0), `scripts/release.py` (`smoke-hook-init` → `smoke-new`: `new pack hook_api_smoke` + `new hook hook_api_smoke/probe` via the installed CLI; verify-versions floors `>=0.9` / `>=0.9,<1`), parity tests in `tests/test_infrastructure.py` / `tests/test_hook_api_contract.py`
- Test: release-helper tests updated alongside

- [ ] **Step 1:** Update parity tests first (they encode the new expected floors) — FAIL run.
- [ ] **Step 2:** Bump the three version sites; update `hook_api_requirements` cap; rewrite `smoke-hook-init` to the `new`-based flow keeping the isolated-env/`--no-project`/`--with untaped-recipe==<version>` machinery from PR #17 exactly as is.
- [ ] **Step 3:** `uv run python scripts/release.py verify-versions 0.9.0` passes; full suite green.
- [ ] **Step 4:** Commit: `git commit -m "chore!: 0.9.0 versions, capped hook API floor, new-based release smoke"`.

### Task 12: Template token policy — `unknown_tokens: error | keep`

Context: today, unknown *bare* input names already raise, but non-bare tokens
(`{{ a.b }}`, `{{ x | upper }}`) silently pass through. That pass-through is
load-bearing for templates emitting GitHub Actions (`${{ github.ref }}`) or Helm
(`{{ .Values.x }}`) files, and a trap for users expecting Jinja. Default becomes
strict, with a per-step escape hatch.

**Files:**
- Modify: `src/untaped_recipe/domain/recipe.py` (`TemplateStep` gains the field), `src/untaped_recipe/domain/templates.py`, `src/untaped_recipe/hook_worker.py` (the stdlib-only twin renderer, lines ~26-53), `src/untaped_recipe/application/apply_recipe.py` (thread the step field into the render call), `src/untaped_recipe/builtins/hooks/yaml_edit.py` (`_render_value` forwards an optional top-level `unknown_tokens` args key, default `"error"`)
- Test: template-step tests, `tests/test_hook_worker.py`, new `tests/test_template_parity.py`

**Interfaces:**
- Both renderer copies gain the same signature: `render_template(template: str, inputs: Mapping[str, object], *, unknown_tokens: str = "error") -> str` (values `"error"` | `"keep"`; anything else raises `ValueError`).
- Under `"error"`: a bare unknown name keeps today's error (`f"template input {name!r} is not defined"`); any other `{{ ... }}` token raises `ValueError(f"template token {token!r} is not a bare input name; set unknown_tokens: keep to pass it through")`. Token discovery: every non-greedy `\{\{.*?\}\}` match that does not match the bare-identifier pattern.
- Under `"keep"`: known inputs render; every other token (bare-unknown included) passes through verbatim — today's behavior.
- `TemplateStep.unknown_tokens: Literal["error", "keep"] = "error"`; `helpers.render_template` exposes the same keyword (hook API addition, covered by the Task 11 HOOK_API_VERSION bump).

- [ ] **Step 1:** Failing tests (run the same table against BOTH copies in `tests/test_template_parity.py` — import `domain.templates.render_template` and the `hook_worker` copy, assert identical output or identical exception text per case): `{{ owner }}` with owner defined renders in both modes; `{{ owner }}` undefined raises "is not defined" under `error`, passes through under `keep`; `${{ github.ref }}` raises the "set unknown_tokens: keep" error naming `'{{ github.ref }}'` under `error`, survives verbatim under `keep`; `{{ .Values.x }}` same; invalid mode string raises. Plus a `TemplateStep` schema test: field defaults to `"error"`, rejects other values, and a template-step apply test showing a workflow-file template plans cleanly with `unknown_tokens: keep`.
- [ ] **Step 2:** FAIL run.
- [ ] **Step 3:** Implement identically in both copies (the worker is stdlib-only and cannot import engine modules — keep the two implementations byte-similar and cross-referenced by comment; the parity test is the drift guard). Thread the step field in `apply_recipe.py` and the args key in `yaml_edit.py`.
- [ ] **Step 4:** Full suite + mypy green; commit: `git commit -m "feat!: template steps default to strict tokens with unknown_tokens: keep opt-out"`.

### Task 13: Docs, invariants, migration note

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/recipes.md`, `docs/hooks.md`
- Create: `docs/packs.md`; changelog/release-notes migration note

- [ ] **Step 1:** Write `docs/packs.md` (pack concept, manifest example from the spec, sharing via `add <path|git-url>`, qualified names, resolution + ambiguity rule). Rewrite `docs/hooks.md` for the function-name contract and dual-verb hooks; delete kind-migration prose. Update `README.md` command table. Document `unknown_tokens: error | keep` in `docs/recipes.md` with the GitHub Actions/Helm example.
- [ ] **Step 2:** Add the six permanent invariants to `AGENTS.md` verbatim from the spec §"Wave 3": no control flow in the recipe schema; planning is the only execution; no state/inventory; builtins minimal; pure-data hook boundary; pipe composability (untaped envelope ingestion via `apply --stdin` + `record` in input `from`) is a protected feature.
- [ ] **Step 3:** Migration note (changelog): manifests drop `kind`; library moves under `packs/`; floors `>=0.9,<1`; CLI renames table (old verb → new verb); emit kinds `recipe.pack_check`/`recipe.pack_recipe` removed (pipe consumers rekey on `recipe.check`/`recipe.recipe`); template steps now strict by default (`unknown_tokens: keep` restores pass-through); recipe.yml schema itself is unchanged (`version: 1`).
- [ ] **Step 4:** `uv run pre-commit run --all-files --show-diff-on-failure` green (docs formatting). Commit: `git commit -m "docs: pack unification docs, invariants, migration note"`.
- [ ] **Step 5:** Full release gate from Global Constraints, then open the PR (release itself follows `docs/release.md` after review).

---

## Self-review notes

- Spec coverage: identity/manifest (T4), hook contract + AST check (T1-T2), dual-verb + `hook run` (T3), library + index/version + ambiguity (T5), sharing front doors + confirm (T6), `new` scaffolding + floors (T7), CLI flattening + resolution + emit-kind consolidation + deletions (T8), structured `show` (T9), library doctor (T10), versions/release smoke (T11), `unknown_tokens` template policy + parity test (T12), invariants/docs/migration (T13). Wave 2 (test harness) is intentionally out — separate plan against the 0.9.0 codebase.
- Known deliberate deference: exact fixture-helper names in existing test files and post-PR-#17 line numbers are re-read at execution (Task 0 Step 3 covers this); interfaces and error strings above are the contract.
