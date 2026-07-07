# untaped-recipe 0.14.0 — ensure wave implementation plan

Design brief for the ensure wave: the 0.9 spec's deferred ensure semantics
(`if_absent` + planning-time globs), preview scaling (BACKLOG promotion —
globs amplify bulk previews), the four items parked during the 0.13
implementation, and the 0.10 harness nits. Brainstormed and ruled with Alexis
in the orchestration session 2026-07-07; a second full critical review was
adjudicated the same day (stale findings discarded against 0.12/0.13 code;
surviving architecture challenges deferred to BACKLOG with the post-0.14
real-pack exercise as their adjudicator; one ruling folded in here — binary
handling, ruling 10).

Deferred with triggers, NOT in this wave: `state: dir` (engine is
file-content-based; trigger = first real recipe needing an empty dir),
byte-mode binary handling (trigger = first real recipe needing it; see Task
B's honest-error contract), tiered zero-dep hook execution, git-aware backup
skip, `apply --check` subcommand split, input-from simplification (all three
adjudicate at the real-pack exercise).

Execution model: self-implemented (delegation suspended). The brief binds
exactly as it would bind Codex: pinned contracts are contracts, test intent
is mandatory, deviations get recorded in the PR body, and the conformance
subagent reviews the diff against this brief.

## Global constraints

- One commit per task, TDD where test intent is pinned, full gates per
  commit: `uv --cache-dir .uv-cache run pytest` / `ruff check` /
  `ruff format --check` / `mypy` (all via `uv --cache-dir .uv-cache run`).
- Never weaken an existing CLI-text assertion; extend, don't reword, unless a
  task pins new text.
- No hook wire-protocol changes. `HOOK_API_VERSION` stays `0.9.0`; recipe
  schema stays `version: 1` (everything here is additive to the schema).
- Pipe surfaces: `recipe.outcome` columns unchanged; check rows keep the
  `recipe/status/path/error` column set (builtin rows are new rows, same
  columns — flag in the PR body's release notes). The preview is a
  stderr/human surface — the tiered collapse is NOT a pipe change.
- Only `UntapedError` subclasses count as per-item failures inside
  `batch_apply` callbacks; anything that must fail cleanly is raised as
  `ConfigError` before `batch_apply` starts or wrapped at the CLI boundary.
- No release-workflow changes.

## Task A — `if_absent` on template/copy

Ensure-style only-if-absent creation (0.9 spec §Deferred designs; ruling 2).

Contract:

- `TemplateStep` and `CopyStep` (domain/recipe.py) gain
  `if_absent: bool = False`.
- Planner semantics (application/apply_recipe.py, `_plan_template` /
  `_plan_copy`): when `if_absent` is true, the step is a no-op iff the dest
  exists in the *planned state* — i.e. if `step.dest` is a buffer key, it
  exists iff the buffered value is not `None`; otherwise it exists iff the
  confined target path `is_file()`. (Planned-state reads are the engine's
  existing rule for transforms; if_absent follows it: an earlier `remove` of
  the dest means a later `if_absent` step DOES write.)
- No preview/pipe changes: a skipped step simply contributes no `FileChange`.

Test intent: dest absent → created; dest present with different content →
zero FileChange for it (content untouched); dest created by an earlier step
in the same recipe → skipped; dest removed by an earlier step → written;
`if_absent: false` (and omitted) keeps today's overwrite behavior byte-exact.

## Task B — `globs:` + `exclude:` on transform/remove

Planning-time glob fan-out (rulings 3–5, 10). Literal `files:` stays a
parse-time expansion; `globs:` expands per target at plan time.

Schema contract (domain/recipe.py):

- `TransformStep` and `RemoveStep` accept `globs` (non-empty list of
  non-empty strings). Exactly one of `file` / `files` / `globs` per step —
  the existing fan-out validator's message becomes
  `{step_type} step requires exactly one of file, files, or globs`.
  Glob-bearing steps pass through `_normalize_file_fanout` unexpanded; the
  step models carry `globs` alongside a now-optional `file`, with a
  model-level exactly-one guard so an instantiated step always has file XOR
  globs.
- `exclude` (list of non-empty strings) is valid only alongside `globs`;
  otherwise validation fails with `exclude is only valid with globs`.
- `optional` is rejected alongside `globs` (`optional is not valid with
  globs`) — zero matches is already a first-class outcome (warning below),
  and glob-matched files exist by construction.

Planner contract (application/apply_recipe.py):

- Per target, a glob-bearing step expands before dispatch: each pattern is
  matched within the target tree with pathlib glob semantics (`**`
  supported); candidates are regular files only (directories, symlinks, and
  other non-regular entries are skipped); results across patterns are
  deduped and sorted by relative POSIX path; `exclude` entries are glob
  patterns matched against the relative POSIX path (full-path match; a
  literal relative path excludes itself). Every matched path then flows
  through the existing single-file handlers, including their `confined_path`
  checks — glob expansion grants no confinement exemption.
- NO default exclusions (ruling 5): dotfiles and `.git` contents match when
  the pattern says so; docs recommend `exclude: [".git/**"]` for repo
  sweeps.
- A glob-bearing step whose patterns (after exclude) match zero files
  contributes nothing and appends
  `globs matched no files: {", ".join(step.globs)}` to that target's
  `TargetPlan.warnings` (ruling 4 — renders in preview and the outcome
  `warnings` pipe column).
- Binary honesty (ruling 10): a step-file read that fails UTF-8 decode
  during planning becomes a per-target plan error (not a traceback):
  `file is not valid UTF-8: {relative} (binary files are unsupported; for
  globs, exclude: skips it)`. Pin the wrap wherever planning reads step
  files, so literal-file steps get the same honest error — the glob path is
  merely how users will actually hit it.

Test intent: `**` expansion + dedup across overlapping patterns + sorted
order; exclude filtering (pattern and literal-path forms); zero-match
warning present in `TargetPlan.warnings` and visible via the outcome
`warnings` column; `.git`-internal files match without exclude (rope is
intentional — locked ruling); mutual-exclusion and exclude/optional
validation messages; glob-matched binary file → target error row naming the
relative path; remove-via-glob and transform-via-glob both plan identically
to their literal-file equivalents on the same matched set.

## Task C — preview tiered collapse + `preview_max_rows`

Preview scaling (ruling 7; BACKLOG promotion). Table mode only — `diff` and
`none` are untouched.

Contract:

- `RecipeSettings` (settings.py) gains `preview_max_rows: int =
  Field(default=50, ge=0)`; `0` means unlimited. Threaded from settings into
  `render_preview` at the apply call site (cli/commands.py) as an explicit
  parameter — cli/preview.py stays settings-unaware.
- Tier 1 (rows ≤ threshold, or threshold 0): today's per-file table,
  byte-exact.
- Tier 2 (per-file rows exceed threshold): one row per changing target —
  columns `target`, `files` (count), `changes` (aggregate `+a -d` summed
  over the target's FileChanges via the existing counting helper).
- Tier 3 (per-target rows also exceed threshold): first N target rows plus a
  stderr line `showing first {N} of {M} targets (use --preview diff for full
  detail)`.
- Truthfulness ruling (recorded here for invariant #0's history): a preview
  is truthful when totals are exact and everything hidden is counted and
  reachable — `preview_summary` already prints exact totals and stays.
- Confirm adjacency: `apply` passes a `preview=` callable to `batch_apply`
  that re-echoes `preview_summary(context.plans)` to stderr — in
  `batch_apply`'s TTY destructive path that renders immediately before the
  `Continue?` confirm, so the decision-bearing totals sit next to the
  prompt regardless of how much preview scrolled by. (`remove`'s
  batch_apply usage is Task D's concern; this callable is apply-only.)
- Sensitive-target and error sub-tables render unchanged in every tier.

Test intent: boundary populations at, one-below, and one-above the threshold
for both tier transitions (0.12 lesson: boundaries, not just mixtures);
`preview_max_rows: 0` renders tier 1 at any size; tier-2 aggregate counts
equal the sum of tier-1 rows for the same plans; truncation line exact text;
summary line appears adjacent to the confirm (assert via UI double ordering);
diff mode byte-identical before/after this task.

## Task D — `remove` preview local-edits warning

Parked item 1 (nearly free since the 0.13 hash guard).

Contract:

- `remove_command` (cli/commands.py) computes `edited =
  library.local_edits(name)` (add a passthrough on the library facade if the
  one `remove` holds doesn't expose it — record as deviation if so) and
  passes a `preview=` callable to its existing `batch_apply` call that
  reproduces the current generic preview lines byte-exact
  (`About to remove {total} pack(s):` + per-row `  - {name}`) and, when
  `edited`, appends
  `Warning: pack '{name}' has local edits in the library (via edit or new
  recipe/hook); removing discards them.`
- Legacy rows (no recorded hash) and clean copies warn nothing (that is
  `local_edits`'s existing contract). `--yes` skips the preview entirely
  (unchanged `batch_apply` behavior).

Test intent: edited copy → warning on stderr before the confirm; clean copy
and legacy row → generic preview only, byte-identical to today; `--yes` path
unchanged.

## Task E — `check` builtin-awareness

Parked item 2: `show`/`list --hooks` learned builtins in 0.13; `check` is
the last command blind to them.

Contract:

- `check_ref` (application/check_pack.py): when the library recipe lookup
  raises `recipe not found: ...` and `"/" not in ref_text and ref_text in
  BUILTIN_HOOKS` (the same guard `commands._resolve_target` uses), return a
  pass row in the existing column set:
  `{"recipe": ref_text, "status": "pass", "path": <builtin module file>,
  "error": ""}`. Builtins are engine-owned and definitionally valid — no
  probing.
- Precedence: the fallback fires only after pack and recipe lookups miss, so
  a library pack or recipe named like a builtin shadows it (mirrors
  HookResolver precedence).
- Library-wide `check` (no-arg) does NOT enumerate builtins — they are not
  library content.
- Any other miss keeps today's exact error path (including 0.13's hints).

Test intent: `check yaml_edit` → pass row with the builtin module path;
unknown bare name → today's `recipe not found` error unchanged; library-wide
check output gains no builtin rows.

## Task F — lock-verify honesty

Parked item 3: `check_lock` currently reports EVERY `uv lock --check`
failure as "lockfile is stale" — a network/resolution failure (corporate
mirror down) masquerades as staleness and sends the user to run `uv lock`
pointlessly.

Contract:

- `check_lock` (infrastructure/uv_project.py) branches on uv's stale
  signature: nonzero exit AND the drained detail (stderr, else stdout)
  contains `needs to be updated` (case-insensitive) → today's exact stale
  message. Any other nonzero →
  `could not verify lockfile freshness in {project_root}` with uv's detail
  appended after `: ` when non-empty. The `FileNotFoundError` (no uv) branch
  is unchanged.
- Still raises `ValueError` either way — check's contract is to verify;
  unverifiable = not verified (ruling 6). `_LockFreshness` memoization is
  untouched (it stores message strings, not classifications).
- The hook-worker stale re-headline from 0.13
  (`_is_stale_lock_failure` in hook_worker_client.py) is a different
  signature path on worker stderr — untouched.

Test intent: the existing stale-signature test stays passing byte-exact;
nonzero exit with network-ish stderr (no signature) → the new headline,
which does NOT contain "stale"; empty-detail nonzero → new headline without
trailing `: `; memoization test unchanged.

## Task G — hookless lock alignment

Parked item 4: freshness probing is hook-gated (0.13), but lock-EXISTENCE
checks still fire for hookless projects — a pure copy/template pack can
never legitimately need `uv.lock` (it spawns no worker).

Contract:

- `_check_pack` (application/check_pack.py): the
  `pack project is missing uv.lock` existence check moves inside the
  existing `if pack.manifest.hooks:` gate (existence first, then
  `locks.check`).
- `_check_project_lock` is deleted: its unconditional existence check on the
  recipe's local hook project is subsumed by `_check_local_hook_project`,
  which is already hook-gated and already checks existence before freshness.
- Net behavior: hookless packs/projects need no `uv.lock` at all;
  hook-declaring ones keep today's exact existence + freshness errors.
  Existing tests that assert missing-lock errors on hookless fixtures are
  updated to hook-declaring fixtures or dropped where the scenario no longer
  exists — never weakened for hook-declaring cases.

Test intent: hookless pack without `uv.lock` passes check (pack ref and
library-wide); hookless explicit-path recipe project without a lock passes;
hook-declaring pack without a lock keeps
`pack project is missing uv.lock: {root}` byte-exact.

## Task H — 0.10 harness nits

Three deferred nits from the 0.10 close-out (ruling 9).

Contract:

- `CaseSpec` (domain/testcase.py) forbids `verdict` together with
  `expect: error`: validation message
  `verdict is not valid with expect: error`. Rationale for the docstring:
  whether validate verdicts exist under an expected error depends on where
  planning fails relative to validate steps — a fragile contract, so the
  combination is rejected at case load. `run_case`
  (application/harness.py) drops its expect-error verdict evaluation
  accordingly (unreachable after the schema guard).
- Explicit-path existence guard: a nonexistent explicit recipe path yields
  one clear error naming the path, raised before deeper resolution errors,
  shared by apply/check/test (pin the guard in
  `resolve_explicit_recipe`). If the message text changes from today's,
  `_library_ref_hint`'s prefix guard (resolution.py) is updated in the same
  commit so the 0.13 did-you-mean hint keeps firing.
- The fixtures-never-written harness test extends its assertion from
  `given/` to the entire case directory (tree snapshot before/after run
  compares equal).

Test intent: case.yml with verdict + expect:error → validation error with
the pinned message; missing `./nope.yml` via apply, check, AND test → the
guard message naming the path; the 0.13 library-ref hint still appends when
a matching library name exists; whole-case-dir immutability test.

## Task I — version 0.14.0 + docs

- Version literals: pyproject.toml + `_version.py` + `uv.lock` (relock) +
  `tests/test_hook_api_contract.py` `verify_versions("0.14.0")` (both
  call sites) + the `tests/test_infrastructure.py` wheel glob.
  `HOOK_API_VERSION` stays `0.9.0`. Recipe schema stays `version: 1`.
- README + packaged SKILL.md (src/untaped_recipe/skills/untaped-recipe/):
  `if_absent`; `globs`/`exclude` with the `.git` rope note
  (recommend `exclude: [".git/**"]` for repo sweeps), the zero-match
  warning, and the binary limitation (+ exclude escape hatch);
  `preview_max_rows` + the tiered preview and the truthfulness note;
  `check` builtin awareness, hookless-lock relaxation, and the
  could-not-verify lock message; `remove` local-edits warning.
- Release notes block in the PR body: new schema keys (additive), check
  builtin rows (new rows, same columns), hookless packs no longer need
  uv.lock, preview collapses above `preview_max_rows`.

## Self-review gates (before opening the PR)

**Field-walk** (every pinned field/flag to its producing type):
`if_absent` → TemplateStep + CopyStep → `_plan_template`/`_plan_copy` guards;
`globs`/`exclude` → TransformStep + RemoveStep model fields → fan-out
validator passthrough → planner expansion; zero-match warning →
`TargetPlan.warnings`; binary error → the planner's step-file read wrap;
`preview_max_rows` → RecipeSettings → apply call site → `render_preview`
parameter; confirm-adjacent summary → apply's `batch_apply(preview=...)`;
remove warning → `remove_command`'s `batch_apply(preview=...)` +
`local_edits` accessor; builtin pass row → `check_ref` fallback branch;
could-not-verify headline → `check_lock` signature branch; hookless
relaxation → `_check_pack` gate move + `_check_project_lock` deletion;
verdict/expect guard → CaseSpec validator; path guard →
`resolve_explicit_recipe` + `_library_ref_hint` prefix.

**Decision-walk** (every locked ruling maps to a task or an explicit
deferral):

| Locked decision | Where |
|---|---|
| Ensure scope: only-if-absent + globs (ruling 1) | Tasks A + B |
| `state: dir` | DEFERRED — BACKLOG (homebase), trigger = first recipe needing an empty dir |
| `if_absent` flag shape (ruling 2) | Task A |
| Explicit `globs:` key, exactly-one-of (ruling 3) | Task B |
| Empty match = no-op + warning (ruling 4) | Task B |
| No default exclusions + `exclude:` key (ruling 5) | Task B |
| Lock-verify honesty (ruling 6) | Task F |
| Preview tiered collapse + `preview_max_rows` + truthfulness ruling (ruling 7) | Task C |
| Parked: remove local-edits warn / check builtins / network-vs-stale / hookless locks (ruling 8) | Tasks D / E / F / G |
| 0.10 harness nits (ruling 9) | Task H |
| Binary = honest error now, byte-mode deferred (ruling 10) | Task B + BACKLOG (homebase) |
| Real-pack exercise right after 0.14 (ruling 11) | ROADMAP (homebase) — adjudicates tiered execution / git-aware backups / input-from rows |
| 0.9 spec: ensure resolved at planning time, never execution-time convergence | Tasks A + B (planner-only; no new execution paths) |
| Second-review surviving items | BACKLOG (homebase) rows with sources + triggers; none in this wave |
