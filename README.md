# untaped-recipe

`untaped-recipe` is a standalone CLI for applying trusted local recipes across
plain directories. It is built on the
[`untaped`](https://github.com/alexisbeaulieu97/untaped) SDK and deliberately
does not clone repos, create branches, commit, push, or open PRs. Recipes plan
every change in memory, preview it, and only write after confirmation — see
[docs/](./docs/) for the full documentation.

## Install

```bash
uv tool install untaped-recipe
```

## Configure

Settings live in the shared untaped config under the `recipe` section:

```bash
untaped-recipe config set library_root ~/.untaped/untaped-recipes
```

The recipe-specific keys are `library_root`, `hook_timeout_seconds`,
`hook_startup_timeout_seconds`, `preview_max_rows`, `backup_keep`, and
`backup_max_age_days` — see [docs/reference.md](./docs/reference.md) for the
settings table. For the config file format, profiles, and environment-variable
overrides, see the core
[configuration docs](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/configuration.md).

## Quickstart

```bash
# Install a recipe pack, preview what it ships, confirm
untaped-recipe add ./ansible

# Apply a recipe to targets (plans, previews, confirms, backs up, writes)
untaped-recipe apply ansible/playbook-migration ./service-a ./service-b

# Drive targets from another untaped tool
untaped-workspace list --format pipe | untaped-recipe apply add-config --stdin --yes

# Drift check for CI: writes nothing, exits non-zero on pending changes
untaped-recipe apply add-config ./service-a --check

# Scaffold a new pack with a recipe and a hook
untaped-recipe new pack mypack
untaped-recipe new recipe mypack/add-config
untaped-recipe new hook mypack/set_owner

# Undo the last apply
untaped-recipe backup restore latest
```

## Documentation

Concept pages under [docs/](./docs/):

- [recipes](./docs/recipes.md) — recipe YAML schema, step types, design rationale
- [inputs](./docs/inputs.md) — the input contract: types, scope, `from` derivation, precedence
- [templating](./docs/templating.md) — the `{{ name }}` token language and path-field rendering
- [hooks](./docs/hooks.md) — hook contract, execution model, helpers, `hook run`, `yaml_edit`
- [packs](./docs/packs.md) — pack manifest, library, references, scaffolding, trust
- [apply](./docs/apply.md) — running recipes: preview, confirmation, check mode, transactional writes
- [safety](./docs/safety.md) — path confinement, backups and restore, integrity mechanisms
- [testing](./docs/testing.md) — `check` preflight and the golden-fixture `test` harness
- [pipes](./docs/pipes.md) — stdin target ingestion and structured output
- [reference](./docs/reference.md) — settings, command index, exit codes, skills
- [release](./docs/release.md) — maintainer release runbook

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

See [AGENTS.md](./AGENTS.md) for architecture rules, product invariants, and
the documentation contract.

## Security

Please report suspected vulnerabilities privately. See
[SECURITY.md](./SECURITY.md).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the local workflow and
[docs/release.md](./docs/release.md) for the release workflow.

## License

MIT. See [LICENSE](./LICENSE).
