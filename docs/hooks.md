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

Recipes do not declare runtimes. The resolver decides whether the hook is a
recipe-local uv project hook, a global uv project hook, a namespaced pack hook,
or a built-in.

## Hook Project Layout

Create a global hook project with:

```bash
untaped-recipe hook init set_owner
untaped-recipe hook init ansible.add_play_collections
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
"set_owner" = { module = "untaped_recipe_hooks_set_owner.hooks.set_owner" }
```

Namespaced packs live under the namespace directory and use dotted public hook
names:

```text
hooks/ansible/
├── pyproject.toml
├── uv.lock
└── src/ansible_hooks/hooks/add_play_collections.py
```

```toml
[tool.untaped_recipe.hooks]
"ansible.add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Then recipes reference `hook: ansible.add_play_collections`.

Recipe-local hooks use the same project shape in a recipe project directory:

```text
recipes/add-config/
├── recipe.yml
├── pyproject.toml
├── uv.lock
└── src/add_config_hooks/hooks/set_owner.py
```

Single-file recipes cannot contain recipe-local hooks; they can still use
global hook projects and built-ins.

## Resolution Order

For `hook: set_owner`, resolution checks:

1. the recipe project's `pyproject.toml`
2. `<library_root>/hooks/set_owner/pyproject.toml`
3. packaged built-ins such as `yaml_edit`

For `hook: ansible.add_play_collections`, global resolution checks
`<library_root>/hooks/ansible/pyproject.toml`.

The hook key must exist in the project's `[tool.untaped_recipe.hooks]` table,
and uv hook projects must have a `uv.lock`. Missing or stale lockfiles fail
planning for the affected target.

## Execution Model

External hook projects are launched with locked uv execution. During one
`apply`, the engine keeps one worker process per hook project and serializes
requests to that worker. Parallel target planning can reuse the same worker
safely.

The worker protocol is newline-delimited JSON over stdin/stdout. Worker stdout
is protocol-only. Hook `print()` output is redirected to stderr, and stderr is
used as diagnostics when a worker request fails. Engine-side Pydantic models
validate worker responses before any file changes are accepted into a plan.

Hook project code is trusted local code, but normal file mutation should still
go through returned transform content so preview, backups, and transactional
writes stay coherent.

## Transform Hooks

Transform hooks receive the current file content and return replacement
content. They should not write files directly.

```python
from pathlib import Path


def transform(
    content: str,
    *,
    inputs: dict,
    target: Path,
    file: Path,
    args: dict,
    helpers: object,
) -> str:
    owner = args["owner"]
    return content.replace("OWNER", str(owner))
```

`target` and `file` are rebuilt as `Path` objects in the worker. `helpers` is a
small worker helper object; hook projects do not need to depend on host
Pydantic or import engine models.

## Validate Hooks

Validate hooks inspect a target and return a verdict.

```python
from pathlib import Path


def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: object,
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

- `pass_`, `warn`, and `fail` verdict helpers.
- `render_template(template, inputs)` for simple `{{ name }}` placeholders.
- `load_yaml(content)` and `dump_yaml(data)`, which require `ruamel.yaml` in the
  hook project's dependencies.

Add hook-specific dependencies to the hook project's `pyproject.toml`, then run
`uv lock`. The engine always runs hook projects with `--locked`.

## Hook Library Commands

```bash
untaped-recipe hook list
untaped-recipe hook init set_owner
untaped-recipe hook add ./my-hook-project --name set_owner
untaped-recipe hook show set_owner
untaped-recipe hook edit set_owner
untaped-recipe hook remove set_owner --yes
```

`hook add` copies project directories, not bare `.py` files. `hook show` and
`hook edit` open the module file when the supplied name matches a declared
hook; otherwise they show or edit `pyproject.toml`.

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
