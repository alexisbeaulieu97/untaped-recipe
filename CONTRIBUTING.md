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

Every behavior fact has exactly one owning concept page under `docs/`; the
concept → owning-page table lives in [AGENTS.md](./AGENTS.md#documentation-contract).
When a change affects behavior, update the owning page and re-derive the
derived surfaces (`README.md` and
`src/untaped_recipe/skills/untaped-recipe/SKILL.md`) in the same change.
GitHub release notes are the change record; there is no changelog file.

## Sensitive Data

Do not include secrets, real customer configurations, real recipe targets,
production logs, health exports, or private data in issues, tests, fixtures,
or examples. Use synthetic data for tests and examples.
