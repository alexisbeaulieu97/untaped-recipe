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
  assets, and hook metadata without targets, inputs, or hook execution. `remove`
  is destructive and requires confirmation or `--yes`.
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
- `--check` emits `recipe.outcome` rows with `status` set to `check`.
- `apply --stdin` consumes bare paths plus `workspace.workspace` and
  `workspace.repo` records.
- The SDK provides `--quiet`/`-q`, `config doctor`, and `config edit`.
- Run `untaped-recipe skills install` to install this packaged skill for agent
  workflows.

## Safety

Recipes are VCS-agnostic and do not call git. Review the diff preview before
confirming broad changes. Python hooks are trusted local code; inspect hooks
before running recipes from another person.

`apply` plans every target before writing. A failed target plan or write does
not block successful targets, and a failed target writes nothing for that
target. Piped stdin without `--yes` is refused before planning unless
`--dry-run` or `--check` is used. Engine-mediated recipe-local and
target-relative paths reject absolute paths, `..` segments, and nested symlink
traversal. Backup restore uses the same transactional, symlink-confined write
path as apply and preserves later-edit hash guards unless `--force` is passed.
Backups store text content for engine-managed files and do not preserve file
mode or mtime.
