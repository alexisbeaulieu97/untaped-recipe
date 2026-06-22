---
name: untaped-recipe
description: Use the untaped-recipe CLI to apply local recipe projects and packs.
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
  `0` disables timeout for trusted long-running hooks.
- Backups are created by default; use `--no-backup` only when the target tree is already protected.
- `untaped-recipe backup list|show|restore` manages backup bundles; `show` and
  `restore` accept full ids, unambiguous prefixes, or `latest`.
- `untaped-recipe recipe init|list|show|add|check|remove|edit` manages
  standalone recipe projects; `check` is static preflight that validates schema,
  input source expressions, assets, and hook metadata without targets or hook
  execution. `remove` is destructive and requires confirmation or `--yes`.
- `untaped-recipe pack init|list|show|add|check|remove|edit` manages recipe pack
  projects. `untaped-recipe pack recipe init|list|show|edit|remove` manages
  recipes inside a pack; pack recipe removal is destructive and requires
  confirmation or `--yes`.
- `untaped-recipe hook init|list|show|add|remove|edit` manages uv hook
  project directories; `remove` is destructive and requires confirmation or
  `--yes`. `hook add` derives the library directory from the declared hook
  metadata; `--name`, if passed, must match that derived name.

## Recipe Model

- Library root defaults to `~/.untaped/untaped-recipes`.
- Library items are uv projects: standalone recipes under
  `<library_root>/recipes/<recipe-id>/`, packs under
  `<library_root>/packs/<pack-id>/`, and reusable global hooks under
  `<library_root>/hooks/<hook-id>/`.
- Public recipe and pack identity comes from top-level `pyproject.toml`
  metadata, not from `recipe.yml`.
- Recipe YAML is behavior-only: `version`, optional `description`, optional
  `inputs`, and `steps`. `name:` is rejected.
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
- `apply foo` resolves only standalone library recipe `recipes/foo/`.
- `apply pack:recipe` resolves an installed pack recipe from `packs/pack/`.
- `apply ./recipe.yml` runs a path-only single-file recipe.
- `apply ./recipe-project` runs a local standalone recipe project.
- `apply ./pack-project --recipe recipe` runs a recipe from a local pack.
- Recipe-local and pack-local hooks are declared in the top-level project
  `pyproject.toml`. Global hooks live under `<library_root>/hooks/<name>/`.
- Recipes only name hooks; they do not declare runtimes.
- Hook resolution checks recipe-local pyproject metadata, then global hook
  projects, then packaged built-ins.
- Use `untaped-recipe recipe init <name>` and
  `untaped-recipe pack init <name>` to scaffold authoring projects. Add local
  hooks with `untaped-recipe recipe hook init <recipe> <hook>` or
  `untaped-recipe pack hook init <pack> <hook>`.
- V1 step types are `validate`, `transform`, `template`, `copy`, and
  `remove`.
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
- The engine does not provide a general YAML selector DSL; `yaml_edit` is a
  shipped hook and custom behavior belongs in trusted Python hooks.
- External hooks are uv-managed projects with `pyproject.toml`, `uv.lock`, and
  `[tool.untaped_recipe.hooks]` metadata. Use
  `untaped-recipe hook init <name>` to scaffold a reusable global hook.
- Built-ins are direct engine imports and do not start uv workers. External
  hooks run through pooled uv workers, up to the clamped `--parallel` value per
  hook project; hook stdout must not be used for data because stdout is reserved
  for the worker protocol and `print()` is redirected to stderr. Timed-out hook
  requests kill and retire the worker and are reported as per-target planning
  failures.
- Hook projects use the scaffolded `src/` layout. Declared hook modules must
  resolve to files under `src/`; use explicit paths like `./my-hook-project`
  when managing a project in the current directory.

## Output And Agent Guidance

- Prefer `--format json` for machine-readable summaries and `--format pipe`
  when chaining into other untaped tools.
- Use `--columns` to narrow list/output rows. `apply` emits `recipe.outcome`
  rows; library commands emit `recipe.recipe`, `recipe.hook`, and
  `recipe.backup`.
- Optional transform skips appear in the `warnings` field of `recipe.outcome`
  rows as a semicolon-delimited string.
- Apply rows and backup metadata use canonical recipe refs: `foo` for
  standalone recipes and `pack:recipe` for pack recipes.
- `recipe.outcome` rows include resolved declared `inputs`. Sensitive inputs
  render as `***`, row warnings/errors are redacted, and file-level previews
  and diffs are suppressed for targets with sensitive inputs; real values still
  reach templates and hooks.
- Backup file entries include redacted per-target inputs and never store the
  full incoming pipe record.
- `--check` emits `recipe.outcome` rows with `status` set to `check`.
- `apply --stdin` consumes bare paths plus `workspace.workspace` and
  `workspace.repo` records. `--stdin --interactive` reads target data from
  stdin and prompts only through the controlling terminal; it fails clearly if
  no terminal is available. `--stdin` writes still require `--yes` unless
  `--dry-run` or `--check` is used.
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
another person.

`apply` plans every target before writing. A failed target plan or write does
not block successful targets, and a failed target writes nothing for that
target. Piped stdin without `--yes` is refused before planning unless
`--dry-run` or `--check` is used. Engine-mediated recipe-local and
target-relative paths reject absolute paths, `..` segments, and nested symlink
traversal. Backup restore uses the same transactional, symlink-confined write
path as apply and preserves later-edit hash guards unless `--force` is passed.
Backups store text content for engine-managed files and do not preserve file
mode or mtime.
