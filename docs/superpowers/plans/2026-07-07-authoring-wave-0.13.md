# untaped-recipe 0.13.0 — authoring wave implementation plan

Design brief for the authoring wave: the `add --force` data-loss guard plus the
quick-wins bundle adjudicated from the 2026-07-07 authoring-seat review
(brainstormed and ruled with Alexis in the orchestration session). Deferred
with triggers, NOT in this wave: `add --editable` (custom pack_store machinery;
revisit when re-add friction recurs) and the transform warning channel
(revisit at the first pack whose transform must report without failing).

Execution model: self-implemented (delegation suspended). The brief still
binds exactly as it would bind Codex: pinned contracts are contracts, test
intent is mandatory, deviations get recorded in the PR body, and the
conformance subagent reviews the diff against this brief.

## Global constraints

- One commit per task, TDD where test intent is pinned, full gates per commit:
  `uv --cache-dir .uv-cache run pytest` / `ruff check` / `ruff format --check`
  / `mypy` (all via `uv --cache-dir .uv-cache run`).
- Never weaken an existing CLI-text assertion; extend, don't reword, unless a
  task pins new text.
- No hook wire-protocol changes. `HOOK_API_VERSION` stays `0.9.0` (nothing in
  this wave touches the helper contract).
- No release-workflow changes.
- Pipe visibility: `list --hooks` gains builtin rows (new rows, same columns —
  flag in the PR body's release notes); no emit kind changes; no column
  removals anywhere.
- Only `UntapedError` subclasses count as per-item failures inside
  `batch_apply` callbacks; anything that must fail an `add` cleanly is raised
  as `ConfigError` before `batch_apply` starts or wrapped at the CLI boundary.

## Task A — install content-hash guard + `--discard-edits`

Problem (BUG class): `edit` steers users to modify the library copy in place
(`README.md` says so explicitly), while `add --force` does
`rmtree(dest)` + `copytree(...)` with no guard — in-library edits are
destroyed silently, and `--force -y` never even prompts. `new recipe`/`new
hook` scaffolding into an installed pack is a second sanctioned source of
in-library edits.

Contract:

- `pack_store._IndexEntry` gains `content_hash: str = ""`. `PackLibrary.add`
  computes the hash of the freshly installed tree and records it in the
  `packs.toml` row (alongside `source`/`rev`/`version`). Legacy rows without
  the field parse as `""` = unguarded.
- New pure helper in `pack_store.py`: `pack_content_hash(root: Path) -> str` —
  sha256 over the pack tree: walk files sorted by POSIX relative path,
  feed each path string and file bytes into one digest. The walk honors the
  same ignore set `copytree` uses (Task B's `PACK_COPY_IGNORE` — shared
  constant, single definition).
- New method `PackLibrary.local_edits(name: str) -> bool` — True iff the pack
  is installed, its index row has a non-empty `content_hash`, and
  `pack_content_hash(dest)` differs from it. Absent pack or empty recorded
  hash → False.
- `PackLibrary.add` gains keyword `discard_edits: bool = False`. On
  `force=True` over an existing entry with `local_edits` True and
  `discard_edits=False`, raise `ValueError` with exactly:
  `pack '{installed_name}' has local edits in the library (via edit or new
  recipe/hook); re-run with --discard-edits to overwrite them`.
  With `discard_edits=True` the install proceeds and records the new hash.
- `add_command` gains flag `--discard-edits` (negative="", default False),
  passed through to `library.add`. Before `batch_apply` starts, the command
  fail-fasts the same condition as `ConfigError` (same message) so the user
  is never prompted for a confirmation that can only fail; when
  `--discard-edits` IS set and edits exist, `_render_pack_add_preview` gains a
  trailing stderr line: `Warning: library copy has local edits; --discard-edits
  will overwrite them.`
- Non-force `add` over an absent pack always records the hash. `remove` needs
  no change (row deletion already drops the hash).

Test intent (`tests/test_library_and_backup.py` + `tests/test_cli.py`):
edited-library-copy + `--force` → blocked with the pinned message (both the
store-level ValueError and the CLI ConfigError paths); `--force
--discard-edits` proceeds and re-records the hash (a second plain `--force`
right after succeeds); unedited `--force` proceeds without `--discard-edits`;
legacy index row without `content_hash` → force proceeds (unguarded) and the
row gains a hash afterward; `pack_content_hash` is stable across an ignored-dir
addition (drop a `__pycache__/x.pyc` into the tree → hash unchanged).

## Task B — `add` ignore set

Contract:

- Module constant in `pack_store.py`:
  `PACK_COPY_IGNORE = (".git", ".venv", "__pycache__", "dist", "build",
  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".uv-cache", "*.egg-info")`.
- `copytree` in `PackLibrary.add` uses
  `ignore=shutil.ignore_patterns(*PACK_COPY_IGNORE)` (replacing the lone
  `".git"`), and `pack_content_hash` prunes names matching the same patterns
  (fnmatch semantics, applied to directory and file names at every level, same
  as `ignore_patterns`). `uv.lock` and `pyproject.toml` are NOT ignored.

Test intent: a source pack containing `.venv/`, `__pycache__/`, `dist/`, and a
`something.egg-info/` installs without any of them appearing in the library
copy; the hash test from Task A covers hash/ignore agreement (field-walk:
one constant, two consumers).

## Task C — env + lock hygiene

Three independent fixes, one commit:

1. **VIRTUAL_ENV scrub.** `UvHookWorker._start` pops `VIRTUAL_ENV` from the
   copied env before `Popen` (a parent venv otherwise makes `uv run`
   mis-target or warn). Test intent: monkeypatched `Popen` capture — with
   `VIRTUAL_ENV` set in the parent env, the spawned env lacks it; `PYTHONPATH`
   handling is unchanged.
2. **Stale-lock re-headline.** Today a stale `uv.lock` makes
   `uv run --locked` die pre-handshake and the user sees `hook worker exited
   before ready` with uv's actual explanation buried in trailing diagnostics.
   In `_ensure_ready`, when the worker exits before ready (EOF path) and the
   drained diagnostics carry uv's stale-lock signature (both substrings
   `lockfile` and `--locked` present, case-insensitive), the headline becomes:
   `pack lockfile is out of date — run 'uv lock' in {project_root}`
   (diagnostics still appended below, as today). All other pre-ready failures
   keep the existing headlines exactly.
3. **`check` lock-freshness probe.** New helper in
   `infrastructure/uv_project.py`: `check_lock(project_root: Path) -> None` —
   runs `uv lock --check` in the project; nonzero exit raises `ValueError`
   with exactly `lockfile is stale — run 'uv lock' in {project_root}` plus
   uv's stderr detail appended after `: ` when non-empty; missing uv
   executable keeps the existing "uv executable not found" wording of
   `lock_project`. `check_pack.py` calls it wherever it today asserts mere
   existence of `uv.lock` for a hook project (`_check_pack`,
   `_check_project_lock`, `_check_local_hook_project`) — existence check
   first (keep those messages), then freshness. The probe runs **at most once
   per project root per CLI invocation** (results memoized — a pack with N
   recipes must not spawn N subprocesses), and only for projects that
   actually declare hooks or are hook projects (the current call sites
   already scope this).

Test intent: stale-lock worker EOF → pinned re-headline with diagnostics
below (extend the existing `_FakeProcess` harness in
`tests/test_hook_projects.py`); non-stale EOF keeps `hook worker exited before
ready`; `check` on a pack whose `uv lock --check` fails reports an error row
containing the pinned freshness message (subprocess monkeypatched — no real uv
in unit tests); memoization: N recipes in one pack → exactly one probe call.

## Task D — ref-vs-path hints

Problem: ref classification is purely syntactic (`is_explicit_recipe_path`),
and each branch's not-found error never suggests the other form.

Contract:

- Library-ref direction: when a library lookup fails with `... not found:
  {ref}` at a CLI entry point (`resolve_apply_recipe`'s library branch and
  `commands._resolve_target`) and `Path(ref_text)` exists on disk (file or
  directory), the raised message gains the suffix:
  ` (a path named '{ref_text}' exists — pass it as an explicit path: prefix
  ./ or use its full path)`.
- Explicit-path direction: when `resolve_explicit_recipe` fails with `recipe
  file not found: {path}` for a *relative, single-segment-ish* input whose
  basename (minus `.yml`/`.yaml`) resolves in the library as a pack or recipe,
  append: ` (did you mean the library ref '{name}'?)`. Cheap check, best
  effort — no library scan on absolute paths.
- Ambiguous-ref and validation errors are untouched. No behavior change other
  than message suffixes; existing assertions keep passing (suffixes extend,
  never replace).

Test intent: `show demo` with `./demo/` on disk but nothing installed → error
carries the path hint; `apply ./demo/recipe.yml`-style miss where a library
recipe `demo` exists → ref hint; plain misses with nothing on disk / in the
library keep today's exact messages.

## Task E — builtin visibility

Problem: `yaml_edit` (the only builtin) is runnable but invisible — `list
--hooks` iterates installed packs only; `show`/`edit` resolve library-only.

Contract:

- `list --hooks` appends one row per `BUILTIN_HOOKS` entry after the library
  rows: `{pack: "(builtin)", name, ref: name, module: <module.__name__>,
  path: <module.__file__>}` — same columns as `_hook_row`, kind stays
  `recipe.hook`. Builtin rows appear even when no packs are installed (and
  suppress the "no packs installed" info message only for `--hooks` mode when
  builtins exist — pin: message still shows for recipes/packs modes).
- `show <name>`: `_resolve_target` gains a builtin fallback — bare names only,
  tried after pack/recipe/hook library lookups miss (library shadows builtins,
  matching `HookResolver` precedence). Renders via the existing `hook_detail`
  with ref = the bare name, exports from the registry, module file from the
  module. New `_ResolvedTarget` field to carry it (field-walk: show + edit
  both consume `_resolve_target`).
- `edit <builtin>` rejects with `ConfigError`: `built-in hooks are
  engine-owned and cannot be edited: {name}`.

Test intent: `list --hooks` on an empty library shows the `yaml_edit` row;
with an installed pack, library rows come first; `show yaml_edit` renders
detail with its exports; `edit yaml_edit` → pinned error; a library hook
named `yaml_edit` shadows the builtin in `show` (matches resolver semantics).

## Task F — hook-pytest scaffold (locked 0.9 Wave-2 decision, reaffirmed in the 0.10 spec, never implemented)

Contract:

- `scaffold_hook` additionally writes `tests/test_hook_<module_leaf>.py` in
  the pack (creating `tests/` if needed). Collision with an existing file →
  the existing "hook already exists"-style guard pattern: raise `ValueError`
  `hook test already exists: {path}` before writing anything.
- Stub shape (kind-dependent, mirrors `_hook_stub`): imports the hook module
  (`<package>.hooks.<leaf>`) and `HookHelpers` from
  `untaped_recipe.hook_worker` (the runtime class — importable in the pack's
  dev env via the existing dev dep), and asserts default behavior: transform
  kind → `transform("hello\n", inputs={}, target=".", file="example.txt",
  args={}, helpers=HookHelpers())` returns the content unchanged; validate
  kind → `validate(inputs={}, target=".", args={}, helpers=HookHelpers())`
  returns the pass verdict (`{"status": "pass"}`-shaped, assert via
  `helpers.pass_()` equality).
- `scaffold_pack`'s dev group gains `"pytest"` next to the existing hook-API
  dev dep, so `uv run --project <pack> pytest` works out of the box on new
  packs (existing packs are not migrated — note in README).
- Rollback parity: the `except` cleanup in `scaffold_hook` also removes the
  test file it created (and `tests/` if this call created it empty), matching
  the existing manifest-row/module rollback.
- Safety rails: `tests/test_hook_*.py` files do not trip
  `orphaned_test_dirs` (it only inspects directories — lock this with a test,
  since `check` must stay green on a scaffolded pack).

Test intent (`tests/test_scaffold.py` or wherever scaffold tests live):
transform-kind scaffold produces an importable test file whose content
references the module and `HookHelpers`; validate-kind variant; lock-failure
rollback removes the test file; scaffolded pack passes `check` (including
`orphaned_test_dirs`); `scaffold_pack` pyproject contains `pytest` in dev.

## Task G — version 0.13.0 + docs

- `pyproject.toml` version → `0.13.0`; relock (`uv lock`).
- README: `add --discard-edits` + the guard semantics, ignore set, `check`
  lock-freshness probe, builtin visibility, hook-pytest scaffold (and the
  note that pre-0.13 packs don't gain the pytest dev dep automatically).
- `SKILL.md` (packaged skill is a source artifact — repo rule): update the
  command behaviors this wave changes (`add`, `check`, `list`, `show`,
  `new hook`).
- PR body: release notes flag the pipe-visible addition (builtin rows in
  `list --hooks`) and the new `add` failure mode.

## Self-review gates (before opening the PR)

**Field-walk** (every pinned field/flag to its producing type):
`content_hash` → `_IndexEntry` + `_read_index`/`_write_index`;
`--discard-edits` → `add_command` param → `PackLibrary.add` kwarg;
`PACK_COPY_IGNORE` → both `copytree` and `pack_content_hash`;
builtin carrier → `_ResolvedTarget` new field → `show` + `edit` branches;
`pytest` dev dep → `scaffold_pack` template string; re-headline →
`_ensure_ready` EOF path only.

**Decision-walk** (new standing rule, first application — every locked
decision in the governing specs/rulings maps to a task or an explicit
deferral):

| Locked decision | Where |
|---|---|
| Hash guard + `--discard-edits` (brainstorm ruling 1) | Task A |
| `add --editable` | DEFERRED — BACKLOG, trigger = recurring re-add friction |
| Transform warning channel (ruling 2) | DEFERRED — BACKLOG, trigger = first pack needing non-fatal reporting |
| All six quick wins ship (ruling 3) | Tasks B–F |
| Decision-walk rule itself (ruling 4) | This section |
| Authoring wave = 0.13.0, ensure = 0.14.0 (ruling 5) | Task G / ROADMAP |
| 0.9 Wave-2: `new hook` scaffolds direct pytest | Task F |
| 0.10 spec: hook scaffold keeps direct-pytest form | Task F |
