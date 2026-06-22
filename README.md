# untaped-recipe

`untaped-recipe` is a standalone CLI for applying trusted local recipes across
plain directories. It is built on the
[`untaped`](https://github.com/alexisbeaulieu97/untaped) SDK and deliberately
does not clone repos, create branches, commit, push, or open PRs.

## Install

```bash
uv tool install untaped-recipe
```

## Configure

Recipes, packs, reusable hooks, and backups live under
`~/.untaped/untaped-recipes` by default.

```bash
untaped-recipe config set library_root ~/.untaped/untaped-recipes
```

The setting is stored in the shared untaped config under the `recipe` section.
External hook requests time out after `hook_timeout_seconds` seconds, default
`60`; set it to `0` to disable the timeout for long-running trusted hooks.

## Library Model

The library has separate first-class item types:

- standalone recipe projects under `<library_root>/recipes/<recipe-id>/`
- pack projects under `<library_root>/packs/<pack-id>/`
- reusable global hook projects under `<library_root>/hooks/<hook-id>/`
- backup bundles under `<library_root>/backups/`

Installed recipes and packs are uv projects. Public identity comes from the
top-level `pyproject.toml`, not from `recipe.yml`:

```toml
[tool.untaped_recipe.recipes]
"add-config" = { path = "recipe.yml" }
```

Recipe YAML is behavior-only. It contains `version`, optional `description`,
optional `inputs`, and `steps`; `name:` is rejected.

Packs declare a pack id and recipe paths:

```toml
[tool.untaped_recipe]
pack = "ansible"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }
```

Single-file recipes are still supported by explicit path, for quick local use:

```bash
untaped-recipe apply ./recipe.yml ./service-a --yes
```

They are not installed as loose `recipes/<name>.yml` library items.

## Authoring

Hooks are referenced from recipes by name. Recipes do not declare hook runtimes.
External hooks live in uv-managed hook projects with a
`[tool.untaped_recipe.hooks]` table:

```toml
[tool.untaped_recipe.hooks]
"ansible.add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Supported hook forms are:

- recipe-local hooks declared in a standalone recipe project's `pyproject.toml`
- pack-local hooks declared in a pack project's `pyproject.toml`
- global hook projects under `<library_root>/hooks/<name>/`
- built-ins such as `yaml_edit`, which are engine-owned and run in-process

Use `untaped-recipe hook init <hook-name>` to scaffold a global uv hook project.
Use `untaped-recipe recipe hook init <recipe> <hook>` or
`untaped-recipe pack hook init <pack> <hook>` for local hooks.

```bash
untaped-recipe recipe init add-config
untaped-recipe recipe init shared-config --library
untaped-recipe pack init ansible
untaped-recipe pack recipe init ansible playbook-migration
untaped-recipe recipe hook init add-config set_owner --kind validate
untaped-recipe pack hook init ansible add_play_collections
```

## Apply

```bash
untaped-recipe apply add-config ./service-a ./service-b --var service=api
untaped-recipe apply ansible:playbook-migration ./service-a --yes
untaped-recipe apply ./recipe-project ./service-a --yes
untaped-recipe apply ./pack-project ./service-a --recipe playbook-migration --yes
untaped-recipe apply ./recipe.yml ./service-a --yes
untaped-recipe apply add-config --stdin --yes --format json
untaped-recipe apply add-config --stdin --input-from service='{{ record.repo }}' --yes
untaped-recipe apply add-config ./service-a --dry-run
untaped-recipe apply add-config ./service-a --check
untaped-recipe apply add-config ./service-a --preview diff
```

`apply` plans every target first, prints a stderr preview, then asks for
confirmation unless `--yes` is passed. Normal apply and `--dry-run` default to
`--preview table`, which shows a file-level table with absolute paths, change
kind, and line counts. `--check` defaults to summary-only preview output for
CI; pass `--preview table` when you want the same file table in check mode.
Use `--preview diff` for patch-compatible unified diffs with `a/` and `b/`
relative paths, or `--preview none` for summary-only runs. `--preview` controls
safety review detail; `--quiet` only mutes success chatter after the run.
Backups are created by default before writing and can be restored later. Target
writes are transactional: if a target cannot be written safely, that target is
rolled back and reported as failed. Use `--check` for CI or compliance checks:
it writes nothing, creates no backups, prompts for nothing, and exits non-zero
when any target would change.

Recipes can list known candidate files explicitly for `transform` and `remove`
steps. `transform.files` and `remove.files` are expanded into ordinary
per-file steps, and `transform` can use `optional: true` to skip playbooks or
config files that are absent in some targets. Missing optional transforms are
reported as warnings in `recipe.outcome` rows. There is no globbing; recipes
name the candidate paths they intend to touch.

Piped stdin accepts bare paths and untaped pipe records. For
`workspace.workspace` records it uses `record.path`; for `workspace.repo`
records it uses `Path(record.path) / record.repo`.

Recipe inputs may be invocation-global or per-target. Input specs support
`description`, `sensitive`, `scope`, and `from` in addition to `type`,
`default`, and `required`. Omitted scope infers `target` when `from` is present
and `global` otherwise. Per-target `from` values are sandboxed strict native
Jinja strings evaluated only for scalar input derivation. They may combine
literal text, string/number/boolean/null constants that Jinja parses without
operators, and field access on `target.path`, `target.name`,
`target.parent_path`, `target.parent_name`, or optional incoming pipe `record`.
There are no ambient Jinja globals; control blocks, filters, tests, calls,
operators, and collection literals are rejected, so negative numeric
expressions like `{{ -1 }}` are not valid V1 sources. Missing, undefined, or
null candidates fall through; `false`, `0`, and empty strings are real values.
Oversized or non-scalar derived values are rejected.

Use `--input-from NAME=JINJA` to override a per-target source, `--var` or
`--vars` to provide fixed values, and `--interactive` to prompt for unresolved
inputs. A fixed value and source override for the same input is rejected.
`scope: global` rejects recipe `from` and `--input-from`, but accepts
`--var`/`--vars`. Interactive prompts run before recipe defaults; an empty
answer accepts the default when one exists. `--interactive --check` is
rejected. With `--stdin --interactive`, target records still come from stdin
and prompts use the controlling terminal. `--stdin` writes still require
`--yes` unless `--dry-run` or `--check` is used.

Every `recipe.outcome` row includes resolved declared inputs. Inputs marked
`sensitive: true` are redacted in rows, warnings/errors, and backup metadata;
file-level previews and diffs are suppressed for targets with sensitive inputs.
Real values still reach templates and hooks. Backup file entries record
redacted per-target inputs and never store the full incoming pipe record.

## Library Commands

```text
untaped-recipe recipe init|list|show|add|check|remove|edit
untaped-recipe pack init|list|show|add|check|remove|edit
untaped-recipe pack recipe init|list|show|edit|remove
untaped-recipe hook init|list|show|add|remove|edit
untaped-recipe backup list|show|restore
```

`recipe add` accepts only uv standalone recipe projects exposing exactly one
recipe. `pack add` accepts uv pack projects, including empty packs.
`hook add` copies uv hook project directories, not bare `.py` files, and the
library directory is derived from the declared hook metadata. Declared hook
modules must live under the project's `src/` layout. Use explicit paths such as
`./my-hook-project` when referring to a project in the current directory; bare
hook names resolve through the library. `recipe remove`, `pack remove`,
`pack recipe remove`, and `hook remove` require confirmation or `--yes`.
`recipe check` and `pack check` are static preflight commands; they validate
input source expressions but do not execute hooks against targets. `backup show`
and `backup restore` accept full ids, unambiguous prefixes, or `latest`;
restore uses the same transactional write path and symlink confinement as
apply. Backups store text content and do not preserve file mode or mtime.

See [docs/recipes.md](./docs/recipes.md) and
[docs/hooks.md](./docs/hooks.md) for schema and hook authoring details.

## Development

```bash
uv sync
uv run pre-commit run --all-files
uv run ruff check --fix
uv run ruff format
uv run mypy
uv run pytest
uv build
```

See [AGENTS.md](./AGENTS.md) for architecture rules and product contracts.
