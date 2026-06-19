# AGENTS.md - `untaped-recipe`

Single source of truth for this standalone CLI repo. If command behavior,
settings, recipe schema, hook contracts, architecture, or development
workflow changes, update this file and the packaged skill in the same change.

## Mission

`untaped-recipe` applies trusted local recipe packages across one or more
plain directories. It is intentionally VCS-agnostic: no clone, branch, git
diff, commit, push, or PR behavior belongs here. Workspace selection can come
from another tool through stdin or pipe records, but this repo owns only
recipe execution, previews, backups, and restore.

## Hard Rules

1. Keep `AGENTS.md`, docs, README, and
   `src/untaped_recipe/skills/untaped-recipe/SKILL.md` current with user-facing
   behavior.
2. Use the untaped SDK entry point in `src/untaped_recipe/__main__.py`.
   The `ToolSpec` command is `untaped-recipe`, the settings section is
   `recipe`, and the profile model is `RecipeSettings`.
3. Keep the package root import-light. `untaped_recipe.__init__` may lazily
   re-export `app`, but importing the package must not eagerly import the CLI.
4. Use the four-layer layout:
   `cli/` for command signatures, `application/` for use cases and ports,
   `domain/` for pure models, and `infrastructure/` for filesystem, hook,
   backup, diff, and YAML adapters.
5. Use absolute imports. CLI code may import from `untaped.api`; tests may use
   `untaped.testing`.
6. stdout is data only. Diffs, prompts, and status messages go to stderr.
7. Do not add shell-command steps without a new design review. V1 writes are
   engine-mediated so preview, backups, and per-target transactional writes stay
   coherent.
8. Python hooks are trusted local code. The engine does not sandbox hooks, but
   normal file mutation must still be expressed as planned file changes.
9. Backups are on by default for applies. Restore refuses to overwrite edits
   made after the backup unless `--force` is passed.
10. Finish changes with the development workflow below.

## Architecture

```text
src/untaped_recipe/
├── __main__.py          # SDK ToolSpec and console-script entry point
├── settings.py          # recipe settings section
├── cli/                 # Cyclopts commands and output rendering
├── application/         # apply orchestration, target parsing, ports
├── domain/              # schema, verdicts, file changes, plans
├── infrastructure/      # libraries, hook loading, backups, diffs, YAML
├── builtins/hooks/      # packaged trusted transform hooks
└── skills/              # packaged agent skill
```

The `application` layer plans all target changes in memory. The CLI renders
diff previews first, then calls the SDK batch confirmation helper. Successful
target plans are flushed only after confirmation and one backup bundle has been
created for the invocation.

## Settings And Library Layout

`RecipeSettings.library_root` defaults to `~/.untaped/untaped-recipes` and
can be configured in the shared untaped profile under `recipe.library_root`.
The directory layout is:

```text
recipes/
hooks/
backups/
```

Recipes resolve in this order:

1. `recipes/<name>/recipe.yml`
2. `recipes/<name>.yml`
3. explicit filesystem file or directory containing `recipe.yml`

Hooks resolve in this order:

1. recipe-local `hooks/<name>.py`
2. global `hooks/<name>.py`
3. packaged built-ins under `src/untaped_recipe/builtins/hooks/`

Hook names in recipes must be safe logical names, not filesystem paths.

## Recipe Schema

V1 recipes use `version: 1`, `name`, optional `description`, optional
`inputs`, and `steps`. Step types are:

- `validate`: call a read-only hook.
- `transform`: read one target file, call a transform hook, and plan new
  content.
- `template`: render a recipe-local template into a target-relative path.
- `copy`: copy a recipe-local text file into a target-relative path.
- `remove`: remove one target-relative file if it exists.

Do not add a general YAML selector DSL to the core engine. Common YAML edits
belong in the shipped `yaml_edit` transform hook backed by `ruamel.yaml`.

## Hook Contracts

Transform hooks expose:

```python
def transform(
    content: str,
    *,
    inputs: dict,
    target: Path,
    file: Path,
    args: dict,
    helpers: HookHelpers,
) -> str: ...
```

Validate hooks expose:

```python
def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: HookHelpers,
) -> Verdict: ...
```

Validate hooks may return a `Verdict`, a compatible dict, `None` for pass, or
a string for fail. Prefer explicit `helpers.pass_()`, `helpers.warn()`, and
`helpers.fail()` in shipped examples.

## Development Workflow

```bash
uv sync
uv run pre-commit run --all-files
uv run ruff check --fix
uv run ruff format
uv run mypy
uv run pytest
uv build
git diff --check --cached
```

Use `uv --cache-dir .uv-cache run ...` when working from the
`untaped-dev` symlinked workspace.

## See Also

- Core SDK: https://github.com/alexisbeaulieu97/untaped
