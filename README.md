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

Recipes, reusable hooks, and backups live under
`~/.untaped/untaped-recipes` by default.

```bash
untaped-recipe config set library_root ~/.untaped/untaped-recipes
```

The setting is stored in the shared untaped config under the `recipe` section.

## Apply

```bash
untaped-recipe apply add-config ./service-a ./service-b --var service=api
untaped-recipe apply ./recipes/add-config/recipe.yml ./service-a --yes
untaped-recipe apply add-config --stdin --yes --format json
untaped-recipe apply add-config ./service-a --dry-run
```

`apply` plans every target first, prints unified diffs to stderr, then asks for
confirmation unless `--yes` is passed. Backups are created by default before
writing and can be restored later. Target writes are transactional: if a target
cannot be written safely, that target is rolled back and reported as failed.

Piped stdin accepts bare paths and untaped pipe records. For
`workspace.workspace` records it uses `record.path`; for `workspace.repo`
records it uses `Path(record.path) / record.repo`.

## Library Commands

```text
untaped-recipe recipe list|show|add|remove|edit
untaped-recipe hook list|show|add|remove|edit
untaped-recipe backup list|show|restore
```

`recipe remove` and `hook remove` require confirmation or `--yes`. `backup show`
and `backup restore` accept full ids, unambiguous prefixes, or `latest`.

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
