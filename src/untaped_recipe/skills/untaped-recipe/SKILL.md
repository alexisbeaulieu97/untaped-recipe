---
name: untaped-recipe
description: Use the untaped-recipe CLI to apply local recipe packs.
---

# Untaped Recipe

Use this skill when applying reusable local file recipes across one or more
plain directories.

## Core Commands

- `untaped-recipe apply <recipe> <dir>...` previews and applies a recipe.
- Use `--stdin` to read bare paths or untaped pipe records; do not combine it
  with positional target directories.
- Pass `--yes` for non-interactive applies.
- Use `--dry-run` to preview without writing, `--vars file.yml` or repeated
  `--var KEY=VALUE` for inputs, and `--parallel N` for planning workers.
- Preview output goes to stderr. Normal apply and `--dry-run` default to
  `--preview table`, which renders changed files with absolute paths, change
  kind, and line counts. `--check` defaults to summary-only preview output for
  CI; pass `--preview table` when you want the same file table in check mode.
  Use `--preview diff` for patch-compatible unified diffs, or `--preview none`
  for summary-only preview output.
- Use `--input-from KEY=JINJA` to override a per-target input source and
  `--interactive` to prompt for unresolved inputs. Do not combine
  `--interactive` with `--check`.
- Use `--check` for compliance/drift checks. It previews without writing,
  creates no backups, prompts for nothing, and exits non-zero when changes
  would be made or a target fails.
- Use `--hook-timeout SECONDS` to override the configured hook request timeout;
  `0` disables timeout for trusted long-running hooks. Worker environment
  startup (uv env creation/sync on first use) runs under the separate
  `recipe.hook_startup_timeout_seconds` setting (default 300, `0` =
  unbounded) with a stderr `preparing hook environment...` notice, so a cold
  cache never fires the hook timeout.
- Backups are created by default; use `--no-backup` only when the target tree is already protected.
- `untaped-recipe backup list|show|restore|prune` manages backup bundles;
  `show` and `restore` accept full ids, unambiguous prefixes, or `latest`.
  `restore` applies the whole bundle as one staged transaction. `prune
  [--keep N] [--older-than DAYS]` deletes old bundles behind the standard
  destructive confirmation, falling back to the `recipe.backup_keep` /
  `recipe.backup_max_age_days` settings when flags are omitted.
- `untaped-recipe new pack <name> [--no-lock]` scaffolds a pack.
- `untaped-recipe new recipe <pack>/<recipe> [--no-lock]` scaffolds a recipe and starter
  golden case inside a pack.
- `untaped-recipe new hook <pack>/<hook> [--no-lock]` scaffolds a hook inside a
  pack plus a direct-call pytest at `tests/test_hook_<hook>.py`; new packs ship
  `pytest` in their dev group and pytest `pythonpath = ["src"]`, so
  `uv run --project <pack> pytest` works immediately (packs scaffolded before
  0.13.0 don't gain these automatically).
- `untaped-recipe test [pack|path|pack/recipe]` runs golden-fixture cases
  under `tests/`; use `--update` with an explicit pack or recipe to regenerate
  `expected/`.
- `untaped-recipe add <path|git-url>` installs a pack after previewing its
  recipes and hooks; use `--yes` for non-interactive installs, `--name` for an
  installed-key override, `--rev` for git sources, and `--force` to replace.
  Installs skip dev/build junk (`.git`, `.venv`, `__pycache__`, `dist`,
  caches, egg-info) and record a content hash in `packs.toml`; `--force`
  refuses to overwrite a library copy with local edits (made via `edit` or
  `new recipe`/`new hook`) unless `--discard-edits` is also passed.
- `untaped-recipe list [--packs|--hooks]`, `show <ref>`, `check [ref|path]`,
  `edit <ref>`, and `remove <pack>` operate on the unified pack library.
  `list --hooks` and `show` also cover built-in hooks (`yaml_edit`, marked
  `(builtin)`); built-ins are engine-owned and cannot be edited.
  `check` with no ref validates the whole library and `packs.toml`; for
  hook-declaring projects it also verifies lockfile freshness via
  `uv lock --check`, so stale locks fail at check time, not hook run time.
  `remove <pack>` is destructive and requires confirmation or `--yes`.
- `untaped-recipe hook run <hook-ref> --target DIR` invokes one hook against
  explicit fixture context without running a full recipe or writing target
  files.

## Recipe Model

- Library root defaults to `~/.untaped/untaped-recipes`.
- Installed library items are uv pack projects under
  `<library_root>/packs/<pack-id>/`; source bookkeeping lives in
  `<library_root>/packs.toml`.
- Public pack identity comes from top-level `[project].name`, not from
  `recipe.yml`. Project names may use the `untaped-recipe-` prefix; the public
  pack name drops it.
- The installed library key is the pack identity everywhere: refs, `list`,
  `check`, `remove`, ambiguity messages, and output rows. `add --name <name>`
  overrides that installed key.
- Recipe YAML is behavior-only: `version`, optional `description`, optional
  `inputs`, and `steps`. `name:` is rejected. The recipe file schema remains
  `version: 1` in 0.9.
- Input specs support `type`, `default`, `required`, `description`,
  `sensitive`, `scope`, and `from`; unknown input-spec fields are rejected.
  Omitted `scope` infers `target` when `from` is present and `global`
  otherwise. `scope: global` rejects recipe `from` and CLI `--input-from`, but
  accepts fixed values from `--var` and `--vars`.
- Per-target `from` values are sandboxed strict native Jinja strings used only
  to derive scalar input values. They can combine literal text,
  string/number/boolean/null constants that Jinja parses without operators,
  and field access on `target.path`, `target.name`, `target.parent_path`,
  `target.parent_name`, and optional incoming pipe `record`. They cannot change
  recipe structure, paths, hook names, or template rendering. There are no
  ambient globals; control blocks, filters, tests, calls, operators, and
  collection literals are rejected, so negative numeric expressions like
  `{{ -1 }}` are not valid V1 sources. Missing, undefined, or null candidates
  fall through; `false`, `0`, and `""` are real values. Oversized or non-scalar
  derived values are rejected.
- Input precedence is fixed value/source override, recipe `from`,
  `--interactive` prompt, recipe `default`, then required-input error. A fixed
  value and `--input-from` source override for the same input is a usage error.
  Empty interactive answers accept the default when one exists; sensitive
  defaults are not displayed to the prompt backend.
- `apply foo` resolves an installed pack recipe only when unique.
- `apply pack/recipe` resolves an installed pack recipe from `packs/pack/`.
- `apply ./recipe.yml` runs a path-only single-file recipe.
- `apply ./pack-project --recipe recipe` runs a recipe from a local pack.
- For `apply`, paths must be explicit: they start with `./`, `../`, `/`, or
  `~`, or end in `.yml`/`.yaml`. Bare `a/b` is always a library ref, never an
  on-disk path probe.
- Pack-local hooks are declared in the top-level pack `pyproject.toml`.
- Recipes only name hooks; they do not declare runtimes.
- Hook resolution checks the recipe's own pack, then installed packs, then
  packaged built-ins.
- Hook metadata rows declare only `module`. The exported function name is the
  contract: a module exports `transform()`, `validate()`, or both, and the
  recipe step `type` selects which function runs. `check` AST-scans modules
  without importing them and rejects missing exports.
- `hook run` resolves explicit `--project PATH`, then installed packs, then
  built-ins. An explicit `--project` must point at a pack with hook metadata and
  never falls through to later sources. If a hook exports both functions,
  `--file` implies transform; otherwise pass `--kind`. Transform hooks require
  `--file`; default stdout is exact transformed content with no added newline,
  and `--diff` switches stdout to a unified diff. Validate hooks reject
  file/content options and emit a `recipe.hook_run` verdict record.
- `hook run` accepts `--inputs`/`--args` YAML mapping files plus repeated
  YAML-parsed `--input KEY=VALUE` and `--arg KEY=VALUE` overrides. It prints
  resolved context and hook diagnostics to stderr; SDK `--quiet` suppresses
  context chatter but not hook diagnostics or errors. Context chatter includes
  ad-hoc fixture values, so use `--quiet` for sensitive values in shared
  terminals. Structured `--format json|yaml|table|pipe` omits raw input and arg
  values. Successful hook-run diagnostics are capped at 10 MiB per invocation.
- Use `untaped-recipe new pack <pack>`, `new recipe <pack>/<recipe>`, and
  `new hook <pack>/<hook>` to scaffold authoring projects. For local explicit
  paths, `new hook ./some-local-pack/probe` targets `./some-local-pack` and
  creates `probe`; bare multi-segment refs must be exactly `<pack>/<name>`.
  Pass `--no-lock` only when the package index is unavailable; it skips
  `uv.lock` creation/refresh, exits successfully, and prints a stderr note.
- Pack test cases live at `tests/<recipe>/<case>/`. `given/` is the single
  fixture target directory; `expected/` is the full expected tree after the
  plan, and omitting `expected/` asserts no planned changes. Optional
  `case.yml` supports only data fields: `inputs`, `expect: success|error`,
  `error_contains`, and `verdict` (`status` worst-of plus
  `message_contains`).
- `test --update` regenerates `expected/`, deletes it when the plan is empty,
  requires an explicit pack or recipe argument, and rejects `expect: error`
  cases. A normal test run never writes pack fixtures.
- To author a pack test: scaffold the recipe, fill `tests/<recipe>/basic/given/`,
  run `untaped-recipe test <pack>/<recipe> --update`, review the generated
  `expected/`, then commit the fixtures with the recipe.
- V1 step types are `validate`, `transform`, `template`, `copy`, and
  `remove`.
- Template steps are strict by default. Unknown bare names and non-bare
  `{{ ... }}` tokens fail unless the step sets `unknown_tokens: keep`, which
  preserves tokens like `${{ github.ref }}` and `{{ .Values.x }}` while still
  rendering known inputs.
- `transform` accepts either `file` or explicit `files`; `files` expands to
  one step per listed file. Missing transform targets fail unless the transform
  also sets `optional: true`.
- `remove` accepts either `file` or explicit `files`; missing remove targets
  are skipped.
- Do not use globbing in recipes. List the known candidate paths the recipe is
  allowed to touch.
- Common YAML edits should use the built-in `yaml_edit` transform hook. It
  supports `set`, `merge`, and `delete` with mapping keys, list indexes, and
  `where` list-item selectors.
- The engine does not provide a general YAML selector DSL; `yaml_edit` is the
  lone built-in hook and custom behavior belongs in trusted Python pack hooks.
- External hooks live in uv-managed packs with `pyproject.toml`, `uv.lock`, and
  `[tool.untaped_recipe.hooks]` metadata.
- Scaffolded hooks use `TYPE_CHECKING` imports from
  `untaped_recipe.hook_api.HookHelpers` so editors can discover helper methods
  through the dev-only `untaped-recipe` dependency. That public protocol models
  external worker helpers; `pass_`, `warn`, and `fail` return dict-shaped
  verdicts.
- Pack hooks declare `[tool.untaped_recipe].requires_hook_api = ">=0.9,<1"` to
  fail fast when the installed CLI's helper API is incompatible. The scaffold
  adds this marker and the `untaped-recipe>=0.9` dev dependency automatically.
  The dev floor tracks the hook API contract for editor type discovery, not
  each CLI release.
- Hook scaffolding refreshes `uv.lock`, so it needs package-index access or a
  configured uv source for `untaped-recipe`. If `uv lock` fails after files are
  written, the scaffolded pack, recipe, hook module, tests, and manifest rows
  stay in place; fix the index or add a package-specific `[tool.uv.sources]`
  override, then run `uv lock` in the pack. A lagging corporate mirror can use
  `[tool.uv.sources]` to route only `untaped-recipe` to an approved fallback
  index.
- Do not add `untaped-recipe` to a pack's runtime dependencies. The
  installed CLI owns the worker and helper implementation, and hook workers run
  with `uv run --locked --no-dev`; packages imported by hook code at runtime
  must be in `[project].dependencies`.
- External helper `render_template(template, inputs, unknown_tokens="error")`
  is strict by default; use `unknown_tokens="keep"` for nested template syntax.
- External helper `dump_yaml(data, options=...)` accepts plain dict formatting
  options: `width`, `preserve_quotes`, nested `indent` keys `mapping`,
  `sequence`, and `offset`, `block_seq_indent`, `explicit_start`, and
  `explicit_end`. Defaults are `preserve_quotes=True` and `width=4096` for both
  in-process built-ins and external workers. `load_yaml(content)` has no
  options. Unsupported option keys are rejected.
- Built-ins are direct engine imports and do not start uv workers. External
  hooks run through pooled uv workers, up to the clamped `--parallel` value per
  hook project; hook stdout must not be used for data because stdout is reserved
  for the worker protocol and `print()` is redirected to stderr. Timed-out hook
  requests kill and retire the worker and are reported as per-target planning
  failures.
- Pack hook modules use the scaffolded `src/` layout. Declared hook modules
  must resolve to files under `src/`; use explicit paths like `./my-pack` when
  managing a project in the current directory.

## Output And Agent Guidance

- Prefer `--format json` for machine-readable summaries and `--format pipe`
  when chaining into other untaped tools.
- Use `--columns` to narrow list/output rows. `apply` emits `recipe.outcome`
  rows; `test` emits `recipe.test`; `hook run` emits `recipe.hook_run`;
  library commands emit `recipe.recipe`, `recipe.hook`, `recipe.pack`,
  `recipe.check`, and `recipe.backup`.
- Optional transform skips appear in the `warnings` field of `recipe.outcome`
  rows as a semicolon-delimited string.
- Apply rows and backup metadata use canonical recipe refs such as
  `pack/recipe`.
- `recipe.outcome` rows include resolved declared `inputs`. Sensitive inputs
  render as `***`, row warnings/errors are redacted, and file-level previews
  and diffs are suppressed for targets with sensitive inputs; real values still
  reach templates and hooks.
- Backup file entries include redacted per-target inputs and never store the
  full incoming pipe record.
- `--check` emits `recipe.outcome` rows with `status` set to `check`.
- After a real apply, `recipe.outcome` rows report `applied`, `unchanged`, or
  `error` per target — `unchanged` means the plan produced no writes there.
- `apply --stdin` consumes bare paths plus untaped pipe records. Bare lines
  that parse as JSON scalars (a directory named `2024`) are still paths; only
  JSON objects are treated as records. It resolves
  absolute `record.target_path` first, then generic `record.path`; records
  whose `kind` ends in `.summary` are skipped as non-targets. Repo-grain
  records such as `workspace.repo` must provide `target_path` and stale
  `path`+`repo` streams fail before planning. `--stdin --interactive` reads
  target data from stdin and prompts only through the controlling terminal; it
  fails clearly if no terminal is available. `--stdin` writes still require
  `--yes` unless `--dry-run` or `--check` is used.
- The SDK provides `--quiet`/`-q`, `config doctor`, and `config edit`.
  `--quiet` mutes post-run success chatter, not selected preview detail,
  warnings, errors, or destructive confirmation prompts. `--format` and
  `--columns` affect stdout outcome rows only, not the fixed-column preview
  table.
- Run `untaped-recipe skills install` to install this packaged skill for agent
  workflows.

## Safety

Recipes are VCS-agnostic and do not call git. Review the preview before
confirming broad changes; use `--preview diff` when exact hunks are needed.
Python hooks are trusted local code; inspect hooks before running recipes from
another person. Installing a pack is installing code on the same trust model as
`pip install`; use `add` preview, `show`, and `check` to evaluate before
trusting it.

`apply` plans every target before writing. A failed target plan or write does
not block successful targets, and a failed target writes nothing for that
target. Piped stdin without `--yes` is refused before planning unless
`--dry-run` or `--check` is used. Engine-mediated recipe-local and
target-relative paths reject absolute paths, `..` segments, and nested symlink
traversal. Backup restore uses the same transactional, symlink-confined write
path as apply and preserves later-edit hash guards unless `--force` is passed.
Backups store text content for engine-managed files and do not preserve file
mode or mtime.
