---
name: untaped-recipe
description: Use the untaped-recipe CLI to apply local recipe packages to directories.
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
- Backups are created by default; use `--no-backup` only when the target tree is already protected.
- `untaped-recipe backup list|show|restore` manages backup bundles; `show` and
  `restore` accept full ids, unambiguous prefixes, or `latest`.
- `untaped-recipe recipe list|show|add|remove|edit` manages local recipes;
  `remove` is destructive and requires confirmation or `--yes`.
- `untaped-recipe hook list|show|add|remove|edit` manages reusable hooks;
  `remove` is destructive and requires confirmation or `--yes`.

## Recipe Model

- Library root defaults to `~/.untaped/untaped-recipes`.
- Recipe resolution checks `recipes/<name>/recipe.yml`, then
  `recipes/<name>.yml`, then explicit filesystem paths.
- Hook resolution checks recipe-local `hooks/`, then global library `hooks/`,
  then packaged built-ins.
- V1 step types are `validate`, `transform`, `template`, `copy`, and
  `remove`.
- Common YAML edits should use the built-in `yaml_edit` transform hook. It
  supports `set`, `merge`, and `delete` with mapping keys, list indexes, and
  `where` list-item selectors.
- The engine does not provide a general YAML selector DSL; `yaml_edit` is a
  shipped hook and custom behavior belongs in trusted Python hooks.

## Output And Agent Guidance

- Prefer `--format json` for machine-readable summaries and `--format pipe`
  when chaining into other untaped tools.
- Use `--columns` to narrow list/output rows. `apply` emits `recipe.outcome`
  rows; library commands emit `recipe.recipe`, `recipe.hook`, and
  `recipe.backup`.
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
`--dry-run` is used. Engine-mediated recipe-local and target-relative paths
reject absolute paths, `..` segments, and nested symlink traversal.
