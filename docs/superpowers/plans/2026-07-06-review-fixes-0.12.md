# untaped-recipe 0.12.0 — external-review fixes implementation plan

> **For agentic workers:** this is a design brief (plan-altitude directive):
> interfaces, contracts, and test intent are pinned; function bodies and test
> internals are the implementer's. Execute task-by-task, one commit per task,
> full gates green per commit. If a pinned contract is unimplementable as
> written, STOP and report — do not improvise.

**Goal:** Fix the three accepted findings from the 2026-07-06 whole-tool
review — hook timeout conflating uv env-sync with hook runtime, backups
having no retention story, and the first-five-minutes text/UX defects —
plus the small ride-alongs, as 0.12.0.

**Source of record:** the review findings as adjudicated 2026-07-06
(scope ruling: findings 1–3 + ride-alongs; preview scaling deferred;
message-string centralization rejected as speculative).

## Global constraints

- Version 0.12.0 (MINOR). `HOOK_API_VERSION` **stays 0.9.0** — Task 1 changes
  the CLI↔worker wire protocol, but the worker script ships inside the
  installed CLI (`Path(hook_worker.__file__)`), so client and worker can never
  skew; the authored-hook API (function signatures, helpers, `requires_hook_api`)
  is untouched. Scaffold requirement strings unchanged.
- Gates per commit: `uv --cache-dir .uv-cache run pytest`, `ruff check`,
  `ruff format --check`, `mypy` (CI is source of truth).
- `hook_worker.py` must remain stdlib-only.
- No release-workflow changes; no new runtime dependencies.
- Existing behavior not named below is frozen; ~130 CLI-text substring
  assertions exist in `tests/test_cli.py` — update the ones your wording
  changes touch, never weaken what they assert.

---

## Task 1 — hook-env ready handshake (finding 1)

**Files:** `src/untaped_recipe/worker_protocol.py`, `src/untaped_recipe/hook_worker.py`,
`src/untaped_recipe/infrastructure/hook_worker_client.py`,
`src/untaped_recipe/settings.py`, CLI wiring in `src/untaped_recipe/cli/common.py`
(or wherever the pool is constructed), tests in the existing worker-client suite.

**Problem being fixed:** `UvHookWorker._start` spawns
`uv run --project <pack> --locked --no-dev python worker.py`; uv creates/syncs
the pack env inline on first use, and the first request waits under
`hook_timeout_seconds` (default 60) — so a cold cache or lagging corporate
mirror produces `hook worker timed out after 60s`, blaming a hook that never ran.

**Pinned contracts:**

- `worker_protocol.py` gains `READY = "ready"`. The worker, after completing
  its imports and before reading any request line, writes exactly one NDJSON
  line `{"ready": true}` to stdout, then enters the request loop. (Stdlib-only.)
- `RecipeSettings` gains
  `hook_startup_timeout_seconds: float = Field(default=300, ge=0)` —
  `0` means unbounded, same semantic as `hook_timeout_seconds`.
- `UvHookWorker` waits for the ready line under the startup bound before the
  first request is written; `hook_timeout_seconds` applies only to
  request→response waits after ready. Constructor plumbing mirrors how
  `hook_timeout_seconds` flows today (pool → worker).
- `UvHookWorkerPool` (and `UvHookWorker`) accept an optional
  `startup_notice: Callable[[Path], None] | None = None`, invoked once per
  worker spawn with the hook project root when the startup wait begins. The
  CLI wires it to the existing quiet-gated UI message plumbing with the text
  `preparing hook environment for {path}...` (stderr; suppressed by `--quiet`).
- Startup-timeout failure is a `FatalHookWorkerError` whose message
  distinguishes startup from hook runtime and names the likely cause:
  `hook environment for {project_root} not ready after {n:g}s (uv creates the
  pack environment on first use; a cold cache or lagging package index makes
  this slow — raise hook_startup_timeout_seconds or pre-run 'uv sync' in the
  pack)`. Diagnostics draining/settle behavior matches the existing
  request-timeout path. The existing request-timeout message is unchanged.
- A non-ready first line (a response or garbage before ready) is a
  `FatalHookWorkerError` (`malformed hook worker handshake: {line!r}`).

**Test intent (names pinned, bodies yours):**

- `test_hook_timeout_starts_after_ready` — a stub worker that sleeps past
  `hook_timeout_seconds` *before* emitting ready but responds instantly after:
  call succeeds when the sleep is under the startup bound.
- `test_startup_timeout_names_environment_not_hook` — stub never emits ready;
  error message contains "not ready" and "environment", not "hook worker timed
  out after".
- `test_worker_exits_before_ready_reports_crash` — stub exits immediately
  (EOF); message is the crash path, not a timeout. This closes the untested
  `hook worker exited before request/response` EOF paths
  (`hook_worker_client.py:299,311`) — add direct coverage for both.
- `test_confirm_accept_applies_changes` — the review found the confirm UI
  double only ever declines; add the accept path through a real `apply`
  (CLI-level, hook-free steps per existing convention).
- Existing stub-worker tests gain the ready line in their fixtures — update
  the shared stub once, not per-test.

## Task 2 — backup lifecycle (finding 2)

**Files:** `src/untaped_recipe/infrastructure/backup.py`,
`src/untaped_recipe/cli/backup_commands.py`, `src/untaped_recipe/settings.py`,
backup tests.

**Pinned contracts:**

- `RecipeSettings` gains `backup_keep: int | None = Field(default=None, ge=1)`
  and `backup_max_age_days: int | None = Field(default=None, ge=1)`.
- New verb `backup prune` with flags `--keep N` (ge=1) and `--older-than DAYS`
  (integer days, ge=1). Flags override the corresponding settings; with
  neither flags nor settings configured → `ConfigError("backup prune needs
  --keep/--older-than or backup_keep/backup_max_age_days settings")`.
- Prune semantics: bundles are ordered newest-first by their existing
  timestamp; a bundle is pruned if it falls outside the newest `keep` OR is
  older than the age bound (union of the two conditions when both apply).
  Deletion is destructive → goes through `batch_apply(destructive=True)` with
  the fleet-standard preview → confirm/`--yes` → progress flow (same shape as
  `backup restore` after 0.9 T12). Emits `recipe.backup` rows for pruned
  bundles; summary reports pruned/kept counts and reclaimed bytes.
- `BackupStore` gains the minimal query/delete surface prune needs (e.g.
  `delete(backup_id)`); `list()` stays the single enumeration path.
- **Transactional restore:** `BackupStore.restore` currently flushes per file —
  rework it to stage all restored contents and commit via the same
  staged-tmp + `os.replace` + rollback semantics as
  `infrastructure/file_writer.py` (reuse the writer or its primitives — do not
  duplicate the algorithm). Hash-guard (`--force`) behavior is unchanged. A
  mid-restore failure must leave either the full set restored or the original
  state with an explicit incomplete-rollback report — same guarantees apply
  writes already have.
- Fold the deferred O(n²) fix: restore resolves the bundle **once**, not
  per-item (`_resolve`/`plan_restore` currently re-walk per file).

**Test intent:** prune by keep, by age, by both (union), settings fallback,
no-policy ConfigError, destructive-contract conformance
(`assert_destructive_contract` if the SDK helper fits), reclaimed-bytes
summary; restore mid-failure leaves no partial set (inject a failing write);
restore resolves bundle once (call-count spy or equivalent).

## Task 3 — text/UX batch (finding 3)

**Files:** `src/untaped_recipe/cli/commands.py`, `cli/backup_commands.py`,
`cli/hook_commands.py`, domain error mapping where recipe.yml is parsed
(`domain/` recipe loading), README.md, AGENTS.md, packaged SKILL.md,
`tests/test_cli.py` (wording assertions).

Each item is a pinned acceptance criterion; mechanism is yours:

1. **Help placeholders:** `new recipe --help`, `new hook --help`, and
   `hook run --help` must render a meaningful description for the ref
   argument (today the `<pack>/<name>` markup is swallowed, leaving `REF  /.`).
   Reword to markup-safe text (e.g. `PACK/RECIPE reference.`) rather than
   fighting the renderer.
2. **Empty library guidance:** `list` (and `--packs`/`--hooks`) and no-arg
   `check` on an empty library print a quiet-gated stderr hint naming
   `new pack` and `add`; stdout stays exactly as today (empty / valid pipe
   output) so pipelines are unaffected.
3. **Schema errors are domain errors:** recipe.yml validation failures
   (e.g. the documented `name:` rejection) surface as the existing
   ConfigError path with the file path and the violated rule in plain words —
   no raw pydantic ValidationError text, no pydantic.dev URL. YAML parse
   errors name the file path. Acceptance: a recipe.yml containing `name: x`
   errors with the path and a "name is not allowed here"-class message.
4. **Repr leaks:** outcome-table `inputs` cell renders `key=value` pairs
   (comma-joined), not a dict repr; `backup show` renders files as rows/lines,
   not one list repr.
5. **Status vocabulary:** the no-changes case uses one word everywhere —
   row status becomes `unchanged` (today rows say `planned` while the summary
   says "unchanged"). This is pipe-visible on `recipe.outcome`: update docs,
   SKILL.md, and tests; flag it in the PR body for the release notes.
6. **Ref grammar typo:** `apply --help` says `pack:recipe` — fix to
   `pack/recipe`.
7. **Corporate-mirror docs note:** README gains a short note next to the
   existing lagging-mirror guidance: uv provisions Python interpreters from
   GitHub; behind a blocked network set `UV_PYTHON_INSTALL_MIRROR` (and the
   `[tool.uv.sources]` override pattern already documented covers packages).
8. **stdin scalar wart:** an `apply --stdin` line that parses as a JSON
   scalar (`2024`, `true`) is treated as a bare path, not rejected — only
   JSON *objects* enter record parsing. Acceptance: a directory literally
   named `2024` works via `--stdin`.
9. **Dead defense:** `input_jinja.call_binop` repetition/power bounds are
   unreachable (operator allowlist rejects all BinOps first) — delete them
   or keep with an explicit belt-and-braces comment; sandbox tests unchanged.

**Test intent:** each item gets/updates a focused CLI-level assertion; item 5
also updates the pipe-shape test for `recipe.outcome`.

## Task 4 — version + docs sweep + release prep

- `pyproject.toml` + `_version.py` → 0.12.0, `uv.lock` refresh,
  `scripts/release.py verify_versions` parity check still green.
- README/AGENTS/SKILL updated for: startup-timeout setting + message, backup
  prune + retention settings, the text fixes above where documented.
- Full gates + `uv build` smoke.

## Self-review gate (before opening the PR)

Walk every pinned field/flag back to the type that produces or carries it
(the sweep wave's recurring defect class): `READY` → worker emit → client
wait; `hook_startup_timeout_seconds` → settings → pool → worker → error text;
`backup_keep`/`backup_max_age_days` → settings → prune resolution → ConfigError
text; prune rows → `recipe.backup` kind fields; `unchanged` → row status →
pipe record → docs.
