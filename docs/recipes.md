# Recipes

A recipe is a declarative description of engine-mediated file changes for one
or more target directories. This page covers the recipe YAML schema and the
five step types that make up a recipe. The engine plans every step in memory,
previews the result, and only then writes files for successful target plans;
see [running recipes](./apply.md) for that flow.

## Schema

Recipe YAML is behavior-only. The file schema is `version: 1`, with an optional
`description`, an optional `inputs` block, and a list of `steps`:

```yaml
version: 1
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
  - type: remove
    file: legacy.yml
```

The `inputs:` block declares the recipe's input contract — the values that
steps, templates, and hooks resolve at apply time. See [inputs](./inputs.md)
for the full input spec: types, `scope`, `sensitive`, `from` derivation, and
precedence.

A recipe carries no identity of its own. `name:` is not part of the schema and
is rejected as extra metadata; public identity comes from the pack manifest or,
for a single-file recipe, from the file path stem. Unknown top-level keys are
rejected at load time.

## Step types

Steps run in order for each target. A step either plans file changes into the
target's plan buffer or validates the target before later steps run.

### validate

```yaml
- type: validate
  hook: has_pyproject
```

`validate` calls a read-only hook before later steps. A failed verdict aborts
that target before any writes; a warn verdict records a warning and continues.
See [hooks](./hooks.md) for the hook contract and verdict returns.

### template

`template` renders a recipe-local text template into a target-relative
destination, substituting `{{ name }}` placeholders from resolved inputs:

```yaml
- type: template
  template: templates/config.yml
  dest: config.yml
```

The placeholder language and the `unknown_tokens` policy that governs
non-input tokens are described in [templating](./templating.md).

### copy

`copy` copies a recipe-local text file into a target-relative destination
without rendering it:

```yaml
- type: copy
  source: files/README.md
  dest: README.md
```

### transform

`transform` reads one target file, calls a trusted Python hook, and plans the
returned content as the new file body:

```yaml
- type: transform
  file: pyproject.toml
  hook: set_owner
  args:
    owner: platform
```

A transform may set `optional: true` to skip a missing target file and record a
warning instead of failing that target. This is only for target layout
variation; a file deleted earlier in the same plan still errors. `optional` is
supported on `transform` only, never on `template` or `copy`.

### remove

`remove` plans deletion of a target-relative file if it currently exists or was
created earlier in the same target plan:

```yaml
- type: remove
  file: legacy.yml
```

### if_absent

`template` and `copy` accept `if_absent: true` to create the destination only
when it does not already exist in the planned state. An existing file is left
untouched, and a file removed earlier in the same recipe counts as absent:

```yaml
- type: template
  template: templates/config.yml
  dest: config.yml
  if_absent: true
```

## Multi-file fan-out

`transform` and `remove` accept `files` as explicit multi-file fan-out:

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
    - legacy.yml
    - ansible.cfg
```

Multi-file syntax is only DRY sugar:

- The recipe model expands it into ordinary single-file steps before planning,
  so the hook is still called once per file with that file's path.
- `file`, `files`, and `globs` are mutually exclusive on a step.
- `files` and `globs` must not be empty.

## Globs

`transform` and `remove` also accept `globs` for planning-time discovery:

```yaml
- type: remove
  globs:
    - "**/*.retry"
  exclude:
    - ".git/**"
```

Glob expansion rules:

- Patterns expand per target at planning time.
- They match regular files only — directories and symlinks are skipped.
- Matches are deduplicated and sorted for deterministic plans.
- `exclude` is only valid with `globs` and uses the same pattern language: `*`
  never crosses `/`, `**` does, and a literal relative path excludes itself.

Glob safety and edge cases:

- Globs have no implicit safety excludes — dotfiles and `.git` internals match
  when the pattern says so, so repo-wide sweeps should usually carry
  `exclude: [".git/**"]`.
- A step whose patterns match nothing plans no changes and records a per-target
  warning.
- Binary (non-UTF-8) files are unsupported: a matched binary file fails that
  target's plan with an error naming the file; use `exclude` to skip it.
- `optional` is not valid with `globs`, because zero matches is already a
  first-class, non-failing outcome.

Path fields — including glob patterns and `exclude` entries — may contain bare
input tokens and are re-checked as confined relative paths after rendering; see
[templating](./templating.md) for that behavior and [safety](./safety.md) for
the underlying path-safety rules.

## Step field compatibility

Which optional and multi-file fields each step type accepts:

| Step        | `file` | `files` | `globs` | `optional` | `if_absent` |
| ----------- | ------ | ------- | ------- | ---------- | ----------- |
| `validate`  | –      | –       | –       | –          | –           |
| `template`  | –      | –       | –       | –          | ✓           |
| `copy`      | –      | –       | –       | –          | ✓           |
| `transform` | ✓      | ✓       | ✓       | ✓*         | –           |
| `remove`    | ✓      | ✓       | ✓       | –          | –           |

`file`, `files`, and `globs` are mutually exclusive on a step. *`optional` is not
valid alongside `globs`.

## Hook arguments

Hook `args` are passed verbatim from recipe YAML to the hook. The engine never
templates `args`; hooks receive the resolved `inputs` mapping separately, with
native list and dict values for structured inputs. A hook that accepts a
templated string argument renders it itself with `helpers.render_template()` —
see [hooks](./hooks.md).

Because the engine does not template `args`, YAML anchors are the recommended
way to reuse structure across steps without teaching the engine a structural
templating language:

```yaml
inputs:
  team:
    type: str
  service:
    type: str

x-common-labels: &common_labels
  managed-by: untaped-recipe
  team: "{{ team }}"

steps:
  - type: transform
    file: "services/{{ service }}.yml"
    hook: yaml_edit
    args:
      edits:
        - op: merge
          path: [metadata, labels]
          value:
            <<: *common_labels
            service: "{{ service }}"
        - op: ensure
          path: [collections]
          value: {name: acme.required}
          match: [name]
```

The `yaml_edit` step above is a built-in hook; its `args` grammar (`edits`,
`op`, `path`, `value`, and `ensure`'s `match`) is documented with the hook
itself in [hooks](./hooks.md). The `ensure` edit is idempotent — re-running the
recipe never duplicates `acme.required` — and string `value` entries such as
`"{{ team }}"` are rendered by `yaml_edit`, not by the engine.

## Single-file recipes

A recipe file can run directly by explicit path, for quick local use:

```bash
untaped-recipe apply ./recipe.yml ./repo --yes
```

Single-file recipes are best when they use only templates, copy or remove steps,
built-ins, or hooks already installed through packs. Reach for a pack when local
hook code or hook-specific dependencies should ship alongside the recipe. See
[packs](./packs.md) for pack structure, the library layout, and how bare and
qualified recipe refs resolve.

## Design rationale

Recipes stay declarative and intentionally dumb. A recipe owns the input
contract, file paths, step ordering, and hook arguments. A hook owns decisions:
parsing, branching, validation, and nontrivial edits. The plan is only the
execution record produced after inputs resolve; it is not a second programming
surface.

That boundary keeps scalar inputs byte-identical with earlier recipe behavior,
lets structured inputs flow to hooks as data, and limits engine templating to
file content and safe path fields. It also keeps `args` stable: recipe authors
can reuse YAML structure with anchors, while hooks choose when a string value is
a template and which unknown-token policy applies.

## Worked example: Ansible playbook migration

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

The `add_play_collections` hook can live in the recipe's own pack, in another
installed pack when the name is unambiguous, or ship as a packaged built-in.
Each named file that exists is transformed; each missing file is skipped with a
warning because the transform is `optional`.
