# Recipes

Recipes describe engine-mediated file changes for one or more target
directories. The engine plans all changes in memory, renders diffs, optionally
creates backups, and only then writes files for successful target plans.

## Library Layout

The default library root is `~/.untaped/untaped-recipes`:

```text
recipes/
hooks/
backups/
```

Recipes can be stored as a project directory:

```text
recipes/add-config/
├── recipe.yml
├── pyproject.toml
├── uv.lock
├── templates/
│   └── config.yml
├── files/
│   └── README.md
└── src/
    └── add_config_hooks/
        └── hooks/
            └── set_owner.py
```

Or as a single file:

```text
recipes/add-config.yml
```

Resolution order is package, single library file, then filesystem path.

Single-file recipes are best when they use only templates, copy/remove steps,
built-ins, or hooks already installed in the global hook library. Use a recipe
project when the recipe needs local hook code or hook-specific dependencies.
Recipe projects declare local hooks in `pyproject.toml`:

```toml
[tool.untaped_recipe.hooks]
"set_owner" = { module = "add_config_hooks.hooks.set_owner" }
```

The recipe still uses the simple hook name:

```yaml
steps:
  - type: transform
    file: pyproject.toml
    hook: set_owner
```

## Schema

```yaml
version: 1
name: add-config
description: Add shared service configuration.
inputs:
  service:
    type: str
    required: true
  replicas:
    type: int
    default: 2
steps:
  - type: validate
    hook: has_pyproject
  - type: template
    template: templates/config.yml
    dest: config.yml
  - type: copy
    source: files/README.md
    dest: README.md
  - type: transform
    file: pyproject.toml
    hook: set_owner
    args:
      owner: platform
  - type: transform
    files:
      - local.yml
      - site.yml
    optional: true
    hook: add_play_collections
  - type: remove
    files:
      - legacy.yml
      - ansible.cfg
```

Supported input types are `str`, `int`, `bool`, and `float`. Unknown input
overrides are rejected so typoed `--var` names do not silently fall back to
defaults.

## Step Types

`validate` calls a hook before later steps. A failed verdict aborts that
target before any writes.

`template` renders a recipe-local text template using `{{ name }}` placeholders
from resolved inputs.

`copy` copies a recipe-local text file into a target-relative destination.

`transform` reads one target file, calls a trusted Python hook, and plans the
returned content as the new file body. A transform may use `optional: true` to
skip a missing target file and record a warning instead of failing that target.
This is only for target layout variation; `optional` is not supported on
`template` or `copy`.

`remove` plans deletion of a target-relative file if it currently exists or was
created earlier in the same target plan.

`transform` and `remove` also accept `files` as explicit multi-file fan-out:

```yaml
- type: transform
  files:
    - local.yml
    - site.yml
    - playbooks/deploy.yml
  optional: true
  hook: add_play_collections

- type: remove
  files:
    - ansible.cfg
```

Multi-file syntax is only DRY sugar. The recipe model expands it into ordinary
single-file steps before planning, and hooks are still called once per file
with that file's path. `file` and `files` are mutually exclusive, and `files`
must not be empty. There is no globbing or discovery in v1; list the known
candidate paths that the recipe is allowed to touch.

All recipe-local and target-relative paths must be safe relative paths. Absolute
paths, `..` segments, and nested symlink traversal are rejected before
engine-mediated reads or writes.

## Apply Behavior

```bash
untaped-recipe apply add-config ./repo-a ./repo-b --var service=api
untaped-recipe apply add-config --stdin --yes --parallel 8 --format pipe
```

Important behavior:

- Every target is planned before writes begin.
- Diffs are written to stderr.
- Provide targets either as positional directories or with `--stdin`, not both.
- Piped stdin requires `--yes` before planning unless `--dry-run` is used.
- `--dry-run` previews and reports without writing.
- A planning failure for one target does not block successful targets.
- Within one target, planning and write failures leave the target unchanged;
  write failures are reported as per-target errors.
- Backups are created by default; pass `--no-backup` only when the target tree
  is already protected another way.

Structured output rows use kind `recipe.outcome`.
Skipped optional transforms appear in the row's `warnings` field as a
semicolon-delimited string.

## Ansible Playbook Migration Example

For mixed-layout Ansible repos, list the known playbook names and let optional
transforms skip whichever ones are absent:

```yaml
version: 1
name: ansible-2.12-playbook-migration
steps:
  - type: transform
    files:
      - local.yml
      - site.yml
      - playbooks/deploy.yml
    optional: true
    hook: add_play_collections

  - type: remove
    files:
      - ansible.cfg
```

The `add_play_collections` hook can be recipe-local in the recipe project's
`pyproject.toml`, a global hook project under `hooks/add_play_collections/`, or
part of a namespaced pack such as `hooks/ansible/` and referenced as
`ansible.add_play_collections`.

## Backups

Backup bundles record target paths, touched files, before and after hashes,
recipe name, inputs, and creation time.

```bash
untaped-recipe backup list
untaped-recipe backup show 20260619T120000000000Z-a1b2c3d4
untaped-recipe backup restore 20260619T120000000000Z-a1b2c3d4
untaped-recipe backup restore latest
```

Backup ids use `YYYYMMDDTHHMMSSffffffZ-8hex`. `show` and `restore` accept full
ids, unambiguous id prefixes, or `latest`.
Restore refuses to overwrite files that changed after the backup was created.
Use `--force` only after inspecting those later edits.
