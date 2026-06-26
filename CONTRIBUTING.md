# Contributing

Thanks for contributing to `untaped-recipe`.

## Local Setup

```bash
uv sync
uv run pre-commit run --all-files
uv run ruff check --fix
uv run ruff format
uv run mypy
uv run pytest
uv build
```

## Documentation

Update `README.md`, `AGENTS.md`, and
`src/untaped_recipe/skills/untaped-recipe/SKILL.md` when a change affects
command behavior, settings, recipe schema, hook contracts, workflows, output
contracts, or agent-facing usage.

## Sensitive Data

Do not include secrets, real customer configurations, real recipe targets,
production logs, health exports, or private data in issues, tests, fixtures,
or examples. Use synthetic data for tests and examples.
