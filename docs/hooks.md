# Hooks

Hooks are trusted Python callables used by `validate` and `transform` recipe
steps. Pack-local hooks live in uv-managed pack projects and run
out-of-process through the worker protocol. Built-ins are engine-owned modules
and run in-process through the direct registry.

Recipes only reference hook names:

```yaml
steps:
  - type: transform
    file: pyproject.toml
    hook: set_owner
```

Recipes do not declare runtimes. The resolver checks the recipe's own pack,
installed packs, then built-ins.

## Hook Contract

The exported function name is the contract. A hook module exports
`transform()`, `validate()`, or both. The recipe step `type` selects which
function runs.

```toml
[tool.untaped_recipe.hooks]
"set_owner" = { module = "service_hooks.hooks.set_owner" }
```

Hook manifest rows do not contain `kind`. `check` keeps its no-import guarantee
by AST-scanning the resolved module file for `def transform` and
`def validate`; it rejects a recipe step wired to a hook that does not export
the required function.

Dual-verb hooks are first-class. A validate/fix pair can share parsing logic in
one module under one public name:

```python
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: "HookHelpers",
) -> dict[str, str]:
    if not (target / "pyproject.toml").is_file():
        return helpers.fail("missing pyproject.toml")
    return helpers.pass_()


def transform(
    content: str,
    *,
    inputs: dict,
    target: Path,
    file: Path,
    args: dict,
    helpers: "HookHelpers",
) -> str:
    return content.replace("OWNER", str(args["owner"]))
```

For `hook run`, if the module exports one function, that function runs. If it
exports both, `--file` implies transform; otherwise pass `--kind transform` or
`--kind validate`.

## Pack Layout

Create a pack and add a hook:

```bash
untaped-recipe new pack ansible
untaped-recipe new hook ansible/add_play_collections
```

The pack layout is a normal uv project:

```text
ansible/
├── pyproject.toml
├── uv.lock
└── src/
    └── ansible_hooks/
        └── hooks/
            └── add_play_collections.py
```

The manifest pins the hook API floor and exposes hook modules:

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = ["untaped-recipe>=0.9"]

[tool.untaped_recipe]
requires_hook_api = ">=0.9,<1"

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

`untaped_recipe.hook_api` gives editors and type checkers the `HookHelpers`
protocol and YAML option types. Keep `untaped-recipe` in
`[dependency-groups].dev`; do not add it to `[project].dependencies`. The
installed CLI owns the worker and helper implementation, and workers run with
`uv run --locked --no-dev`.

Add packages imported by hook code at runtime to `[project].dependencies`, then
run `uv lock`.

## Resolution

For `hook: add_play_collections`, resolution checks:

1. the recipe's own pack project,
2. installed packs,
3. packaged built-ins such as `yaml_edit`

Bare hook names are accepted only when they resolve uniquely. Use qualified
`pack/name` refs when multiple packs expose the same hook name:

```bash
untaped-recipe hook run ansible/add_play_collections --target ./repo --file site.yml --diff
```

For `hook run`, `--project PATH` points at an explicit local pack project and
does not fall through to installed packs or built-ins when invalid.

## Execution Model

External pack hooks are launched with locked uv execution:
`uv run --project <pack-project> --locked --no-dev python <worker>`. During one
`apply`, the engine keeps a small worker pool per hook project. The pool can
start up to the clamped `--parallel` value for that project, and each worker
serializes its own requests. Put packages imported by hook code at runtime in
`[project].dependencies`; dev-only dependencies are intentionally not installed
into workers.
Each hook request has a timeout controlled by `recipe.hook_timeout_seconds`
or `apply --hook-timeout`; the default is 60 seconds and `0` disables the
timeout. Timed-out workers are killed, retired from the pool, and reported as
planning failures for the affected targets.

The worker protocol is newline-delimited JSON over stdin/stdout. Worker stdout
is protocol-only. Hook `print()` output is redirected to stderr, and stderr is
used as bounded diagnostics when a worker request fails. Successful request
diagnostics are discarded during `apply` so chatty hooks do not grow memory
during bulk runs. `hook run` captures successful hook diagnostics and prints
them to stderr for debugging, capped at 10 MiB per hook invocation.
Engine-side Pydantic models validate worker responses before any file changes
are accepted into a plan.

Hooks are trusted code, but hook behavior must still be pure at planning time:
they may read the target tree and their own pack directory, but must not write
files, reach the network, or read outside those roots. Normal file mutation
must go through returned transform content so preview, backups, and
transactional writes stay truthful.

## Transform Hooks

Transform hooks receive the current file content and return replacement
content. They should not write files directly.

```python
from pathlib import Path
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
) -> str:
    owner = args["owner"]
    return content.replace("OWNER", str(owner))
```

`target` and `file` are rebuilt as `Path` objects in the worker. `helpers` is a
small worker helper object. The `TYPE_CHECKING` import gives editors the helper
API without importing anything at hook runtime; type checkers resolve it from
the `untaped-recipe` dev dependency.

## Validate Hooks

Validate hooks inspect a target and return a verdict.

```python
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: "HookHelpers",
) -> dict[str, str]:
    if not (target / "pyproject.toml").is_file():
        return helpers.fail("missing pyproject.toml")
    return helpers.pass_()
```

Accepted return values:

- compatible verdict dict, such as `{"status": "warn", "message": "..."}`
- `None` for pass
- string for fail
- a local verdict-like object with `model_dump()`

Warnings are recorded on the target plan. Failures abort that target before
any writes.

## Helpers

Worker helpers provide:

- `pass_`, `warn`, and `fail` verdict helpers, returning dict-shaped verdicts
  such as `{"status": "warn", "message": "..."}`.
- `render_template(template, inputs, unknown_tokens="error")` for simple
  `{{ name }}` placeholders.
- `load_yaml(content)` and `dump_yaml(data, options=None)`, which require
  `ruamel.yaml` in the hook project's dependencies.

Add hook-specific dependencies to the hook project's `pyproject.toml`, then run
`uv lock`. The engine always runs hook projects with `--locked --no-dev`, so
only `[project].dependencies` are available to hook code at runtime.

`render_template` replaces known bare input names. By default, unknown bare
names and non-bare `{{ ... }}` tokens raise. Pass `unknown_tokens="keep"` to
preserve unknown tokens such as GitHub Actions `${{ github.ref }}` or Helm
`{{ .Values.x }}` while still rendering known inputs.

`dump_yaml` accepts ordinary dict options so hook projects do not need runtime
imports from the engine:

```python
data = helpers.load_yaml(content)
return helpers.dump_yaml(
    data,
    options={
        "width": 120,
        "preserve_quotes": True,
        "indent": {"mapping": 2, "sequence": 4, "offset": 2},
        "block_seq_indent": 2,
        "explicit_start": False,
        "explicit_end": False,
    },
)
```

Defaults are `preserve_quotes=True` and `width=4096` for both in-process
built-ins and external workers. Omitted options use ruamel's defaults for that
setting. Unsupported option keys and unsupported nested `indent` keys are
rejected. `load_yaml` has no formatting options.

## Hook Run Debugging

`hook run` invokes exactly one resolved hook without writing target files. It
uses the same `HookExecutor`, resolver, helpers, built-in in-process calls, and
external uv worker protocol as `apply`. An explicit `--project PATH` must point
at a pack project with hook metadata; it never falls through to installed packs
or built-ins when the path is missing or not a hook project.

Transform hooks require `--target DIR --file TARGET_RELATIVE_PATH`. Without a
content override, `hook run` reads the target file and writes exact transformed
content to stdout with no added newline:

```bash
untaped-recipe hook run ansible/set_owner --target ./repo --file pyproject.toml
```

Use `--content TEXT`, `--content -`, or `--content-file PATH` to pass fixture
content while still giving the hook the requested target-relative `file` path.
With a content override, the target file does not need to exist. Use `--diff`
to write a unified input-to-output diff to stdout instead of raw content.

Validate hooks require `--target DIR` and reject `--file`, content options, and
`--diff`. They emit one `recipe.hook_run` verdict record by default and exit
non-zero when the verdict status is `fail`.

Both hook kinds accept `--inputs file.yml` and `--args file.yml` YAML mapping
files. Repeated `--input KEY=VALUE` and `--arg KEY=VALUE` overrides are
YAML-parsed and take precedence over file values, so `--input enabled=yes`
passes a boolean and `--arg count=3` passes an integer. Quote values that should
stay strings when YAML would coerce them.

By default, `hook run` prints resolved target, file, inputs, args, and hook
diagnostics to stderr. The SDK `--quiet` flag suppresses the resolved context
messages but not hook diagnostics or errors. This context echo includes the
ad-hoc fixture values passed on the command line or loaded from fixture files,
so use `--quiet` in shared terminals when those values are sensitive. Structured
`--format json|yaml|table|pipe` emits `recipe.hook_run` on stdout. Transform
records include `content` and include `diff` when `--diff` is passed; structured
records omit raw input and arg values.

## Built-In YAML Hook

`yaml_edit` applies mapping and list-item edits while preserving comments,
quotes, and order where `ruamel.yaml` can round-trip them. It is a built-in and
does not start a uv worker.

```yaml
steps:
  - type: transform
    file: config.yml
    hook: yaml_edit
    args:
      unknown_tokens: keep
      edits:
        - op: merge
          path: [services, {where: {name: api}}, config]
          value:
            owner: "{{ owner }}"
            timeout: 30
        - op: set
          path: [jobs, {where: {id: build}}, permissions]
          value:
            contents: read
            packages: write
        - op: delete
          path: [legacy]
```

Each edit uses `op: set|merge|delete`. Path segments are mapping keys, list
indexes (`{index: 0}`), or first-match list selectors
(`{where: {name: api}}`). String values use the same `{{ input }}` renderer as
template steps and can opt into `unknown_tokens: keep` at the hook args level.

The core engine intentionally does not include a general YAML selector DSL in
v1. `yaml_edit` is the lone built-in transform hook for common YAML edits;
write custom uv pack hooks for behavior outside that contract.
