# untaped-recipe

`untaped-recipe` is a standalone CLI for applying trusted local recipes across
plain directories. It is built on the
[`untaped`](https://github.com/alexisbeaulieu97/untaped) SDK and deliberately
does not clone repos, create branches, commit, push, or open PRs.

## Install

```bash
uv tool install git+https://github.com/alexisbeaulieu97/untaped-recipe.git
```

## Configure

Installed packs and backup bundles live under `~/.untaped/untaped-recipes` by
default.

```bash
untaped-recipe config set library_root ~/.untaped/untaped-recipes
```

The setting is stored in the shared untaped config under the `recipe` section.
External hook requests time out after `hook_timeout_seconds` seconds, default
`60`; set it to `0` to disable the timeout for long-running trusted hooks.

## Library Model

The library has one installable item type: packs.

- installed pack projects under `<library_root>/packs/<pack-id>/`
- install-source bookkeeping in `<library_root>/packs.toml`
- backup bundles under `<library_root>/backups/`

Packs are uv projects. Public pack identity comes from `[project].name` in the
top-level `pyproject.toml`; `untaped-recipe-ansible` installs as pack
`ansible`:

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"

[dependency-groups]
dev = ["untaped-recipe>=0.9"]

[tool.untaped_recipe]
requires_hook_api = ">=0.9,<1"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Recipe YAML is behavior-only. It contains `version`, optional `description`,
optional `inputs`, and `steps`; `name:` is rejected.

Single-file recipes are still supported by explicit path, for quick local use:

```bash
untaped-recipe apply ./recipe.yml ./service-a --yes
```

They are not installed as loose `recipes/<name>.yml` library items.

## Authoring

Hooks are referenced from recipes by name. Recipes do not declare hook runtimes,
and hook manifest rows do not declare `kind`; the exported function name is the
contract. A hook module exports `transform()`, `validate()`, or both, and the
recipe step `type` selects which function runs.

```toml
[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Supported hook sources are:

- the recipe's own pack
- installed packs
- built-ins such as `yaml_edit`, which are engine-owned and run in-process

Generated hooks use `TYPE_CHECKING` imports from `untaped_recipe.hook_api` for
editor discovery through the dev-only `untaped-recipe` dependency. Pack projects
must not depend on `untaped-recipe` at runtime; the installed CLI provides the
worker and helper implementation. Runtime hook dependencies belong in
`[project].dependencies`, and type-only authoring dependencies belong in
`[dependency-groups].dev` because workers execute with
`uv run --locked --no-dev`. Hook scaffolding refreshes `uv.lock`, so it needs
access to PyPI or a configured uv source for `untaped-recipe`. The scaffolded
dev dependency tracks the hook API floor for editor type discovery, not every
CLI release.

If `uv lock` fails after scaffold files are written, `new pack`, `new recipe`,
and `new hook` leave the pack, recipe, hook module, tests, and manifest rows in
place and print a repairable error. Fix the package index or add a
package-specific `[tool.uv.sources]` override, then run `uv lock` in the pack.
For example, a lagging corporate mirror can route only `untaped-recipe` to an
approved fallback index:

```toml
[tool.uv.sources]
untaped-recipe = { index = "approved-pypi" }

[[tool.uv.index]]
name = "approved-pypi"
url = "https://pypi.org/simple"
explicit = true
```

uv also provisions Python interpreters from GitHub on demand. On networks that
block it, point `UV_PYTHON_INSTALL_MIRROR` at an approved mirror of
python-build-standalone (or preinstall a matching interpreter) so pack
environments can build at all.

Use `--no-lock` with `new pack`, `new recipe`, or `new hook` to skip the lock
step entirely. The command exits successfully and prints a stderr note, but
hooks cannot run until `uv lock` succeeds because workers use
`uv run --locked --no-dev`.

```bash
untaped-recipe new pack ansible
untaped-recipe new recipe ansible/playbook-migration
untaped-recipe new hook ansible/add_play_collections
untaped-recipe add ./ansible --yes
untaped-recipe hook run ansible/add_play_collections --target ./service-a --file site.yml --diff
```

## Apply

```bash
untaped-recipe apply add-config ./service-a ./service-b --var service=api
untaped-recipe apply ansible/playbook-migration ./service-a --yes
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

Piped stdin accepts bare paths and untaped pipe records. Recipe resolves
absolute `record.target_path` first, then falls back to `record.path` for
generic path records. Records whose `kind` ends in `.summary` are informational
and skipped as non-targets. Repo-grain records such as `workspace.repo` must
provide `target_path`; older saved streams that only contain `path` plus `repo`
are rejected instead of writing to the wrong directory.

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
untaped-recipe new pack <name> [--no-lock]
untaped-recipe new recipe <pack>/<name> [--no-lock]
untaped-recipe new hook <pack>/<name> [--no-lock]
untaped-recipe add <path|git-url> [--rev REV] [--name NAME] [--force]
untaped-recipe list [--packs|--hooks]
untaped-recipe show <pack|recipe-ref|hook-ref>
untaped-recipe check [pack|recipe-ref|path]
untaped-recipe test [pack|path|pack/recipe] [--update]
untaped-recipe remove <pack>
untaped-recipe edit <pack|recipe-ref|hook-ref>
untaped-recipe hook run <hook-ref>
untaped-recipe backup list|show|restore
```

`add` installs a pack from a local path or git URL and asks for confirmation
after listing the recipes and hooks being installed. `--name` overrides the
installed pack key; that key is the identity used by refs, output rows,
ambiguity errors, `check`, and `remove`.

`list` shows recipes by default. `list --packs` shows installed packs and
`list --hooks` shows hook refs. `show` renders structured pack, recipe, or hook
detail. `check` is static preflight; without a ref it validates the whole
library and `packs.toml`, and with a ref it validates one pack or recipe.
`test` runs golden-fixture cases packs ship under `tests/`; `--update`
regenerates goldens for an explicit pack or recipe.
`remove <pack>` is destructive because library packs are editable in place; it
requires confirmation or `--yes`. `backup show` and `backup restore` accept
full ids, unambiguous prefixes, or `latest`; restore previews and confirms like
apply, uses the same transactional write path and symlink confinement, and
preserves the changed-since-backup hash guard unless `--force` is passed.
Backups store text content and do not preserve file mode or mtime.

`hook run` is a no-write debug harness. Transform hooks require `--file`; by
default the command reads `--target/--file` and writes exact transformed content
to stdout with no added newline. Use `--content TEXT`, `--content -`, or
`--content-file PATH` to supply fixture content without requiring the target
file to exist. Use `--diff` to emit a unified diff instead of raw content.
Validate hooks reject file/content options and emit a `recipe.hook_run` verdict
record. Repeated `--input KEY=VALUE` and `--arg KEY=VALUE` values are
YAML-parsed and override `--inputs`/`--args` YAML mapping files. Fixture context
and hook diagnostics go to stderr; structured `--format json|yaml|table|pipe`
output omits raw input and arg values. Use SDK `--quiet` when ad-hoc fixture
values should not be echoed in a shared terminal.

See [docs/packs.md](./docs/packs.md), [docs/recipes.md](./docs/recipes.md),
and [docs/hooks.md](./docs/hooks.md) for pack, schema, and hook authoring
details. See [docs/migration-0.9.md](./docs/migration-0.9.md) for the 0.9.0
breaking changes.

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

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md) for the
local workflow, architecture rules, product contracts, and
[docs/release.md](./docs/release.md) for the release workflow.

## License

MIT. See [LICENSE](./LICENSE).
