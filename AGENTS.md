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
8. External Python hooks are trusted local uv hook projects executed by pooled
   workers. Built-ins are engine-owned direct imports. Do not reintroduce
   importlib file loading for arbitrary `.py` hooks, pluggy, or PEP 723 hooks
   without a new design review.
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
├── infrastructure/      # libraries, hook resolution/execution, backups, diffs, YAML
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

1. recipe project `pyproject.toml` entries under `[tool.untaped_recipe.hooks]`
2. global hook projects under `hooks/<name>/`
3. namespaced hook packs under `hooks/<namespace>/` for dotted names
4. packaged built-ins registered in `src/untaped_recipe/builtins/registry.py`

Hook names in recipes must be safe logical names, not filesystem paths.
Recipes never declare hook runtimes. The resolver returns either a built-in
reference or a uv hook project reference.

## Recipe Schema

V1 recipes use `version: 1`, `name`, optional `description`, optional
`inputs`, and `steps`. Step types are:

- `validate`: call a read-only hook.
- `transform`: read one target file, call a transform hook, and plan new
  content. `optional: true` is supported only here; missing target files are
  skipped with a warning, but files deleted earlier in the same plan still
  error.
- `template`: render a recipe-local template into a target-relative path.
- `copy`: copy a recipe-local text file into a target-relative path.
- `remove`: remove one target-relative file if it exists.

`transform` and `remove` may accept `files` as explicit fan-out syntax. Keep
that behavior normalized in `Recipe` model validation so `ApplyRecipe` continues
to plan ordinary single-file steps. Do not add globbing or selector discovery
to the engine; recipes must list known candidate paths explicitly.

Do not add a general YAML selector DSL to the core engine. Common YAML edits
belong in the shipped `yaml_edit` transform hook backed by `ruamel.yaml`.

## Hook Contracts

External hook projects are uv-managed directories with `pyproject.toml`,
`uv.lock`, package code under `src/`, and a hook metadata table:

```toml
[tool.untaped_recipe.hooks]
"ansible.add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

External hooks run out-of-process through NDJSON stdin/stdout workers. Worker
stdout is protocol-only; hook `print()` output is redirected to stderr. The
worker uses stdlib wire parsing, and the engine validates worker responses
before using them. A bounded worker pool is created per hook project per apply
invocation, up to the clamped `--parallel` value; each worker serializes its own
requests.

Built-ins use the direct registry and run in-process. Keep built-ins reserved
for engine-owned code such as `yaml_edit`.

External transform hooks expose:

```python
def transform(
    content: str,
    *,
    inputs: dict,
    target: Path,
    file: Path,
    args: dict,
    helpers: object,
) -> str: ...
```

External validate hooks expose:

```python
def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: object,
) -> dict | None | str: ...
```

Validate hooks may return a compatible verdict dict, `None` for pass, a string
for fail, or a `Verdict`-like object with `model_dump()` if the hook project
chooses to depend on `untaped-recipe`. Prefer explicit `helpers.pass_()`,
`helpers.warn()`, and `helpers.fail()` in shipped examples. Built-in hooks may
use the engine's concrete `HookHelpers` and `Verdict` types directly.

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
