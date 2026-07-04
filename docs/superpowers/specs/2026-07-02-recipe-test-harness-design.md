# Recipe Test Harness — Design

Date: 2026-07-02 (amended 2026-07-03 while writing the implementation plan against
the landed 0.9.0 code; amendments are marked inline and listed under §Amendments)
Status: approved (brainstormed and locked with Alexis)
Target release: 0.10.0 (the pack-unification spec's Wave 2, expanded here into a full
design).
Sequencing: the implementation plan is written AFTER the 0.9.0 pack-unification wave
lands — that wave reshapes the library, CLI, and planner modules this harness builds
on, so a plan written earlier would name files that no longer exist. This spec locks
the design so that plan has a stable target.

## Context

Recipes and packs have no way to pin their own behavior. A pack author edits a hook
or a template and finds out whether existing recipes still produce the intended
changes by running `apply` against real targets and eyeballing the preview. Hooks are
unit-testable with pytest (pure functions), but recipe-level behavior — input
resolution, template rendering, step ordering, hook wiring, the `unknown_tokens`
policy — has no harness at all.

The harness ships as 0.10.0, after the 0.9.0 pack unification, and builds directly on
its pack model: cases live inside packs, `test` resolves refs through the same
`PackLibrary`, and planning is the only execution (invariant 2).

## Decision: golden-fixture cases, plan-only

A test case is a directory compared by content. No assertion language exists or will
exist (the anti-DSL guard from the pack-unification spec, restated here as a design
invariant): `case.yml` carries data only — inputs, targets, and expected outcomes.
Logic in tests is pytest's job, at the hook level.

### Case layout

```text
tests/<recipe-name>/<case-name>/
├── given/          # fixture target tree: targets plus any support files hooks read
├── expected/       # full expected tree after the plan (omit = assert no changes)
└── case.yml        # optional
```

`tests/` sits at the pack root, beside the manifest. Case discovery is by directory
convention — `tests/<recipe-name>/` must match a recipe name in the pack manifest; a
tests directory naming no known recipe is a `check` failure (wired into the pack
check machinery so `check` catches orphaned tests after a recipe rename).

### `case.yml` schema

Every field optional; an absent `case.yml` means: no inputs, expect success,
comparison driven by `expected/` presence as usual.

```yaml
inputs:                      # recipe inputs, same names/types apply accepts
  owner: "platform-team"
expect: success              # success (default) | error
error_contains: "..."        # required iff expect: error; forbidden otherwise
verdict:                     # validate expectations (optional)
  status: warn               # expected worst-of status across the case: pass | warn | fail
  message_contains: "..."    # substring that must appear in at least one verdict message
```

- **`given/` is the single target directory** (amended 2026-07-03; the original
  draft had a `targets:` list of file paths, which contradicts the engine: an apply
  target is a *directory*, and which files change is the recipe's decision — its
  steps — not the case's). The harness plans against a temporary copy of `given/`
  named after the case (so `from:` expressions over `target.name` see the case
  name). Support files hooks read are simply files in `given/` the recipe does not
  touch; full-tree comparison still proves they were left alone.
- `expect: error` cases pass iff planning raises and the error message contains
  `error_contains`. Requiring the substring is deliberate: a bare "it failed"
  assertion silently keeps passing when the failure changes cause (a typo'd fixture
  path fails too). `expected/` is forbidden in error cases.
- `verdict` asserts against the verdicts the plan produced (`Verdict.status`,
  `Verdict.message`): `status` is the expected worst-of across all verdicts in the
  case, `message_contains` a single substring match. When `verdict` is omitted,
  verdicts influence the case only through their normal effect on planning.
  A `verdict` block on a case whose plan produced *no* verdicts fails the case
  ("no verdicts produced") rather than passing vacuously (amended 2026-07-03).

## Execution semantics

- **Plan-only, real planner.** `test` runs the exact planner `apply` uses — hooks
  execute through the normal uv worker with the normal resolution order (the pack's
  own hooks → library packs → builtins). No parallel "test planner" exists to drift.
- **Full-tree comparison.** The plan is materialized in memory over a copy of
  `given/` and the ENTIRE result tree is compared byte-for-byte against `expected/`
  — extra files, missing files, and content differences all fail the case. Full-tree
  compare is the harness's core guarantee: it catches unintended changes to files
  the recipe should not touch, which touched-files-only comparison silently misses.
- **Omitted `expected/` = no changes planned.** The natural shape for validate-only
  recipes (pair it with `verdict`) and for guard cases proving a recipe leaves
  already-conformant trees alone.
- **Nothing is written to disk.** Fixtures are never mutated; the working tree is
  never touched. The only write path is `--update` (below), which rewrites
  `expected/` and nothing else.
- **One golden format.** The pack-unification sketch allowed an `expected.diff`
  alternative; this design drops it. One comparison path, one `--update` path, no
  diff-context brittleness. Mismatches are still *rendered* as diffs — they just are
  not *stored* as diffs.

## CLI

`test [pack|path|pack/recipe]` mirrors `check`'s grammar:

| Invocation | Scope |
|---|---|
| `test` | every installed library pack's cases (post-install verification for free — `tests/` ships inside the byte-identical installed copy) |
| `test .` / `test <path>` | the pack at a filesystem path (the development loop) |
| `test <pack>` | one library pack |
| `test <pack>/<recipe>` | one recipe's cases |

- `--update` regenerates `expected/` from the current plan (creating it when absent,
  deleting it when the plan is empty) and REQUIRES an explicit pack or recipe
  argument — there is no library-wide golden regeneration in one keystroke.
  `--update` on an `expect: error` case is an error. Cases whose golden already
  matches are reported `pass` and not rewritten; rewritten cases are reported
  `updated`; exit code fails only on `error` rows (amended 2026-07-03).
- **Output:** one `recipe.test` record per case on stdout — pack, recipe, case,
  status (`pass` | `fail` | `error`), and a short mismatch summary — plus a stderr
  summary line through `UiContext`. Failed comparisons render a unified diff per
  mismatched file on stderr, reusing the existing diff helper (`infrastructure/
  diff.py`, or its SDK successor if the recipe re-pin lands first — whichever exists
  when the plan is written).
- **Exit codes:** 0 when every discovered case passes, 1 otherwise (including "no
  cases found" for an explicitly named recipe *or pack* — naming a target with no
  tests is a failure, not a silent pass; the bare library-wide `test` reports packs
  without tests on stderr but does not fail on them). (Pack case amended
  2026-07-03: the original text covered only recipes.)
- `test` also emits one error row per orphaned `tests/<name>/` directory in the
  selected pack(s) — `check` remains the canonical guard, but `test` silently
  skipping cases that exist on disk would misreport coverage (amended 2026-07-03).
- `recipe.test` joins the kept emit kinds from the pack-unification spec's
  consolidation table. Row fields: `pack`, `recipe`, `case`, `status`, `detail`
  (short mismatch summary; empty on pass).

## Scaffolding

- `new recipe` additionally scaffolds `tests/<recipe>/basic/` with an empty `given/`
  directory and a fully commented `case.yml` documenting every field — the case is
  inert until fixtures exist, but the shape is discoverable exactly where it is
  needed.
- `new hook` keeps its direct-pytest scaffold from the pack-unification spec (hooks
  are pure functions; no worker, no harness needed at unit level).

## Non-goals

- Real-write (apply-through) testing — planning is the only execution; the flush
  path is the transactional writer's concern and is tested in its own unit tests.
- Any assertion language, matcher syntax, or templating inside `case.yml`.
- Snapshot-testing emit/stdout output (the pipe envelope is frozen and covered by
  the SDK's own tests).
- Parallel case execution — revisit only if real suites get slow.
- Watch mode / re-run-on-change.
- `expected.diff` goldens (dropped above).

## Versioning

0.10.0 (MINOR on top of 0.9.0): `test` verb, `recipe.test` emit kind, `check`
gaining the orphaned-tests rule, and the `new recipe` scaffold addition are all
additive. No manifest, recipe-schema, or library format changes.

## Amendments (2026-07-03, while writing the implementation plan)

Made against the landed 0.9.0 code (`c18c9a7`); each is marked inline above.

1. **`targets:` dropped from `case.yml`.** The draft field listed *file* paths with
   "default = every file in `given/`", but an apply target is a directory and the
   recipe's steps — not the case — decide which files change. `given/` is the single
   target directory; the harness plans against a temp copy named after the case.
2. **Empty `verdict` evidence fails.** `verdict:` with zero produced verdicts fails
   the case instead of passing vacuously.
3. **Orphaned `tests/` dirs also surface in `test` output** as error rows (`check`
   stays the canonical guard).
4. **Explicitly named pack with no cases fails**, same as an explicitly named recipe.
5. **`--update` reports `updated` vs `pass`** (already-matching goldens are not
   rewritten) and fails only on `error` rows.
6. **`recipe.test` row fields locked:** `pack`, `recipe`, `case`, `status`, `detail`.
