# AGENTS.md - `untaped-recipe`

Single source of truth for this standalone CLI repo. If command behavior,
settings, recipe schema, hook contracts, architecture, or development
workflow changes, update this file and the packaged skill in the same change.

## Mission

`untaped-recipe` applies trusted local recipe packs and explicit recipe files
across one or more plain directories. It is intentionally VCS-agnostic: no
clone, branch, git diff, commit, push, or PR behavior belongs here. Workspace
selection can come from another tool through stdin or pipe records, but this
repo owns only recipe execution, previews, backups, and restore.

## Domain (invariant #0)

**untaped-recipe is a deterministic transformation engine over file trees — "moderne
for files, driven by hooks."** A recipe's scope is anything expressible as planned
file edits: version migrations, bulk config rewrites, scaffolding, drift checks. The
comparison that holds is OpenRewrite/moderne (transformation recipes, previewable at
scale), not Ansible (general task execution): "you could write a recipe for anything"
is bounded to *anything that is a file transformation*. This sentence wins every
future scope argument and joins AGENTS.md alongside the Wave-3 invariants.

Two consequences are load-bearing:

- **Truthful preview is the product.** Plan → preview → confirm → flush is only
  trustworthy while every step is a planned file mutation. Agent-authored packs are a
  first-class north-star use case precisely because the human reviews the plan, not
  the agent; anything that executed at apply time outside the plan buffer would make
  the preview lie.
- **Follow-up commands are data.** Real migrations often end with "now run `uv lock`"
  or "re-run the formatter". Recipes will be able to *declare* follow-ups; preview
  and outcome *display* them; recipe never executes them (model locked, design
  deferred — see Deferred designs).

**Never build** (recorded with reasons so future scope arguments end here):

- Exec/shell/API step types — they kill truthful preview; this is the boundary
  decision itself. "Ensure"-style capabilities enter as *planned* mutations resolved
  at planning time (see Deferred designs), never as execution-time convergence.
- Control flow in the recipe schema (`when:`, loops) — a decision is a hook.
- State, inventory, or remote execution — targets come from arguments and pipes.
- Hook sandboxing — packs are trusted code, on the same model as `pip install`; the
  mitigations are evaluate-before-trust surfaces (`show`, `check`'s AST scan, the
  `add` confirmation, the 0.10 harness), not a sandbox.

## Permanent Invariants

- AGENTS.md gains permanent invariants:
  1. Control flow never enters the recipe schema; a decision is a hook.
  2. Planning is the only execution; writes are a flush of the plan buffer.
  3. No state, no inventory; targets come from arguments and pipes.
  4. Builtins stay minimal; typed uv hook packs are the extension story.
  5. The hook boundary stays pure data (stdlib-only worker, JSON protocol, no runtime
     import of untaped-recipe in hooks).
  6. Pipe composability is a protected feature: `apply --stdin` ingests untaped
     NDJSON envelopes (kind tags, `target_path`), and input `from` expressions can
     read the piped `record` — other untaped tools' output drives recipes.
  7. Hooks are pure at planning time: they may read the target tree and their own
     pack directory (deterministic inputs), and must never write, reach the network,
     or read outside those roots. Planning's truth depends on it; the 0.10 harness
     catches violations as unstable goldens.
  (AGENTS.md also carries invariant #0, the domain lock from §Domain.)

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
8. External Python hooks are trusted local uv pack hooks executed by pooled
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
├── cli/                 # Cyclopts commands, output rows, preview rendering, test_commands.py
├── application/         # apply/check/golden-test harness use cases, hook run, ports
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
packs/
packs.toml
backups/
```

Installed library entries are uv pack projects under `packs/<pack-id>/`.
`packs.toml` records source path or git URL, rev, and installed version. The
installed key is the pack identity everywhere: refs, `list`, `check`,
`remove`, ambiguity messages, and output rows.

Pack identity comes from top-level `[project].name`, not from `recipe.yml`.
Project names may use the `untaped-recipe-` prefix; the public pack name drops
that prefix. A pack may expose zero or more recipe paths and zero or more hook
modules:

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

Nested uv projects or uv workspaces inside a pack are opaque to this tool. Only
the top-level project metadata and declared recipe paths are read.

Recipes resolve as follows:

1. bare `apply <recipe>` resolves an installed pack recipe only when unique
2. `apply <pack>/<recipe>` resolves an installed pack recipe
3. explicit `apply ./recipe.yml` runs a path-only single-file recipe
4. explicit `apply ./pack-project --recipe <recipe>` runs a local pack recipe

For `apply`, a path must be explicit: it starts with `./`, `../`, `/`, or `~`,
or ends in `.yml`/`.yaml`. Anything else, including `a/b`, is a library ref and
must not be classified by on-disk existence.

Hooks resolve in this order:

1. the recipe's own pack project
2. installed packs
3. packaged built-ins registered in `src/untaped_recipe/builtins/registry.py`

Hook names in recipes must be safe logical names, not filesystem paths.
Recipes never declare hook runtimes. The resolver returns either a built-in
reference or a uv pack hook reference with exported hook verbs.

`hook run <hook>` is not recipe step execution. It resolves explicit
`--project PATH` first, then installed packs, then built-ins. Keep it a
single-hook, no-write debug harness.

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
  `unknown_tokens` is `error` by default and may be `keep` only for templates
  that intentionally emit other tools' template syntax.
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

## Testing Packs

`test [pack|path|pack/recipe]` mirrors `check`'s grammar and runs golden-fixture
cases stored inside packs at `tests/<recipe>/<case>/`:

- `given/` is the single fixture target directory; the plan runs against a temp
  copy named after the case. Fixtures and packs are never written by a test run.
- `expected/` is the full expected tree after the plan; extra, missing, and
  changed files all fail. Omitting it asserts the plan makes no changes.
- `case.yml` is optional data-only config: `inputs`, `expect: success|error`,
  `error_contains` (required with `expect: error`), and `verdict` (`status` is
  the expected worst produced verdict; `message_contains` matches any verdict
  message). No assertion language beyond this exists or will exist; logic in
  tests is pytest's job at the hook level.
- Planning is the only execution: the harness runs the same planner as `apply`
  with the normal hook resolution order. `--update` regenerates `expected/`,
  deleting it when the plan is empty, requires an explicit pack or recipe
  argument, and rejects `expect: error` cases.
- One `recipe.test` record per case (`pack`, `recipe`, `case`, `status`,
  `detail`) is emitted on stdout. Unified diffs per mismatched file and a
  summary line go to stderr. Exit 1 on any fail/error, including "no test cases
  found" for an explicitly named pack or recipe; bare `test` reports packs
  without tests but does not fail on them.
- `check` fails a pack whose `tests/` contains a directory naming no manifest
  recipe; `test` also reports such directories as error rows.
- `new recipe` scaffolds `tests/<recipe>/basic/` with an empty `given/` and a
  fully commented `case.yml`.

## Hook Contracts

External hook code lives in uv-managed pack directories with `pyproject.toml`,
`uv.lock`, package code under `src/`, and a hook metadata table:

```toml
[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

The exported function name is the contract: a module exports `transform()`,
`validate()`, or both. Recipe step `type` selects which function runs. `check`
must keep its no-import guarantee by AST-scanning resolved module files for
`def transform` and `def validate`, rejecting steps wired to a hook that does
not export the required function.

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
belongs on stderr. If a hook exports both functions, `--file` implies
transform; otherwise `--kind` is required. Successful external hook diagnostics
stay discarded for `apply`, but are surfaced by `hook run` with a 10 MiB
per-invocation cap.

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
projects declare `[tool.untaped_recipe].requires_hook_api = ">=0.9,<1"` to fail
fast when the installed CLI's helper API is incompatible. The public
`untaped_recipe.hook_api.HookHelpers` protocol models external worker helpers,
where verdict helpers return dict-shaped verdicts. Keep the application-layer
`HookHelpersPort` separate for in-process built-ins, where verdict helpers
return `Verdict`. External `helpers.render_template(template, inputs,
unknown_tokens="error")` is strict by default; `unknown_tokens="keep"` is the
escape hatch for GitHub Actions, Helm, and other nested template syntaxes.
External `helpers.dump_yaml(data, options=...)` accepts plain dict options for
width, quote preservation, indent, block sequence indent, and explicit document
start/end; worker and in-process defaults are `preserve_quotes=True` and
`width=4096`. Unsupported option keys must be rejected rather than ignored.

## Release Workflow

Use `.github/workflows/release.yml` for PyPI releases. The workflow publishes
`untaped-recipe` to TestPyPI or PyPI through Trusted Publishing, verifies
scaffold locking against the target index, and only creates the production
GitHub release/tag after PyPI verification passes. The production `pypi`
GitHub environment should be protected with required reviewers.
The SDK package `untaped` must be published to PyPI first. The release workflow
checks that `untaped>=3.0.0,<4` resolves from PyPI before publishing
`untaped-recipe`.

Do not manually create a GitHub release/tag for a version whose PyPI package has
not been published and verified. PyPI versions are permanently burned once
uploaded; if publish succeeds but post-publish verification never passes, bump
the package patch version in root `pyproject.toml` and
`src/untaped_recipe/_version.py` before retrying. Bump `HOOK_API_VERSION` and
the derived `requires_hook_api` scaffold floor only when the helper contract
changes. See `docs/release.md` for the runbook.

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
