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

## Ways To Define Recipes And Hooks

Recipes can be stored as either:

- a single recipe file: `recipes/<name>.yml`
- a recipe project: `recipes/<name>/recipe.yml` with optional templates, files,
  package code, `pyproject.toml`, and `uv.lock`
- an explicit filesystem recipe file or directory containing `recipe.yml`

Hooks are referenced from recipes by name. Recipes do not declare hook runtimes.
External hooks live in uv-managed hook projects with a
`[tool.untaped_recipe.hooks]` table:

```toml
[tool.untaped_recipe.hooks]
"ansible.add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Supported hook forms are:

- recipe-local hooks declared in a recipe project's `pyproject.toml`
- global hook projects under `<library_root>/hooks/<name>/`
- namespaced hook packs under `<library_root>/hooks/<namespace>/`, referenced as
  `namespace.hook`
- built-ins such as `yaml_edit`, which are engine-owned and run in-process

Use `untaped-recipe hook init <hook-name>` to scaffold a global uv hook project.
For a namespaced pack, initialize the first hook with a dotted name such as
`untaped-recipe hook init ansible.add_play_collections`.

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

Recipes can list known candidate files explicitly for `transform` and `remove`
steps. `transform.files` and `remove.files` are expanded into ordinary
per-file steps, and `transform` can use `optional: true` to skip playbooks or
config files that are absent in some targets. Missing optional transforms are
reported as warnings in `recipe.outcome` rows. There is no globbing; recipes
name the candidate paths they intend to touch.

Piped stdin accepts bare paths and untaped pipe records. For
`workspace.workspace` records it uses `record.path`; for `workspace.repo`
records it uses `Path(record.path) / record.repo`.

## Library Commands

```text
untaped-recipe recipe list|show|add|remove|edit
untaped-recipe hook init|list|show|add|remove|edit
untaped-recipe backup list|show|restore
```

`hook add` copies uv hook project directories, not bare `.py` files. `recipe
remove` and `hook remove` require confirmation or `--yes`. `backup show` and
`backup restore` accept full ids, unambiguous prefixes, or `latest`.

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
