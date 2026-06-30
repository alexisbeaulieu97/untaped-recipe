# Hooks

Hooks are trusted local Python callables used by `validate` and `transform`
recipe steps. External hooks live in uv-managed hook projects and run
out-of-process. Built-ins are engine-owned modules and run in-process through a
direct registry.

Recipes only reference hook names:

```yaml
steps:
  - type: transform
    file: pyproject.toml
    hook: set_owner
```

Recipes do not declare runtimes. The resolver decides whether the hook is local
to the standalone recipe or pack project, a reusable global uv hook project, or
a built-in.

## Hook Project Layout

Create a global hook project with:

```bash
untaped-recipe hook init set_owner
```

The scaffolded project looks like:

```text
hooks/set_owner/
├── pyproject.toml
├── uv.lock
└── src/
    └── untaped_recipe_hooks_set_owner/
        └── hooks/
            └── set_owner.py
```

Hook metadata lives in `pyproject.toml`:

```toml
[tool.untaped_recipe.hooks]
"set_owner" = { kind = "transform", module = "untaped_recipe_hooks_set_owner.hooks.set_owner" }
```

The hook `kind` is required and must be either `transform` or `validate`.
Older manifests such as:

```toml
"set_owner" = { module = "untaped_recipe_hooks_set_owner.hooks.set_owner" }
```

must be migrated by adding the matching kind:

```toml
"set_owner" = { kind = "transform", module = "untaped_recipe_hooks_set_owner.hooks.set_owner" }
```

Recipe-local hooks use the same project shape inside a standalone recipe
project:

```text
recipes/add-config/
├── recipe.yml
├── pyproject.toml
├── uv.lock
└── src/add_config_hooks/hooks/set_owner.py
```

```bash
untaped-recipe recipe hook init add-config set_owner --kind validate
```

Pack-local hooks use the same top-level pack project:

```text
packs/ansible/
├── pyproject.toml
├── uv.lock
├── recipes/playbook-migration/recipe.yml
└── src/ansible_hooks/hooks/add_play_collections.py
```

```bash
untaped-recipe pack hook init ansible add_play_collections
```

Single-file recipes cannot contain local hooks; they can still use global hook
projects and built-ins.

## Resolution Order

For `hook: set_owner`, resolution checks:

1. the standalone recipe or pack project's `pyproject.toml`
2. `<library_root>/hooks/set_owner/pyproject.toml`
3. packaged built-ins such as `yaml_edit`

The hook key must exist in the project's `[tool.untaped_recipe.hooks]` table,
the hook row must declare a matching `kind`, and uv hook projects must have a
`uv.lock`. Missing or stale lockfiles fail planning for the affected target.
`recipe check` and `pack check` reject validate steps wired to transform hooks,
and transform steps wired to validate hooks, without importing or executing the
hook body.

For `untaped-recipe hook run <hook>`, resolution checks explicit
`--project PATH` first. Without `--project`, it uses the current working
directory when that directory has hook metadata, then global hooks, then
built-ins.

## Execution Model

External hook projects are launched with locked uv execution. During one
`apply`, the engine keeps a small worker pool per hook project. The pool can
start up to the clamped `--parallel` value for that project, and each individual
worker serializes its own requests safely.
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

Hook project code is trusted local code, but normal file mutation should still
go through returned transform content so preview, backups, and transactional
writes stay coherent.

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
API without making `untaped-recipe` a runtime dependency of the hook project.

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
- a `Verdict`-like object with `model_dump()` if the hook project chooses to
  depend on `untaped-recipe`

Warnings are recorded on the target plan. Failures abort that target before
any writes.

## Helpers

Worker helpers provide:

- `pass_`, `warn`, and `fail` verdict helpers, returning dict-shaped verdicts
  such as `{"status": "warn", "message": "..."}`.
- `render_template(template, inputs)` for simple `{{ name }}` placeholders.
- `load_yaml(content)` and `dump_yaml(data, options=None)`, which require
  `ruamel.yaml` in the hook project's dependencies.

Add hook-specific dependencies to the hook project's `pyproject.toml`, then run
`uv lock`. The engine always runs hook projects with `--locked`.

`dump_yaml` accepts ordinary dict options so hook projects do not need runtime
imports from `untaped-recipe`:

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

## Hook Library Commands

```bash
untaped-recipe hook list
untaped-recipe hook init set_owner
untaped-recipe hook run set_owner --target ./repo --file pyproject.toml --diff
untaped-recipe recipe hook init add-config set_owner --kind validate
untaped-recipe pack hook init ansible add_play_collections
untaped-recipe hook add ./my-hook-project --name set_owner
untaped-recipe hook show set_owner
untaped-recipe hook edit set_owner
untaped-recipe hook remove set_owner --yes
```

`hook add` copies project directories, not bare `.py` files. `hook show` and
`hook edit` open the module file when the supplied name matches a declared
hook; otherwise they show or edit `pyproject.toml`.

When adding a global hook project, the library directory is derived from the
declared hook name. A project declaring `set_owner` installs under
`hooks/set_owner/`. If `--name` is passed, it must match that derived name.
Declared hook modules must resolve to files under the project's `src/`
directory, matching the scaffolded layout. Use `./my-hook-project` or an
absolute path when adding/showing/editing a project from the current directory;
bare names resolve through the hook library.

## Hook Run Debugging

`hook run` invokes exactly one resolved hook without writing target files. It
uses the same `HookExecutor`, resolver, helpers, built-in in-process calls, and
external uv worker protocol as `apply`. An explicit `--project PATH` must point
at a hook project with hook metadata; it never falls through to global hooks or
built-ins when the path is missing or not a hook project.

Transform hooks require `--target DIR --file TARGET_RELATIVE_PATH`. Without a
content override, `hook run` reads the target file and writes exact transformed
content to stdout with no added newline:

```bash
untaped-recipe hook run set_owner --target ./repo --file pyproject.toml
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
template steps.

The core engine intentionally does not include a general YAML selector DSL in
v1. `yaml_edit` is a shipped transform hook for common YAML edits; write custom
uv hook projects for behavior outside that contract.
