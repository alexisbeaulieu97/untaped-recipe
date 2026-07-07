# Templating

Recipes render values in two places: the bodies of `template` steps and the
path-bearing fields of steps. Both use the same `{{ name }}` token language,
where `name` is a resolved recipe input. This page covers that language, the
`unknown_tokens` policy for template bodies, and the strict rendering and
confinement rules that apply to path fields.

The token language is deliberately small. A `{{ name }}` token is replaced by
the string form of the resolved input called `name`; it is not a Jinja
expression. Filters, operators, and control blocks are not part of it. (Per-input
`from` derivation is a separate, sandboxed Jinja surface — see
[inputs](./inputs.md).)

## Template bodies

A `template` step renders a recipe-local text file, substituting `{{ name }}`
placeholders from resolved inputs. By default rendering is strict:
`unknown_tokens: error` rejects both unknown bare names and any non-bare
`{{ ... }}` token. This catches typos and stray template syntax at plan time.

Set `unknown_tokens: keep` when the template intentionally emits another tool's
template syntax, such as GitHub Actions or Helm:

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

Under `keep`, known inputs such as `{{ owner }}` still render, while unknown
tokens such as `${{ github.ref }}` or `{{ .Values.image.tag }}` pass through
verbatim. `unknown_tokens` affects template file bodies only; it never relaxes
path-field rendering.

Sensitive input values do render into template bodies — that is why file-level
previews and diffs are suppressed for targets with sensitive inputs (see
[running recipes](./apply.md) and [safety](./safety.md)). Structured inputs
(`list` and `dict`) cannot be rendered into a body: a bare token that resolves to
a list or mapping is an error, because hooks receive structured inputs natively
rather than as text.

## Path-bearing fields

```yaml
inputs:
  service:
    type: str
    required: true
steps:
  - type: template
    template: templates/service.yml
    dest: "services/{{ service }}.yml"
```

Path fields also accept bare `{{ input }}` tokens, but they are not Jinja
expressions. When one appears:

- The engine renders each path field per target after input resolution.
- Rendering is always strict — `unknown_tokens: keep` has no effect here.
- The rendered value is rechecked as a confined relative path.

The path-bearing fields are:

- `template.template` and `template.dest`
- `copy.source` and `copy.dest`
- `transform.file`, `transform.files`, `transform.globs`, and
  `transform.exclude`
- `remove.file`, `remove.files`, `remove.globs`, and `remove.exclude`

## Confinement recheck

Rendering a path field can turn a safe-looking template into an unsafe path, so
every rendered path is validated again before any engine-mediated read or write.
The rendered value must be a safe relative path — the same base rule described in
[safety](./safety.md), reported with the original field name. In addition:

- Recipe-local `template.template` and `copy.source` paths are confined to the
  recipe directory.
- Target-relative paths, and the results of glob expansion, are confined to the
  target root.

A rendered path that is absolute, contains `..`, escapes its base, or would
traverse a symlink is rejected before the step runs.

## Sensitive and structured inputs are forbidden in paths

Two input kinds cannot appear in path fields:

- **Sensitive inputs.** Paths are displayed in previews and stored in plans and
  backup metadata, so a sensitive value in a path field is rejected outright.
- **Structured inputs.** `list` and `dict` inputs have no meaningful single-path
  rendering; hooks receive them natively instead.

To use target or pipe-record context in a path, first derive a scalar input with
`from`, then render that input:

```yaml
inputs:
  service:
    type: str
    from: "{{ record.repo }}"
steps:
  - type: template
    template: templates/service.yml
    dest: "services/{{ service }}.yml"
```

Here `service` is a plain `str` derived once per target from the pipe record (see
[inputs](./inputs.md) for `from` derivation), so it renders cleanly and safely
into the `dest` path.
