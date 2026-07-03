# Pack Unification — Design

Date: 2026-07-01
Status: approved (brainstormed and locked with Alexis); amended 2026-07-03 after a
second vision + correctness review with Alexis (domain lock, SDK 3.0 re-pin at the
front of the wave, gap rulings, deferred-design models).
Target release: 0.9.0 (wave 1), 0.10.0 (wave 2)
Sequencing: implementation starts only after PR #17 merges and untaped-recipe 0.8.0
publishes to PyPI, so the release pipeline is proven before this breaking wave.
As of the amendment both preconditions are met (0.8.0 and 0.8.1 shipped); the wave
now begins by re-pinning to `untaped>=3.0.0,<4` (plan Task 0.5) so every line of new
code is written against the SDK 3.0 surface instead of being migrated later.

## Context

untaped-recipe currently has three shareable/installable shapes — standalone recipe
projects, packs, and hook projects — each with its own library module
(`recipe_library.py`, `pack_library.py`, `hook_library.py`, ~900 LoC combined), its own
CLI namespace, and its own docs. The shapes are almost identical: a directory with a
`pyproject.toml` carrying `[tool.untaped_recipe]` metadata. The triplication makes the
tool heavier to learn, the sharing story fragmented, and the CLI surface 32 leaf
subcommands.

Separately, a hook's kind (`transform` | `validate`) is declared twice — in the hook
project manifest and in every recipe step that uses it — with a cross-check
(`ensure_hook_kind`). The manifest copy is derived metadata: the worker already
dispatches by calling the `transform()` or `validate()` function by name.

This redesign lands while the project has a single user, so breaking changes are cheap.

## Domain (invariant #0)

**untaped-recipe is a deterministic transformation engine over file trees — "moderne
for files, driven by hooks."** A recipe's scope is anything expressible as planned
file edits: version migrations, bulk config rewrites, scaffolding, drift checks. The
comparison that holds is OpenRewrite/moderne (transformation recipes, previewable at
scale), not Ansible (general task execution): "you could write a recipe for anything"
is bounded to *anything that is a file transformation*. This sentence wins every
future scope argument and joins AGENTS.md alongside the Wave-3 invariants.

Two consequences are load-bearing:

- **Truthful preview is the product.** Plan → preview → confirm → flush is only
  trustworthy while every step is a planned file mutation. Agent-authored packs are a
  first-class north-star use case precisely because the human reviews the plan, not
  the agent; anything that executed at apply time outside the plan buffer would make
  the preview lie.
- **Follow-up commands are data.** Real migrations often end with "now run `uv lock`"
  or "re-run the formatter". Recipes will be able to *declare* follow-ups; preview
  and outcome *display* them; recipe never executes them (model locked, design
  deferred — see Deferred designs).

**Never build** (recorded with reasons so future scope arguments end here):

- Exec/shell/API step types — they kill truthful preview; this is the boundary
  decision itself. "Ensure"-style capabilities enter as *planned* mutations resolved
  at planning time (see Deferred designs), never as execution-time convergence.
- Control flow in the recipe schema (`when:`, loops) — a decision is a hook.
- State, inventory, or remote execution — targets come from arguments and pipes.
- Hook sandboxing — packs are trusted code, on the same model as `pip install`; the
  mitigations are evaluate-before-trust surfaces (`show`, `check`'s AST scan, the
  `add` confirmation, the 0.10 harness), not a sandbox.

## Decision: everything is a pack

A **pack** is a directory whose `pyproject.toml` declares `[tool.untaped_recipe]` with
an optional `recipes` table and an optional `hooks` table. Today's three shapes become
degenerate cases: a pack of many recipes, a pack of one recipe, a pack of zero recipes
(hooks only). Single YAML recipe files remain runnable ad hoc
(`untaped-recipe apply ./recipe.yml`) but are not a library or sharing concept.

### Pack identity

Pack name = `[project].name` with the conventional `untaped-recipe-` prefix stripped
(project `untaped-recipe-ansible` → pack `ansible`). No separate name field, no
directory-name coupling. The prefix convention makes packs searchable on GitHub later.

### Manifest shape

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = ["untaped-recipe>=0.9"]

[tool.untaped_recipe]
requires_hook_api = ">=0.9,<1"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

- Both tables are optional and explicit. No `recipes/*/recipe.yml` auto-discovery:
  public identity comes from the manifest, and scaffolding maintains the entries, so
  the explicitness costs nothing day to day.
- Hook entries have no `kind` field (see Hook contract below).
- `requires_hook_api` gains an upper bound (`<1`) so a future breaking hook-API major
  fails loudly instead of silently passing old floors.

## Hook contract

- The exported function name is the contract: a hook module exports `transform()`,
  `validate()`, or **both**. The recipe step's `type` selects which function runs.
  Recipe-side `type` stays exactly as today — it drives the discriminated step schema
  (`transform` requires `file`; `validate` forbids it) and tells the recipe reader
  which steps mutate.
- Dual-verb hooks are first-class: validate/fix pairs share parsing logic in one
  module under one public name.
- `check` keeps its no-import guarantee: instead of comparing a declared kind, it AST-
  scans the resolved module file for `def transform` / `def validate` and rejects a
  step wired to a hook that does not export the required function. (Accepted
  limitation: dynamically defined functions are invisible to the scan; scaffolded
  hooks are flat functions and the worker fails loudly at call time.)
- `hook run` verb selection: if the module exports one function, run it; if both,
  `--file` implies transform, otherwise `--kind` is required. Error messages state
  this rule.
- Worker protocol, helpers, `--locked --no-dev` execution, and the pure-JSON boundary
  are unchanged. `HOOK_API_VERSION` bumps to `0.9.0` with this wave.

## Library

One `PackLibrary` replaces the three library modules.

- Layout: `<library_root>/packs/<pack-name>/` (installed copy of the pack directory).
- Install-source tracking lives in a library-level index, `<library_root>/packs.toml`
  (pack name → source path or git URL + rev + the pack's `[project].version` at add
  time), never in sidecar files inside pack directories — installed packs stay
  byte-identical to their source so re-sharing an edited pack never leaks library
  bookkeeping. The index is bookkeeping for a future `update` command; `update` itself
  is out of scope for 0.9.0.
- Library packs remain editable in place (parity with today's `hook edit`).
- `add --name <n>` overrides the library key (the collision escape). The index key is
  the pack's identity everywhere — refs, `list`, `check`, `remove`; `show` displays
  both the installed name and the manifest's `[project].name` when they differ.
- `packs.toml` is written without a cross-process lock; concurrent `add`/`remove` can
  race. Accepted for the sole-user reality — recorded as a decision, not an
  oversight.

## Resolution

For `hook: set_owner` referenced by a recipe:

1. the recipe's own pack (`[tool.untaped_recipe.hooks]` in the same manifest),
2. library packs,
3. builtins (`yaml_edit` stays the only builtin; builtins remain in-process).

Builtins stay minimal by invariant: structured-editing hooks (TOML, JSON, INI, …)
belong in packs, which can carry real dependencies via uv — `yaml_edit` remains the
lone, historical builtin. The eventual home for common editors is a curated "std"
pack, not core growth.

Recipe references in `apply` resolve against the library (bare name when unique,
`pack/name` otherwise) or an **explicit** filesystem path. A path must be marked as
one: it starts with `./`, `../`, `/`, or `~`, or ends in `.yml`/`.yaml`. Anything
else — including `a/b` — is always a library ref, never sniffed against the working
directory, so `apply ansible/playbook-migration` cannot change meaning because a
local `ansible/` directory happens to exist.

- Bare names that match more than one library pack are **errors**, listing the
  qualified candidates. Never first-match.
- Qualified syntax: `pack/name` (e.g. `ansible/add_play_collections`,
  `apply ansible/playbook-migration ./target`).

## CLI surface

The `pack` and `recipe` management namespaces disappear. Top-level verbs operate on
packs because packs are the only unit:

| Verb | Behavior |
|---|---|
| `new pack <name>` | scaffold a pack |
| `new recipe <pack>/<name>` | scaffold a recipe inside a pack, updating the manifest |
| `new hook <pack>/<name>` | scaffold a hook inside a pack, updating the manifest |
| `add <path\|git-url> [--rev R] [--name N] [--force]` | install a pack into the library |
| `remove <pack> [--yes]` | uninstall; destructive (library packs are editable in place, removal discards edits): interactive confirm, `--yes` skips, piped stdin refused without `--yes` |
| `list [--hooks\|--packs]` | recipes by default (with source pack); hook and pack views |
| `show <ref>` | structured pack, recipe, or hook detail (see below) |
| `check [pack\|path]` | validate manifest, recipes, hook wiring (AST scan); no args = whole library |
| `edit <ref>` | open the relevant file |
| `hook run <ref> ...` | unchanged debugging verb (verb-selection rule above) |
| `apply <recipe-ref\|path> ...` | apply semantics unchanged; ref-vs-path classification per Resolution (explicit paths only) |
| `backup ...` | `restore` gains apply-parity confirmation (below); `list`/`show` unchanged |

`new <kind>` is the single creation pattern, reusing qualified names. Personal one-off
hooks get no special case: make a personal pack (`new pack mine`) — it is instantly
shareable like any other.

### Structured `show`

`show` stops dumping raw file text. For a recipe it renders the description, an
inputs table (name, type, required, default, description, sensitive), a step summary,
and the hooks the recipe uses; for a hook, the exported verbs and module path; for a
pack, its recipes and hooks with one-line descriptions.
Together with the `add` confirmation this is how a user evaluates a pack someone else
shared before trusting it. (Recipe declared inputs already carry type, default,
required, description, and sensitive metadata — `show` finally surfaces them.)

### Library doctor

`check` with no arguments validates the whole library: every installed pack passes the
normal pack checks, and `packs.toml` agrees with `packs/` (an index entry whose
directory is missing, or a pack directory absent from the index, is reported). This
reuses the per-pack check machinery; it exists because manual edits under the library
root are supported (`edit`), so drift is possible.

### Structured output kinds

Unification also consolidates the emit record kinds (a breaking change for pipe
consumers, shipped with 0.9):

- Kept: `recipe.outcome`, `recipe.backup`, `recipe.hook_run`, `recipe.recipe`,
  `recipe.hook`, `recipe.pack`, `recipe.check`.
- Deleted: `recipe.pack_check` and `recipe.pack_recipe` (their commands collapse into
  `check` and `list`/`show`).

### Backup restore parity

`backup restore` is destructive (it overwrites working-tree files) yet today runs
with no confirmation, preview, or progress, while `apply` gets all three from the
SDK's `batch_apply(destructive=True)`. Restore routes through the same helper: it
previews the files it will overwrite, asks for confirmation (`--yes` to skip),
reports progress, and exits through the standard outcome path. `--force` keeps its
existing, separate meaning — bypass the changed-since-backup hash guard — and does
not imply `--yes`.

### Sharing

The unit of sharing is the pack directory. `add ./path` copies; `add <git-url>`
fetches to a temp dir and runs the same install path (one code path, two front
doors), recording source + rev in `packs.toml`. Before installing, `add` prints the
recipes and hooks the pack brings and asks for confirmation (`--yes` to skip):
installing a pack installs trusted code, and that should be visible.

Trust stance, explicitly: installing a pack is installing code, on the same trust
model as `pip install` — there is deliberately no sandbox (see Never build). The
mitigations are evaluate-before-trust surfaces: the `add` confirmation listing what
comes in, structured `show`, `check`'s no-import AST scan, and (0.10) the test
harness. A user who `add`s a pack is trusting its author; the tool's job is to make
what they are trusting visible, not to pretend the code is contained.

## Wave 2 (0.10.0): recipe test harness

Expanded into a full design at
`docs/superpowers/specs/2026-07-02-recipe-test-harness-design.md`, which supersedes
this sketch where they differ (notably: the `expected.diff` golden variant is
dropped — `expected/` trees are the only format). The sketch below is kept for the
original context.

`untaped-recipe test [pack|recipe-ref]` runs golden-fixture cases that live inside
the pack:

```text
tests/<recipe-name>/<case-name>/
├── given/          # fixture target tree
├── expected/       # expected tree after apply (or expected.diff)
└── case.yml        # optional: inputs, expected verdict status/warnings
```

- Execution is **plan-only**: the planner runs against `given/`, planned changes are
  compared against `expected/` (or `expected.diff`); nothing is written.
- `--update` regenerates goldens from the current plan.
- Anti-DSL guard: cases are directories compared by content. `case.yml` carries only
  inputs and expected verdict status — no assertion language, ever. Logic in tests is
  pytest's job.
- `new hook` scaffolds a pytest that calls `transform()` directly (hooks are pure
  functions; no worker needed in unit tests).

## Wave 3: hygiene and invariants (rides along anywhere)

- Template steps gain `unknown_tokens: error | keep` (default `error`). Today unknown
  *bare* input names already fail, but non-bare `{{ ... }}` tokens (`{{ a.b }}`,
  filters) silently pass through — a trap for users expecting Jinja, yet load-bearing
  for templates that emit GitHub Actions (`${{ github.ref }}`) or Helm
  (`{{ .Values.x }}`) files. Under `error`, any `{{ ... }}` token that is not a bare
  known input name fails planning, and the error names the offending token and points
  at `unknown_tokens: keep`. Under `keep`, unknown tokens pass through verbatim while
  known inputs still render. Constraint: the renderer exists in two copies
  (`domain/templates.py` and the stdlib-only `hook_worker.py`); the change lands in
  both, pinned by a parity test.
- AGENTS.md gains permanent invariants:
  1. Control flow never enters the recipe schema; a decision is a hook.
  2. Planning is the only execution; writes are a flush of the plan buffer.
  3. No state, no inventory; targets come from arguments and pipes.
  4. Builtins stay minimal; typed uv hook packs are the extension story.
  5. The hook boundary stays pure data (stdlib-only worker, JSON protocol, no runtime
     import of untaped-recipe in hooks).
  6. Pipe composability is a protected feature: `apply --stdin` ingests untaped
     NDJSON envelopes (kind tags, `target_path`), and input `from` expressions can
     read the piped `record` — other untaped tools' output drives recipes.
  7. Hooks are pure at planning time: they may read the target tree and their own
     pack directory (deterministic inputs), and must never write, reach the network,
     or read outside those roots. Planning's truth depends on it; the 0.10 harness
     catches violations as unstable goldens.
  (AGENTS.md also carries invariant #0, the domain lock from §Domain.)
- The recipe file schema stays `version: 1` in the 0.9 wave — the breaking changes
  are in manifests, the library, and the CLI, not in recipe.yml.
- Hook-executor port collapse: `HookExecutorPort` and `HookDebugExecutorPort` are
  parallel protocols over one implementation with doubled `*_for_debug` methods; they
  merge into a single protocol with a `capture_diagnostics` flag.
- Output plumbing goes through `UiContext` so the SDK root flags actually apply:
  the preview summary and hook diagnostics stop using raw `echo`/`sys.stderr` (they
  currently ignore `--quiet`), the hand-built themed table in preview switches to
  `render_rows`, the planning phase gains progress reporting (a callback parameter on
  the bulk runner, threaded from the CLI — the SDK stays out of the application
  layer, same pattern as `PromptFunc`), and stdin target reading reuses the SDK's
  `read_stdin`.
- Dead code deleted: the never-varied `template_renderer` injection parameter, the
  never-read `Target.kind`/`Target.lineno` fields, and the transform-file validation
  duplicated between planning and the debug runner.

## Migration

Sole-user migration, no compat shims, no migration command:

- Existing library `hooks/<name>/` projects and pack/recipe entries move under
  `packs/`; hook manifests drop `kind`; `requires_hook_api` floors bump to
  `>=0.9,<1`; scaffolded dev-dep floors bump to `untaped-recipe>=0.9`.
- A short migration note in the changelog covers the manual steps.

## Deferred designs (first post-0.9 minors; models locked 2026-07-03)

- **Ensure semantics.** The plan layer already models create/remove/modify
  (`FileChange.before/after`), and the schema already has `template`, `copy`, and
  `remove` steps plus `files:` fan-out — the capability largely exists. The remaining
  gap is flags, not a subsystem: an `ensure`-style step (or flags on existing steps)
  covering `state: dir`, only-if-absent creation, and possibly glob patterns in
  `files:`. Resolved at planning time against the target tree — declarative like an
  Ansible module to read, but planned and previewed, never converged at execution
  time.
- **Follow-up declarations.** A recipe-level list of suggested follow-up commands
  (pure data: command string + reason), rendered in preview and outcome. Never
  executed by recipe.
- **Recipe composition** (declarative include-lists, à la moderne's migration
  recipes) is *expected eventually, not yet* — upgraded from the original brainstorm
  rejection. Trigger: the first real pack where one recipe's step list duplicates
  another's. Pre-locked constraint for that future design: include-lists only — no
  conditions, no parameters beyond input pass-through.

## Deferred: SDK extraction candidates

Not part of 0.9. This list seeds a future untaped-SDK redesign spec, which will pair
it with a fresh sweep of the other tools for bubble-up candidates before deciding the
SDK surface. Recorded here so the audit findings survive until that brainstorm.

- **Move**: the transactional file writer (`infrastructure/file_writer.py` — staged
  temp files, ordered `os.replace`, reverse rollback). Near-zero coupling; any future
  write-capable tool wants exactly this. Move it only after the 0.8.1
  encoding/newline hardening lands, so the SDK inherits the corrected version.
- **Move (small)**: the unified-diff helper (`infrastructure/diff.py`) plus the
  `+N -M` line-stat counting duplicated in preview.
- **Extract later**: the backup bundle store (`infrastructure/backup.py`) — generic
  in spirit, but its on-disk metadata is recipe-shaped; generalize the entry model
  first. Pairs naturally with the file writer.
- **Extract later**: the sandboxed Jinja evaluator core (`infrastructure/
  input_jinja.py` — SandboxedEnvironment, AST allowlist, blow-up guards) as a
  safe-eval primitive; the `{target, record}` allowlist and scalar contract stay
  recipe-side.
- **Trivial**: `load_yaml_mapping_file` (`cli/common.py`) — every tool with a
  `--vars file.yml` flag reimplements it.
- **Keep**: input resolution (`application/inputs.py`) — the pattern is common but
  the implementation is recipe-domain through and through.

## Non-goals

- PyPI-style pack distribution (revisit only if packs need inter-pack dependencies).
- Standalone recipe projects as a library/sharing form.
- Control flow, conditionals, or loops in the recipe schema (see §Domain — never).
- Exec/shell/API step types and hook sandboxing (see §Domain — never).
- Saved-plan / state / drift semantics (plan output stays ephemeral).
- Pack `update` command (index records enough to add it later).
- Recipe composition — not a permanent non-goal; deferred with a trigger (see
  Deferred designs).
