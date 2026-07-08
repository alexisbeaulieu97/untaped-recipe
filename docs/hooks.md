# Hooks

Hooks are the trusted Python callables that back the `validate` and `transform`
recipe steps (see [recipes](./recipes.md) for which step types call a hook and
how). This page covers the hook contract, how names resolve, the out-of-process
execution model, the helper API, the `hook run` debugging command, and the
built-in `yaml_edit` hook.

Hooks come in two flavors. Pack-local hooks live in uv-managed pack projects and
run out-of-process through a worker protocol. Built-ins are engine-owned modules
and run in-process through a direct registry. Recipes only ever reference hook
names:

```yaml
steps:
  - type: transform
    file: pyproject.toml
    hook: set_owner
```

Recipes do not declare runtimes. The resolver decides where a name lives by
checking the recipe's own pack, then installed packs, then built-ins.

## Hook contract

The exported function name is the contract. A hook module exports `transform()`,
`validate()`, or both. The recipe step `type` selects which function runs. Hook
manifest rows do not carry a `kind` — the metadata declares only the module (the
manifest format itself belongs to [packs](./packs.md)):

```toml
[tool.untaped_recipe.hooks]
"set_owner" = { module = "service_hooks.hooks.set_owner" }
```

`check` keeps its no-import guarantee by AST-scanning the resolved module file
for `def transform` and `def validate`; it rejects a recipe step wired to a hook
that does not export the required function. See [testing](./testing.md) for the
full `check` contract.

### Signatures

Transform hooks receive the current file content and return replacement content:

```python
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def transform(
    content: str,
    *,
    inputs: dict[str, object],
    target: Path,
    file: Path,
    args: dict[str, object],
    helpers: "HookHelpers",
) -> str:
    return content.replace("OWNER", str(args["owner"]))
```

Validate hooks inspect a target and return a verdict:

```python
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers


def validate(
    *,
    inputs: dict[str, object],
    target: Path,
    args: dict[str, object],
    helpers: "HookHelpers",
) -> object:
    if not (target / "pyproject.toml").is_file():
        return helpers.fail("missing pyproject.toml")
    return helpers.pass_()
```

`target` (and, for transforms, `file`) are rebuilt as `Path` objects inside the
worker. The `TYPE_CHECKING` import gives editors and type checkers the
`HookHelpers` protocol without importing anything at hook runtime.

### Verdict return values

The validate verdict vocabulary is **pass / fail / skip**:

| Return | Meaning |
| --- | --- |
| `helpers.pass_(message="")` | target is applicable and passes |
| `helpers.fail(message)` | target fails; planning aborts it before any writes |
| `helpers.skip(message="")` | target is **not applicable**; planning stops for it, status becomes `skipped`, and it is never counted as a failure |
| `None` | treated as pass |
| a plain string | treated as fail with that message |

`helpers.skip` is the tri-state for out-of-scope targets — use it instead of
`fail` when the recipe simply does not apply (an apply exits 0 when every
non-skipped target succeeds).

### Warnings

`helpers.warn(message)` is a **warning accumulator**, callable any number of
times from **validate and transform** hooks. Warnings attach to the target plan
(the per-target warnings channel, the `recipe.outcome` `warnings` column, and
sensitive-input redaction) and never change the verdict:

```python
def validate(*, inputs, target, args, helpers) -> object:
    if _legacy_layout(target):
        helpers.warn("legacy layout detected")
    return helpers.pass_()
```

> **Deprecated:** a legacy `{"status": "warn", ...}` verdict dict (or an old
> hook that returns `helpers.warn(...)` as its verdict) is still accepted for
> this release and mapped to **pass + an accumulated warning**. Switch to
> calling `helpers.warn(...)` for its side effect and returning a pass/fail/skip
> verdict.

A validate hook may also return a local verdict-like object exposing
`model_dump()`. Prefer the explicit `helpers.pass_()`, `helpers.fail()`, and
`helpers.skip()` helpers over hand-rolled dicts.

### Dual-verb hooks

A validate/fix pair can share parsing logic in one module under one public name.
The recipe step `type` picks the verb at run time:

```python
def validate(*, inputs, target, args, helpers) -> object:
    if not (target / "pyproject.toml").is_file():
        return helpers.fail("missing pyproject.toml")
    return helpers.pass_()


def transform(content, *, inputs, target, file, args, helpers) -> str:
    return content.replace("OWNER", str(args["owner"]))
```

### Inputs and args

Hooks receive resolved recipe inputs as `dict[str, object]`. Structured `list`
and `dict` inputs arrive as native Python values, not templated strings — read
them directly from `inputs` (see [inputs](./inputs.md) for input types and
[templating](./templating.md) for the `{{ name }}` token language). Because
[recipes](./recipes.md) pass hook `args` verbatim, the engine never templates
them: if a hook accepts templated string args, call `helpers.render_template()`
inside the hook and choose the unknown-token policy explicitly.

## Authoring a pack hook

Hook code lives in a normal uv pack project. Scaffolding a hook writes a typed
stub, the `TYPE_CHECKING` import, and a direct-call pytest for you; see
[packs](./packs.md) for the `new pack` / `new hook` commands and the lock
mechanics. The generated tree is a standard uv project:

```text
ansible/
├── pyproject.toml
├── uv.lock
└── src/
    └── ansible_hooks/
        └── hooks/
            └── add_play_collections.py
```

The stub already exports a working function (a transform returns its content
unchanged; a validate returns `helpers.pass_()`) so the pack passes `check`
before you fill it in. The paired `tests/test_hook_<name>.py` calls the exported
function directly — hooks are pure functions, so no worker is needed in unit
tests (see [testing](./testing.md) for the hook test harness).

### `hook_api`, the dev dependency, and `requires_hook_api`

The manifest pins the hook API floor and exposes hook modules:

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = ["untaped-recipe>=0.10"]

[tool.untaped_recipe]
requires_hook_api = ">=0.10,<1"

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Rules for the dev dependency and the import surface:

- `untaped_recipe.hook_api` gives editors and type checkers the `HookHelpers`
  protocol and the YAML option types.
- Keep `untaped-recipe` in `[dependency-groups].dev`; do **not** add it to
  `[project].dependencies`.
- The installed CLI owns the worker and helper implementation, and workers run
  with `uv run --locked --no-dev`, so the dev group is never installed into a
  worker.

`requires_hook_api` is a compatibility floor:

- The engine fails fast when the installed helper API is older than the pack
  requires, before running any hook.
- It tracks the **hook API contract**, not the CLI release cadence — the helper
  API changes rarely, so this floor moves independently of `untaped-recipe`
  version bumps.
- The scaffolded floor derives from the engine's `HOOK_API_VERSION` (currently
  `0.10.0`), which is why both the manifest floor and the dev dependency read
  `0.10` rather than the current CLI version.

Add packages that hook code imports at runtime to `[project].dependencies`, then
run `uv lock`. Dev-only dependencies are intentionally not available to hook
code at run time.

## Resolution

For `hook: add_play_collections`, resolution checks, in order:

1. the recipe's own pack project,
2. installed packs in the library,
3. packaged built-ins such as `yaml_edit`.

Bare hook names resolve only when they are unique across those sources. Use a
qualified `pack/name` reference when more than one pack exposes the same hook
name; a qualified name resolves against the library only. See [packs](./packs.md)
for the full reference grammar.

For `hook run`, `--project PATH` points at an explicit local pack project and is
searched before installed packs. It must point at a pack that actually declares
hook metadata; it never falls through to installed packs or built-ins when the
path is missing or is not a hook project.

## Execution model

External pack hooks are launched with locked uv execution:

```text
uv run --project <pack-project> --locked --no-dev python <worker>
```

### Worker pool

During one `apply`, the engine keeps a small worker pool **per hook project**.
The pool may start up to the clamped `--parallel` value for that project, and
each worker serializes its own requests. Because workers run `--no-dev`, only
`[project].dependencies` are importable at hook run time.

### Timeouts

Two independent bounds cover a hook request and the worker's environment
startup:

| Setting | Default | What it bounds |
| --- | --- | --- |
| `hook_timeout_seconds` (`apply --hook-timeout`) | `60` seconds, `0` disables | Each individual hook request |
| `hook_startup_timeout_seconds` | `300`, `0` = unbounded | Worker environment startup — uv creating or syncing the pack env on first use, plus module imports |

A timed-out worker is killed, retired from the pool, and reported as a planning
failure for the affected targets rather than hanging the whole batch. Startup
emits a `preparing hook environment for ...` notice on stderr, so a cold cache
or a lagging package index does not count against the per-hook timeout. If the
startup bound itself expires, the worker is shut down and the failure names the
project with `hook environment for <project> not ready after <N>s`, suggesting a
higher `hook_startup_timeout_seconds` or pre-running `uv sync` in the pack. See
[reference](./reference.md) for these settings and [apply](./apply.md) for
`--hook-timeout` and `--parallel` on the command line.

### Worker protocol

The worker protocol is newline-delimited JSON over stdin/stdout, after a
one-line ready handshake that marks the end of environment startup. Worker
stdout is protocol-only: hook `print()` output is redirected to stderr, and
stderr is captured as bounded diagnostics when a request fails. Successful
request diagnostics are discarded during `apply` so chatty hooks do not grow
memory during bulk runs; `hook run` instead surfaces successful diagnostics for
debugging (capped at 10 MiB per invocation). Engine-side Pydantic models
validate every worker response before any file change is accepted into a plan.

### Planning-time purity

Hooks are trusted code, but hook behavior must stay **pure at planning time**.
A hook may read the target tree and its own pack directory, but must not write
files, reach the network, or read outside those roots. All file mutation must
flow through returned transform content so preview, backups, and transactional
writes stay truthful.

## Helpers

The `helpers` argument is a small worker helper object, built fresh per hook
invocation. It provides:

- `pass_(message="")`, `fail(message)`, and `skip(message="")` verdict helpers
  (see [Verdict return values](#verdict-return-values)).
- `warn(message)`, the warning accumulator (see [Warnings](#warnings)); callable
  from validate and transform hooks, it records a warning and returns nothing.
- `render_template(template, inputs, unknown_tokens="error")` for simple
  `{{ name }}` placeholders.
- `load_yaml(content)` and `dump_yaml(data, options=None)`, which require
  `ruamel.yaml` in the pack's `[project].dependencies`.

### `render_template`

`render_template` replaces known bare input names. By default, unknown bare
names and any non-bare `{{ ... }}` token raise. Pass `unknown_tokens="keep"` to
preserve tokens you do not own — GitHub Actions `${{ github.ref }}` or Helm
`{{ .Values.x }}` — while still rendering known inputs. Structured input values
are not valid render targets; read lists and mappings directly from `inputs`
instead. The token language itself is described in [templating](./templating.md).

### `load_yaml` / `dump_yaml`

`dump_yaml` accepts ordinary dict options, so hook projects never need runtime
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

Defaults are `preserve_quotes=True` and `width=4096`, for both in-process
built-ins and external workers. Omitted options fall back to ruamel's own
defaults for that setting. Unsupported option keys and unsupported nested
`indent` keys are rejected. `load_yaml` has no formatting options.

## Debugging with `hook run`

`hook run` invokes exactly one resolved hook without writing target files. It
uses the same resolver, executor, helpers, in-process built-in calls, and
external uv worker protocol as `apply`, so what you see here is what `apply`
does. Which verb runs follows the module's exports:

- one function exported → that function runs;
- both exported → `--file` implies transform, otherwise pass `--kind transform`
  or `--kind validate`.

The hook ref accepts the same `./pack/hook` path form `new hook` accepts: it
resolves as `--project ./pack` plus the trailing hook name. An explicit
`--project PATH` keeps precedence and combining it with a path-form ref is a
usage error. `--project PATH` must point at a pack project with hook metadata
and never falls through to installed packs or built-ins.

### Transform mode

```bash
untaped-recipe hook run ansible/set_owner --target ./repo --file pyproject.toml
```

Transform hooks require `--target DIR --file TARGET_RELATIVE_PATH`. Without a
content override, `hook run` reads the target file and writes the exact
transformed content to stdout with no added newline. Use `--content TEXT`,
`--content -` (stdin), or `--content-file PATH` to pass fixture content while
still giving the hook the requested target-relative `file` path; with a content
override the target file need not exist. Add `--diff` to write a unified
input-to-output diff to stdout instead of raw content.

### Validate mode

```bash
untaped-recipe hook run ansible/set_owner --target ./repo --kind validate
```

Validate hooks require `--target DIR` and reject `--file`, the content options,
and `--diff`. They emit one `recipe.hook_run` verdict record whose `status` is
`pass`, `fail`, or `skip`, and exit non-zero only when the status is `fail`
(a `skip` exits 0). Accumulated `helpers.warn(...)` messages print to stderr.

### Inputs, args, and output

Both kinds accept `--inputs file.yml` and `--args file.yml` YAML mapping files.
Repeated `--input KEY=VALUE` and `--arg KEY=VALUE` overrides are YAML-parsed and
take precedence over file values, so `--input enabled=yes` passes a boolean and
`--arg count=3` passes an integer. Quote values that should stay strings when
YAML would coerce them.

By default `hook run` prints the resolved target, file, inputs, args, and hook
diagnostics to stderr. The SDK `--quiet` flag suppresses the resolved-context
messages but not hook diagnostics or errors. That context echo includes the
fixture values passed on the command line or loaded from files, so prefer
`--quiet` in shared terminals when those values are sensitive.
`--format json|yaml|table|pipe` emits the `recipe.hook_run` record on stdout;
transform records include `content`, and include `diff` when `--diff` is passed,
while structured records omit raw input and arg values. See [pipes](./pipes.md)
for the envelope and record schema.

## Built-in `yaml_edit`

`yaml_edit` applies mapping and list-item edits while preserving comments,
quotes, and order wherever `ruamel.yaml` can round-trip them. It is a built-in
and runs in-process, so it never starts a uv worker.

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

Each edit names one `op`:

| `op` | Effect |
| --- | --- |
| `set` | Assign `value` at `path`, creating intermediate containers. |
| `merge` | Shallow-update the mapping at `path` with `value` (both must be mappings). |
| `delete` | Remove the item at `path`; the path must already exist. |
| `ensure` | Idempotently guarantee `value` is present at `path` (list membership or set-if-absent). |

Path segments are one of:

- a mapping key (a bare string),
- a list index, written `{index: 0}`,
- a first-match list selector, written `{where: {name: api}}`, which matches the
  first list item whose fields all equal the given values.

String values inside `value` use the same `{{ input }}` renderer as template
steps and honor `unknown_tokens: keep` when it is set at the hook `args` level.

### `ensure`

`ensure` adds a value only if it is not already present, so re-running a recipe
never duplicates entries. **When nothing needs to change, the file is returned
byte-identical** — no reformatting of untouched files.

```yaml
- op: ensure
  path: [collections]
  value: {name: acme.required}
  match: [name]          # optional; see below
```

Behavior depends on what `path` resolves to:

| `path` resolves to | `value` | Presence test | When absent |
| --- | --- | --- | --- |
| a **list** | scalar | value equality (`match` forbidden) | append the scalar verbatim |
| a **list** | mapping + `match` | any entry whose `match`-key values all equal `value`'s | append the mapping verbatim |
| a **list** | mapping, no `match` | whole-mapping equality | append the mapping verbatim |
| a **mapping** | mapping (`match` forbidden) | per key | set only keys the mapping lacks (shallow set-if-absent); existing keys untouched |

More rules:

- In mixed string/mapping lists, string entries participate **only** in
  scalar-equality matching — a mapping `value` never matches a string entry.
- A missing container is created, typed by `value` (a list ensure on a missing
  path creates the list with the one entry).
- Appends are **verbatim** (no style inference): a flow-style list stays flow,
  and the appended value keeps the shape you authored — pick the value shape
  your fleet wants.
- Load errors: a scalar `value` with `match`, a mapping `path` with `match`, and
  a list `value` are all rejected.

`ensure` uses the same dump options as the other ops (it adds no formatting
knobs), and its string values honor the same `{{ input }}` / `unknown_tokens`
policy as `set`/`merge`.

The engine intentionally ships no general YAML selector DSL in v1. `yaml_edit`
is the lone built-in transform hook, meant for common YAML edits; write a custom
uv pack hook for anything outside that contract.
