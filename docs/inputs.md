# Inputs

Recipe inputs are the typed values a recipe resolves once per invocation or once
per target, then passes to templates and hooks. This page is the home of the
input contract: the spec fields, the type system, scope, sensitive handling, the
`from` derivation sandbox, resolution precedence, and the command-line flags that
supply values. The `inputs:` block is part of the recipe schema in
[recipes](./recipes.md); this page owns what goes inside it.

## Input specs

Each entry under `inputs:` is a named spec:

```yaml
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
```

A spec supports these fields:

| Field | Purpose |
|---|---|
| `type` | One of `str`, `int`, `bool`, `float`, `list`, or `dict`. Defaults to `str`. |
| `items` | Scalar element type for `type: list` (see [Types](#types)). |
| `values` | Scalar value type for `type: dict`. |
| `default` | The value used when no fixed value, source, or prompt resolves. |
| `required` | When `true`, an input that resolves to nothing after sources, prompts, and defaults raises `missing required input: <name>` at plan time. |
| `description` | Prompt and help text for humans. |
| `sensitive` | Redact the value in output and previews (see [Sensitive inputs](#sensitive-inputs)). |
| `scope` | `global` or `target` (see [Scope](#scope)). |
| `from` | One Jinja expression or an ordered list of candidate expressions (see [Deriving values with `from`](#deriving-values-with-from)). |

Unknown input-spec fields are rejected, so a typo such as `defualt:` fails when
the recipe loads rather than silently doing nothing.

## Types

Scalar inputs are `str`, `int`, `bool`, or `float`. Values from the command line,
`from` expressions, and defaults are coerced to the declared type; a value that
cannot coerce fails that input. Booleans accept `1/true/yes/on` and
`0/false/no/off` (case-insensitive) in addition to real booleans.

Structured inputs are shallow. A `list` carries `items` and a `dict` carries
`values` to name the scalar element type (`str`, `int`, `bool`, or `float`),
both defaulting to `str`:

```yaml
inputs:
  collections:
    type: list
    items: str
  labels:
    type: dict
    values: str
```

The shape is one level deep by design: element and value types are scalars, and
nested containers (a list of lists, a dict of dicts) are rejected. `items` is
valid only with `type: list` and `values` only with `type: dict`. Hooks receive
structured inputs as native Python lists and dicts — see the hook contract in
[hooks](./hooks.md).

## Scope

```yaml
inputs:
  api_token:            # global: one value for the whole invocation
    type: str
    scope: global
  service:              # target: may vary per target
    type: str
    from:
      - "{{ target.name }}"
```

```bash
untaped-recipe apply deploy ./repo --var api_token=secret --yes
```

`scope` controls how often an input resolves:

- `global` resolves one value per invocation, before any target is planned.
- `target` resolves a value that may vary per target.

Omitting `scope` infers it: `target` when the spec declares `from`, and `global`
otherwise. A `global` input cannot declare `from` and cannot take a per-target
source override — both are per-target mechanisms. Supply fixed global values with
`--var`/`--vars` instead. Declaring `from` on a `scope: global` input is a load
error; passing `--input-from` for one is a usage error
(`cannot use --input-from for input '<name>' with scope global`).

## Sensitive inputs

```yaml
inputs:
  api_token:
    type: str
    scope: global
    sensitive: true
    required: true
```

```bash
untaped-recipe apply deploy ./repo --var api_token=secret --yes
```

Mark an input `sensitive: true` to keep its value out of everything the engine
records or displays, while the real value still reaches templates and hooks:

- Resolved-input cells in structured output rows render as `***` (the
  `recipe.outcome` row schema and its `inputs` mapping are documented in
  [pipes](./pipes.md)).
- The value is redacted from row warnings and errors.
- Backup metadata stores the redacted value, never the real one.
- **File-level preview detail and diffs are suppressed for any target that
  resolves a sensitive input.** Because rendered file content can embed the
  secret, such targets appear in the stderr preview only as a target plus a
  changed-file count — never as per-file diffs. `--preview diff` does not override
  this. Preview modes and the rest of apply-time behavior live in
  [apply](./apply.md).

Redaction is keyed on the spec, so it applies wherever the value would otherwise
surface. A sensitive default is never shown to the prompt backend either (see
[Interactive prompts](#interactive-prompts)).

## Deriving values with `from`

A `target`-scoped input can derive its value from per-target context with `from`,
given as a single expression or an ordered list of candidates:

```yaml
inputs:
  service:
    type: str
    required: true
    from:
      - "{{ record.repo }}"
      - "{{ target.name }}"
```

`from` expressions are evaluated in a sandboxed, strict, native Jinja
environment. They exist only to derive declared input values — they cannot change
hook names, step types, or any other recipe structure.

### What an expression may contain

An expression may combine literal text with:

- string, number, boolean, and `null` constants that Jinja parses without
  operators, and
- attribute or item access on `target` or `record`.

Everything else is rejected at load time: control blocks, filters, tests, calls,
operators, and collection literals. Because operators are rejected, a negative
numeric expression such as `{{ -1 }}` is not a valid source. No ambient Jinja
globals are available.

A valid `from` list — literal text plus attribute access:

```yaml
from:
  - "{{ record.repo }}"
  - "svc-{{ target.name }}"
  - "default-service"
```

A filter makes the expression invalid; this is rejected at load time:

```yaml
from:
  - "{{ target.name | upper }}"
```

### Context fields

The evaluation context contains:

- `target.path` — the target path as a string.
- `target.name` — the target basename.
- `target.parent_path` — the target's parent path as a string.
- `target.parent_name` — the target's parent basename.
- `record` — the incoming untaped pipe record, present only for targets read from
  pipe records. See [pipes](./pipes.md) for how records reach targets.

### Fall-through

```yaml
inputs:
  service:
    type: str
    from:
      - "{{ record.repo }}"     # tried first; falls through when undefined
      - "{{ target.name }}"     # used when record.repo does not resolve
```

```bash
untaped-recipe apply deploy ./repo --yes
```

Candidates are tried in order. A candidate whose value is missing, undefined, or
`null` falls through to the next one. Real values do not fall through: `false`,
`0`, `""`, `[]`, and `{}` are all resolved values. If no candidate resolves, the
input continues down the precedence chain (prompt, then default, then the
required-input error).

Recipe `from` candidates fall through silently when they do not resolve. A
command-line source override (`--input-from`) is stricter: if its expression does
not resolve for a target, that target fails with
`--input-from for input '<name>' did not resolve for target: <path>` rather than
falling through.

### Structured derivation and bounds

Derived values are bounded in size and nesting depth; a value that renders too
large, or a container deeper than the shallow input shape allows, is rejected.
Deriving a container is allowed only for an input declared `list` or `dict`; a
scalar-declared input rejects a derived list or mapping.

Using derived context inside a **path** is a separate concern — path fields
cannot reference structured or sensitive inputs, and the derive-a-scalar pattern
for paths lives in [templating](./templating.md).

## Resolution precedence

For each declared input, the engine resolves the first source that produces a
value, in this order:

1. A fixed value from `--var`/`--vars`, or a source override from `--input-from`
   (these two are mutually exclusive for one input).
2. Recipe `from` — the first candidate that resolves.
3. An interactive prompt, when `--interactive` is set.
4. The recipe `default`.
5. Otherwise, a `missing required input: <name>` error if `required: true`; a
   non-required input with nothing to resolve is simply left unset.

For example, when `--var service=web` is supplied for an input that also declares
`from`, the fixed value wins at step 1 and the `from` candidates are never
evaluated.

When `--interactive` is active and a default exists, the default is folded into
the prompt (it is shown and an empty answer accepts it) rather than applied as the
later step 4.

## Providing values on the command line

`apply` accepts four flags that feed the precedence chain. All of them reject an
unknown input name (`unknown input: <name>`), so a misspelled flag fails fast.

### Fixed values: `--var` and `--vars`

`--var name=value` sets one fixed value; `--vars file.yml` loads a YAML mapping of
them. When both supply the same name, `--var` wins. For a **scalar**-declared
input, `--var` keeps the literal string and coerces it to the declared type. For
an input declared `list` or `dict`, `--var` parses the value as YAML first, then
coerces:

```bash
untaped-recipe apply add-config ./repo --var 'cols=[name, owner]' --yes
untaped-recipe apply add-config ./repo --var 'labels={team: platform}' --yes
```

A structured `--var` whose YAML is not the declared shape fails with
`input '<name>' expects YAML list: ...` or `... expects YAML mapping: ...`.
`--vars` files may hold native YAML lists and mappings directly, with no
per-value string parsing.

### Source overrides: `--input-from`

`--input-from name='<jinja>'` overrides one input's per-target source with a
single expression, using the same sandbox and context as recipe `from`
([above](#deriving-values-with-from)). Unlike recipe `from`, it must resolve for
every target. Supplying both a fixed value and a source override for the same
input is a usage error:
`cannot combine --var/--vars and --input-from for <name>`.

### Interactive prompts

`--interactive` prompts for inputs that no source resolved. Prompts read a
scalar; an empty answer accepts the recipe default when one exists, and otherwise
leaves a non-required input unset. A sensitive input is prompted without echo and
its default is not shown to the prompt backend — but an empty answer still accepts
that hidden default.

Structured inputs cannot be prompted. With `--interactive`, a `list` or `dict`
input resolves exactly as it would non-interactively (its default, or unset); a
`required` structured input with no value raises
`interactive prompting is not supported for structured input '<name>'; pass --var or --vars`.

`--interactive` cannot be combined with `--check`. Its interaction with `--stdin`
(target data from stdin, prompts from the controlling terminal) is covered in
[apply](./apply.md).
