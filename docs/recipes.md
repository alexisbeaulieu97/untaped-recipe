# Recipes

Recipes describe engine-mediated file changes for one or more target
directories. The engine plans all changes in memory, renders diffs, optionally
creates backups, and only then writes files for successful target plans.

## Library Layout

The default library root is `~/.untaped/untaped-recipes`:

```text
packs/
packs.toml
backups/
```

The library stores installed pack projects under `packs/<pack-id>/`. A pack is
the reusable library and sharing unit. It is a uv project whose
`pyproject.toml` exposes zero or more recipes and zero or more hooks:

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = ["untaped-recipe>=0.10"]

[tool.untaped_recipe]
requires_hook_api = ">=0.9,<1"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

```text
ansible/
├── pyproject.toml
├── uv.lock
├── recipes/
│   └── playbook-migration/
│       ├── recipe.yml
│       └── templates/
│           └── config.yml
└── src/
    └── ansible_hooks/
        └── hooks/
            └── add_play_collections.py
```

Nested uv projects or workspaces inside a pack are opaque. Untaped only reads
the top-level project metadata and declared recipe paths.

Single-file recipes remain supported by explicit path only, for quick local use:

```bash
untaped-recipe apply ./recipe.yml ./repo --yes
```

Single-file recipes are best when they use only templates, copy/remove steps,
built-ins, or hooks already installed through packs. Use a pack when local hook
code or hook-specific dependencies should ship with the recipe.

Recipe YAML is behavior-only. It contains `version`, optional `description`,
optional `inputs`, and `steps`; `name:` is rejected. The recipe file schema
remains `version: 1`.

See [packs.md](./packs.md) for pack identity, installation, and sharing.

## Authoring Commands

```bash
untaped-recipe new pack ansible
untaped-recipe new recipe ansible/playbook-migration
untaped-recipe new hook ansible/add_play_collections
untaped-recipe add ./ansible --yes
untaped-recipe hook run ansible/add_play_collections --target ./repo --file site.yml --diff
```

`new pack` creates an empty uv pack project. `new recipe <pack>/<recipe>` adds a
recipe under `recipes/<recipe>/` and updates the manifest. `new hook
<pack>/<hook>` adds a hook module under `src/`, updates
`[tool.untaped_recipe.hooks]`, adds the dev-only `untaped-recipe` dependency
for typing, pins `requires_hook_api = ">=0.9,<1"`, and refreshes `uv.lock`.

`new recipe` and `new hook` also accept explicit local pack paths such as
`./some-local-pack/probe`; the final path segment is the recipe or hook name and
the parent is the pack directory. Bare multi-segment refs must be exactly
`<pack>/<name>`.

`hook run` invokes one resolved hook against explicit fixture context without
running a full recipe or writing target files.

## Resolution

```bash
untaped-recipe apply playbook-migration ./repo
untaped-recipe apply ansible/playbook-migration ./repo --yes
untaped-recipe apply ./pack-project ./repo --recipe playbook-migration --yes
untaped-recipe apply ./recipe.yml ./repo --yes
untaped-recipe check
untaped-recipe check ansible
untaped-recipe check ansible/playbook-migration
```

Resolution rules:

- Bare recipe names resolve against installed packs only when unique.
- `pack/recipe` resolves an installed pack recipe.
- `./recipe.yml` runs a path-only single-file recipe.
- `./pack-project --recipe recipe` runs a recipe from a local pack path.

For `apply`, local paths must be explicit. A path is explicit only when it
starts with `./`, `../`, `/`, or `~`, or ends in `.yml` or `.yaml`. Anything
else, including `a/b`, is a library ref and is never classified by checking the
filesystem.

`list`, `show`, `check`, `remove`, and `edit` operate on the unified pack
library. `list` shows recipes by default; use `list --packs` for packs and
`list --hooks` for hooks. `check` with no ref validates the whole library.

Apply output and backup metadata use canonical refs such as
`ansible/playbook-migration`.

## Schema

```yaml
version: 1
description: Add shared service configuration.
inputs:
  service:
    type: str
    required: true
    description: Service identifier.
    from:
      - "{{ record.repo }}"
      - "{{ target.name }}"
  replicas:
    type: int
    default: 2
  api_token:
    type: str
    scope: global
    sensitive: true
    required: true
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

Supported input types are `str`, `int`, `bool`, and `float`.

## Inputs

Input specs support:

- `type`: one of `str`, `int`, `bool`, or `float`.
- `default`: fallback value when no fixed value, source, or prompt resolves.
- `required`: require a value after sources and defaults.
- `description`: prompt/help text for humans.
- `sensitive`: redact the value in output rows, warnings/errors, and backup
  metadata; file-level previews and diffs are suppressed for targets with
  sensitive inputs.
- `scope`: `global` for one value per invocation or `target` for a value that
  may vary per target.
- `from`: one Jinja expression or an ordered list of candidate expressions.

Unknown input-spec fields are rejected so typos fail at recipe load time.
Omitted `scope` infers `target` when `from` is present and `global` otherwise.
`scope: global` rejects recipe `from` and CLI `--input-from`; use
`--var`/`--vars` for fixed global values.

Per-target `from` values are sandboxed strict native Jinja strings. They are
used only to derive scalar input values, not to change recipe structure, paths,
hook names, or template rendering. They may combine literal text,
string/number/boolean/null constants that Jinja parses without operators, and
field access on `target` or optional `record`. Control blocks, filters, tests,
calls, operators, and collection literals are rejected, and no ambient Jinja
globals are available. Negative numeric expressions like `{{ -1 }}` are not
valid V1 sources. The context contains:

- `target.path`: target path as a string.
- `target.name`: target basename.
- `target.parent_path`: target parent path as a string.
- `target.parent_name`: target parent basename.
- `record`: the incoming untaped pipe record, only for targets read from pipe
  records.

Missing, undefined, or null candidate values fall through to the next
candidate. `false`, `0`, and `""` are real values. Derived values must be
scalar and bounded to small results; oversized or non-scalar rendered values
are rejected.

Input precedence for each declared input is:

1. fixed value from `--var`/`--vars` or source override from `--input-from`
2. recipe `from`
3. `--interactive` prompt
4. recipe `default`
5. required-input error

A fixed value and source override for the same input is a usage error. Unknown
input names in `--var`, `--vars`, or `--input-from` are rejected. When a
default exists, interactive prompts show it and an empty answer accepts it.
Sensitive defaults are not displayed to the prompt backend, but an empty answer
still accepts the default.

Examples:

```yaml
inputs:
  service:
    type: str
    required: true
    from:
      - "{{ record.repo }}"
      - "{{ target.name }}"
  owner:
    type: str
    scope: target
    default: platform
  api_token:
    type: str
    scope: global
    sensitive: true
    required: true
```

```bash
untaped-recipe apply add-config ./services/api --var api_token=secret --yes
untaped-recipe apply add-config --stdin --input-from owner='{{ record.team }}' --var api_token=secret --yes
untaped-recipe apply add-config ./services/api --interactive
```

`--interactive --check` is rejected. With `--stdin --interactive`, target data
still comes from stdin and prompts are read from the controlling terminal; the
command fails clearly when no terminal is available. `--stdin` writes require
`--yes` unless `--dry-run` or `--check` is used.

## Step Types

`validate` calls a hook before later steps. A failed verdict aborts that
target before any writes.

`template` renders a recipe-local text template using `{{ name }}` placeholders
from resolved inputs. Template steps are strict by default:
`unknown_tokens: error` rejects unknown bare names and non-bare `{{ ... }}`
tokens. Set `unknown_tokens: keep` when the template intentionally emits
another tool's template syntax, such as GitHub Actions or Helm:

```yaml
steps:
  - type: template
    template: templates/workflow.yml
    dest: .github/workflows/ci.yml
    unknown_tokens: keep
```

```yaml
# templates/workflow.yml
name: ci
on: push
jobs:
  test:
    if: ${{ github.ref == 'refs/heads/main' }}
    steps:
      - run: helm template --set owner={{ owner }} chart
      - run: echo '{{ .Values.image.tag }}'
```

Under `keep`, known inputs such as `{{ owner }}` still render and unknown
tokens such as `${{ github.ref }}` or `{{ .Values.image.tag }}` pass through
verbatim.

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
untaped-recipe apply ansible/playbook-migration ./repo-a --yes
untaped-recipe apply ./pack-project ./repo-a --recipe playbook-migration --yes
untaped-recipe apply add-config --stdin --yes --parallel 8 --format pipe
untaped-recipe apply add-config --stdin --input-from service='{{ record.repo }}' --yes
untaped-recipe apply add-config ./repo-a --check
untaped-recipe apply add-config ./repo-a --preview diff
untaped-recipe check
untaped-recipe check ansible
untaped-recipe check ansible/playbook-migration
```

Important behavior:

- Every target is planned before writes begin.
- Preview output is written to stderr. Normal apply and `--dry-run` default to
  `--preview table`, which shows changed files with absolute paths, change
  kind, and line counts. `--check` defaults to summary-only preview output for
  CI; pass `--preview table` when you want the same file table in check mode.
  Use `--preview diff` for patch-compatible unified diffs or `--preview none`
  for summary-only preview output.
- File-level preview details and diffs are suppressed for targets with
  sensitive inputs because the generated content may contain secret values.
- Provide targets either as positional directories or with `--stdin`, not both.
- Piped stdin requires `--yes` before planning unless `--dry-run` or `--check`
  is used.
- `--dry-run` previews and reports without writing.
- `--check` previews without writing, creates no backups, prompts for nothing,
  and exits non-zero if any target would change or fail.
- A planning failure for one target does not block successful targets.
- Within one target, planning and write failures leave the target unchanged;
  write failures are reported as per-target errors.
- Backups are created by default; pass `--no-backup` only when the target tree
  is already protected another way.
- `check` with no ref validates the whole installed pack library and its index.
- `check <pack>` validates pack metadata, declared recipe files and assets,
  recipe input source expressions, pack-local hooks, exported hook functions,
  and lockfile state.
- `check <recipe-ref>` validates one recipe by bare or qualified ref.
- `hook run <hook>` executes one hook through the same built-in or external uv
  worker path as `apply`; transform stdout is raw content or `--diff`, while
  validate stdout is a `recipe.hook_run` verdict record.

Structured output rows use kind `recipe.outcome`.
Skipped optional transforms appear in the row's `warnings` field as a
semicolon-delimited string.
Check-mode output uses the same `recipe.outcome` rows with `status: check`.
Every `recipe.outcome` row includes an `inputs` mapping containing resolved
declared recipe inputs. Sensitive values are rendered as `***`, and sensitive
values are also redacted from row warnings and errors.
`--format` and `--columns` affect stdout `recipe.outcome` rows only; they do
not change the fixed-column stderr preview table. `--quiet` mutes post-run
success chatter but does not mute selected preview detail, warnings, errors, or
destructive confirmation prompts.

## Ansible Playbook Migration Example

For mixed-layout Ansible repos, list the known playbook names and let optional
transforms skip whichever ones are absent:

```yaml
version: 1
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

The `add_play_collections` hook can be in the recipe's own pack, another
installed pack when the name is unambiguous, or a packaged built-in if the
engine ships one.

## Backups

Backup bundles record target paths, touched files, before and after hashes,
canonical recipe ref, redacted per-target inputs on each file entry, and
creation time. Backups store text content for the engine-managed files that
recipes edit; restores do not preserve file mode or mtime. Backup metadata
never stores the full incoming pipe record.

```bash
untaped-recipe backup list
untaped-recipe backup show 20260619T120000000000Z-a1b2c3d4
untaped-recipe backup restore 20260619T120000000000Z-a1b2c3d4
untaped-recipe backup restore latest
```

Backup ids use `YYYYMMDDTHHMMSSffffffZ-8hex`. `show` and `restore` accept full
ids, unambiguous id prefixes, or `latest`.
Restore refuses to overwrite files that changed after the backup was created.
Use `--force` only after inspecting those later edits. Restore uses the same
symlink-confined, staged, rollback-aware write path as apply, so a failed
multi-file restore reports any incomplete rollback details.
