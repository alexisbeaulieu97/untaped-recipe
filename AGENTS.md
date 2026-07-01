# AGENTS.md - `untaped-recipe`

Single source of truth for this standalone CLI repo. If command behavior,
settings, recipe schema, hook contracts, architecture, or development
workflow changes, update this file and the packaged skill in the same change.

## Mission

`untaped-recipe` applies trusted local recipe projects and packs across one or more
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
   made after the backup unless `--force` is passed and uses the same
   transactional, symlink-confined write path as apply. Backups store text
   content for engine-managed files and do not preserve mode or mtime.
10. Finish changes with the development workflow below.

## Architecture

```text
src/untaped_recipe/
├── __main__.py          # SDK ToolSpec and console-script entry point
├── settings.py          # recipe settings section
├── cli/                 # Cyclopts commands, output rows, and preview rendering
├── application/         # apply orchestration, hook-run use cases, target parsing, ports
├── domain/              # schema, verdicts, file changes, plans
├── infrastructure/      # libraries, hook resolution/execution, backups, diffs, YAML
├── builtins/hooks/      # packaged trusted transform hooks
└── skills/              # packaged agent skill
```

The `application` layer plans all target changes in memory and owns hook-run
fixture validation plus hook invocation. Pipe target parsing resolves
absolute `record.target_path` before generic `record.path`; records whose
`kind` ends in `.summary` are skipped as non-targets. Repo-grain records such
as `workspace.repo` must provide `target_path` and stale `path`+`repo` streams
fail before planning. The CLI renders stderr previews from
`cli/preview.py`: normal apply and `--dry-run` default to a file-level table,
while `--check` defaults to summary-only CI output unless `--preview table` or
`--preview diff` is passed. Diff mode keeps patch-compatible `a/` and `b/`
headers. The command path calls the SDK batch confirmation helper with the
generic target-row preview suppressed because recipe owns richer file-level
preview detail. Successful target plans are flushed only after confirmation and
one backup bundle has been created for the invocation. Target parsing preserves
optional untaped pipe record context for per-target input derivation;
`ApplyRecipe` still receives only a concrete target path plus resolved plain
inputs.

## Settings And Library Layout

`RecipeSettings.library_root` defaults to `~/.untaped/untaped-recipes` and
can be configured in the shared untaped profile under `recipe.library_root`.
`RecipeSettings.hook_timeout_seconds` defaults to `60`; `0` disables per-hook
request timeouts.
The directory layout is:

```text
recipes/
packs/
hooks/
backups/
```

Library entries are uv projects:

- `recipes/<recipe-id>/` is a standalone recipe project.
- `packs/<pack-id>/` is a recipe pack project exposing zero or more recipes.
- `hooks/<hook-id>/` is a reusable global hook project.

Standalone recipe and pack identity comes from top-level `pyproject.toml`
metadata, not from `recipe.yml`. A standalone recipe project declares exactly
one recipe:

```toml
[tool.untaped_recipe.recipes]
"add-config" = { path = "recipe.yml" }
```

A pack declares its pack id plus zero or more recipe paths:

```toml
[tool.untaped_recipe]
pack = "ansible"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }
```

Nested uv projects or uv workspaces inside a recipe or pack are opaque to this
tool. Only the top-level project metadata and declared recipe paths are read.

Recipes resolve as follows:

1. bare `apply <recipe>` resolves only `recipes/<recipe>/`
2. `apply <pack>:<recipe>` resolves `packs/<pack>/`
3. explicit `apply ./recipe.yml` runs a path-only single-file recipe
4. explicit `apply ./recipe-project` runs a local standalone recipe project
5. explicit `apply ./pack-project --recipe <recipe>` runs a local pack recipe

Hooks resolve in this order:

1. standalone recipe or pack project `pyproject.toml` entries under
   `[tool.untaped_recipe.hooks]`
2. global hook projects under `hooks/<name>/`
3. packaged built-ins registered in `src/untaped_recipe/builtins/registry.py`

Hook names in recipes must be safe logical names, not filesystem paths.
Recipes never declare hook runtimes. The resolver returns either a built-in
reference or a uv hook project reference with a declared hook kind.

`hook run <hook>` is not recipe step execution. It resolves explicit
`--project PATH` first, then the current working directory when it has hook
metadata, then global hooks, then built-ins. Keep it a single-hook, no-write
debug harness.

## Recipe Schema

V1 recipe YAML is behavior-only. Public identity comes from uv project metadata
or from the explicit file path stem for path-only files. Recipe YAML uses
`version: 1`, optional `description`, optional `inputs`, and `steps`. `name`
is not part of the recipe YAML schema and must be rejected as extra behavior
metadata. Step types are:

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

Input specs accept `type`, `default`, `required`, `description`, `sensitive`,
`scope`, and `from`; unknown input-spec keys are validation errors. `scope` is
`global` or `target`. Omitted scope infers `target` when `from` is present and
`global` otherwise. `scope: global` may use `--var`/`--vars`, but must reject
recipe `from` and CLI `--input-from`.

Per-target input `from` values are Jinja strings used only to derive scalar
input values. They may combine literal text, string/number/boolean/null
constants that Jinja parses without operators, and field access on `target` or
optional incoming pipe `record`. They must not affect recipe structure, paths,
hook names, or the existing template renderer. The sandboxed strict native
Jinja context contains `target.path`, `target.name`, `target.parent_path`,
`target.parent_name`, and optional `record`, with no ambient globals. Control
blocks, filters, tests, calls, operators, and collection literals are rejected;
negative numeric expressions like `{{ -1 }}` are not valid V1 sources. Missing,
undefined, or null candidate values fall through to the next candidate;
`false`, `0`, and `""` are real values. Oversized or non-scalar derived values
are rejected.

Input precedence is fixed value/source override first, then recipe `from`,
interactive prompt, recipe `default`, and required-input error. A fixed value
from `--var`/`--vars` and a source override from `--input-from` for the same
input is a usage error. `--interactive --check` is rejected.
`--stdin --interactive` must read target data from stdin and prompt only
through a controlling terminal.

`recipe.outcome` rows and backup file metadata include resolved declared
inputs only. Sensitive input values are redacted in rows, warnings/errors, and
backup metadata; file-level previews and diffs are suppressed for targets with
sensitive inputs. Real values still reach templates and hooks. Never copy the
full incoming pipe record into rows or backups.

## Hook Contracts

External hook projects are uv-managed directories with `pyproject.toml`,
`uv.lock`, package code under `src/`, and a hook metadata table. The same hook
table is used for global hook projects and for hooks local to a standalone
recipe or pack project:

```toml
[tool.untaped_recipe.hooks]
"ansible.add_play_collections" = { kind = "transform", module = "ansible_hooks.hooks.add_play_collections" }
```

`kind` is required and must be `transform` or `validate`; old hook rows that
only declare `module` are invalid and must be migrated. `recipe check` and
`pack check` reject recipe steps whose type does not match the resolved hook
kind.

External hooks run out-of-process through NDJSON stdin/stdout workers. Worker
stdout is protocol-only; hook `print()` output is redirected to stderr. The
worker uses stdlib wire parsing, and the engine validates worker responses
before using them. A bounded worker pool is created per hook project per apply
invocation, up to the clamped `--parallel` value; each worker serializes its own
requests. Hook request timeouts kill and retire the affected worker and must
surface as per-target planning failures rather than hanging the full batch.

`hook run` must reuse `HookResolver`, `HookExecutor`, `UvHookWorkerPool`, and
`HookHelpers`. Transform mode requires `--target` and `--file`, writes raw
transformed content to stdout by default, and can emit `--diff`; validate mode
emits a `recipe.hook_run` verdict record. Fixture context and hook diagnostics
belong on stderr. Successful external hook diagnostics stay discarded for
`apply`, but are surfaced by `hook run` with a 10 MiB per-invocation cap.

Built-ins use the direct registry and run in-process. Keep built-ins reserved
for engine-owned code such as `yaml_edit`.

External transform hooks expose:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def transform(
    content: str,
    *,
    inputs: dict,
    target: Path,
    file: Path,
    args: dict,
    helpers: "HookHelpers",
) -> str: ...
```

External validate hooks expose:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: "HookHelpers",
) -> dict | None | str: ...
```

Validate hooks may return a compatible verdict dict, `None` for pass, or a
string for fail. Prefer explicit `helpers.pass_()`, `helpers.warn()`, and
`helpers.fail()` in shipped examples. Built-in hooks may use the engine's
concrete `HookHelpers` and `Verdict` types directly.
External hook typing lives in `untaped_recipe.hook_api`. Hook projects may use
`untaped-recipe` as a dev dependency for editor discovery, but must not depend
on `untaped-recipe` at runtime. External uv workers run with `uv run --locked
--no-dev`, so hook runtime dependencies belong in `[project].dependencies`;
type-only authoring dependencies belong in `[dependency-groups].dev`. Hook
projects can declare `[tool.untaped_recipe].requires_hook_api` to fail fast
when the installed CLI's helper API is too old. The public
`untaped_recipe.hook_api.HookHelpers` protocol models external worker helpers,
where verdict helpers return dict-shaped verdicts. Keep the application-layer
`HookHelpersPort` separate for in-process built-ins, where verdict helpers
return `Verdict`. External `helpers.dump_yaml(data, options=...)` accepts plain
dict options for width, quote preservation, indent, block sequence indent, and
explicit document start/end; worker and in-process defaults are
`preserve_quotes=True` and `width=4096`. Unsupported option keys must be
rejected rather than ignored.

## Release Workflow

Use `.github/workflows/release.yml` for PyPI releases. The workflow publishes
`untaped-recipe` to TestPyPI or PyPI through Trusted Publishing, verifies
scaffold locking against the target index, and only creates the production
GitHub release/tag after PyPI verification passes. The production `pypi`
GitHub environment should be protected with required reviewers.
The SDK package `untaped` must be published to PyPI first; remove the temporary
`tool.uv.sources.untaped` git source and relock before publishing
`untaped-recipe`. The release workflow checks that `untaped>=2.4.0,<3` resolves
from PyPI before publishing `untaped-recipe`.

Do not manually create a GitHub release/tag for a version whose PyPI package has
not been published and verified. PyPI versions are permanently burned once
uploaded; if publish succeeds but post-publish verification never passes, bump
the package patch version before retrying. Bump `HOOK_API_VERSION` and the
derived `requires_hook_api` scaffold floor only when the helper contract changes.
See `docs/release.md` for the runbook.

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
