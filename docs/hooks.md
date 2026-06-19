# Hooks

Hooks are trusted local Python files used by `validate` and `transform` recipe
steps. The engine loads hooks from recipe-local hooks, global library hooks,
then packaged built-ins.

## Resolution Order

For `hook: set_owner`, resolution checks:

1. `<recipe package>/hooks/set_owner.py`
2. `<library_root>/hooks/set_owner.py`
3. packaged built-ins such as `yaml_edit`

Recipe-local hooks are best for package-specific behavior. Global hooks are
for reusable local conventions. Built-ins cover common safe transforms.

## Transform Hooks

Transform hooks receive the current file content and return replacement
content. They should not write files directly.

```python
from pathlib import Path

from untaped_recipe.infrastructure.hook_helpers import HookHelpers


def transform(
    content: str,
    *,
    inputs: dict,
    target: Path,
    file: Path,
    args: dict,
    helpers: HookHelpers,
) -> str:
    owner = args["owner"]
    return content.replace("OWNER", str(owner))
```

The engine owns file writes after preview, confirmation, and backup creation.

## Validate Hooks

Validate hooks inspect a target and return a verdict.

```python
from pathlib import Path

from untaped_recipe.domain.plan import Verdict
from untaped_recipe.infrastructure.hook_helpers import HookHelpers


def validate(
    *,
    inputs: dict,
    target: Path,
    args: dict,
    helpers: HookHelpers,
) -> Verdict:
    if not (target / "pyproject.toml").is_file():
        return helpers.fail("missing pyproject.toml")
    return helpers.pass_()
```

Accepted return values:

- `Verdict(status="pass" | "warn" | "fail", message="...")`
- compatible dict
- `None` for pass
- string for fail

Warnings are recorded on the target plan. Failures abort that target before
any writes.

## Helpers

`HookHelpers` provides:

- `pass_`, `warn`, and `fail` verdict helpers.
- `render_template(template, inputs)` for simple `{{ name }}` placeholders.
- `load_yaml(content)` and `dump_yaml(data)` backed by `ruamel.yaml`.

## Built-In YAML Hook

`yaml_edit` applies mapping and list-item edits while preserving comments,
quotes, and order where `ruamel.yaml` can round-trip them.

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
Python hooks for behavior outside that contract.
