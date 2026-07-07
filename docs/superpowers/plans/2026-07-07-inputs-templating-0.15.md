# untaped-recipe 0.15.0 — inputs & templating wave implementation plan

Design brief for the inputs & templating wave: structured input shapes
(list/dict) and bare-token templating of path-bearing step fields, plus the
real-usage quickies. Brainstormed and ruled with Alexis 2026-07-07 from the
0.15 parking lot his first sustained tool usage produced. One brainstorm
premise was corrected en route: the case for sandboxed Jinja in fields
(differentiating vars from inputs) dissolved when the `vars:` block was
ruled out — with no vars, every bare token unambiguously names an input.

Deferred with triggers, NOT in this wave: manifest args-schema (the shape
vocabulary lands here first; follow when deep-arg-validation pain recurs);
recursive input shapes (`items:` taking a nested shape is the natural
extension); dotted/sandboxed-Jinja field access (trigger = the
from:-derived-input idiom proving insufficient in real packs);
non-overridable `vars:` constants (trigger = input overridability causing
real harm).

Execution model: Codex implements from this merged brief (one commit per
task, STOP protocol, deviations recorded in the PR body); adversarial
conformance subagent + orchestration-session design pass review the diff.

## Global constraints

- One commit per task, TDD where test intent is pinned, full gates per
  commit: `uv --cache-dir .uv-cache run pytest` / `ruff check` /
  `ruff format --check` / `mypy` (bare, config-scoped).
- Never weaken an existing CLI-text assertion; extend, don't reword, unless
  a task pins new text.
- Recipe schema stays `version: 1` (all additive). `HOOK_API_VERSION`
  stays `0.9.0`: hooks already receive `inputs` as a JSON object over the
  wire; list/dict VALUES inside it are wire-compatible JSON — no protocol
  change. Task G records this reasoning in the PR body.
- Pipe surfaces: `recipe.outcome` columns unchanged; structured formats
  carry real list/dict values in the `inputs` mapping (no envelope or kind
  changes).
- Scalar inputs keep byte-identical behavior everywhere: `--var` parsing,
  coercion, prompting, rendering. Every new behavior is gated on the input
  being DECLARED list/dict-typed.
- Only `UntapedError` subclasses count as per-item failures inside
  `batch_apply` callbacks; per-target planning failures stay ValueError →
  error rows as today.

## Task A — structured InputSpec (list/dict shapes)

Contract (domain/recipe.py):

- `InputType = Literal["str", "int", "bool", "float", "list", "dict"]`.
  New literal alias `ScalarInputType = Literal["str", "int", "bool",
  "float"]`.
- `InputSpec` gains `items: ScalarInputType | None = None` and
  `values: ScalarInputType | None = None`. Validation (model-level):
  `items is only valid with type list`; `values is only valid with type
  dict`. Omitted `items`/`values` on a structured type defaults to `"str"`
  element coercion (the fields stay None; coercion treats None as "str").
- `coerce` for `type: list`: the value must be a non-string Sequence →
  returns a `list` with every element coerced through the existing scalar
  paths for the element type; any element failure or a non-sequence value
  → `ValueError("cannot coerce value to list")` (element detail may be
  chained/appended). For `type: dict`: the value must be a Mapping with
  ONLY `str` keys (non-string key → `ValueError("dict input keys must be
  strings")`); values coerced per element type; non-mapping →
  `ValueError("cannot coerce value to dict")`.
- Defaults flow through `coerce` at resolution time exactly as scalars do
  today (no new default-validation site).
- `sensitive: true` is allowed on structured inputs (redaction is
  whole-value — Task B).

Test intent: list/dict declarations round-trip; items-without-list and
values-without-dict validation messages; element coercion (list of "1"/2 →
ints with `items: int`); empty list and empty dict are VALID values;
wrong-element-type failure; non-string dict key failure; string value
against `type: list` fails (a str is a Sequence — must be rejected
explicitly); scalar specs unaffected (existing tests untouched).

## Task B — CLI intake + UX guards for structured inputs

Contract:

- `--var` (cli layer, where raw `key=value` strings are coerced —
  `application/inputs.py::_coerce_fixed_values` is the gate): when the
  declared input is list/dict-typed AND the supplied value is a `str`, the
  value is parsed as YAML first (same loader family the recipe files use),
  then coerced. YAML parse failure or a scalar result →
  `ConfigError` `input {name!r} expects YAML {list|mapping}: {detail}`.
  Values that are already containers (from `--vars` YAML) skip parsing.
  Scalar-typed inputs NEVER parse — today's exact string behavior.
- Interactive prompting: `_resolve_one`'s interactive branch raises
  `ConfigError` `interactive prompting is not supported for structured
  input {name!r}; pass --var or --vars` when the spec is structured.
- Redaction: whole-value `***` via the existing `redact_inputs` (no
  change; pin with a test: sensitive list renders as `***` in
  display_values and outcome rows).
- Outcome `inputs` column: structured formats (json/pipe) carry the real
  list/dict; the table view's `_inputs_cell` flattening renders structured
  values via their Python repr inside the existing key=value form (verify;
  if unreadable, a compact YAML/JSON dump is acceptable — pin the choice in
  the PR body as a recorded decision, with a test either way).

Test intent: `--var 'cols=[a, b]'` → list input resolves (end-to-end CLI
test through apply on a hookless recipe); malformed YAML → pinned error;
`--var` on scalar input containing `[` stays a literal string (boundary:
zero behavior change for scalars); `--vars` file with a native list;
prompting error; sensitive-list redaction in outcome rows.

## Task C — from:/--input-from structured derivation

Contract (infrastructure/input_jinja.py + application/inputs.py):

- `ensure_derived_value_within_bound` gains a keyword
  `structured: bool = False`. When False: today's exact behavior
  (scalar-only). When True: list/dict containers are permitted at the top
  level; the existing `_value_size`/`MAX_DERIVED_VALUE_LENGTH`/depth caps
  apply to the whole container (machinery already handles containers).
  Deep element shape is NOT its concern — `spec.coerce` (Task A) enforces
  shallowness right after.
- `_coerce_derived_value` passes `structured=spec.type in ("list",
  "dict")` on both bound checks.

Test intent: pipe record with a list field → `from: [record.collections]`
feeds a `type: list` input end-to-end; scalar-declared input deriving a
list still fails with today's "derived input value must be a scalar";
oversized container rejected by the length cap; nested-container element
rejected by coerce (shallow shapes).

## Task D — bare-token templating of path-bearing fields

The renderer is the EXISTING `render_template`
(domain/templates.py) — bare `{{ name }}` tokens only, `str()`
substitution. No Jinja, no dotted access, no filters. Pinned matching/
rendering functions (0.14 lesson — pin the function, not the concept):
token rendering = `render_template(text, resolved_inputs,
unknown_tokens="error")` wrapped by a new field-aware helper; post-render
path validation = the existing `safe_relative_path` + `confined_path`.

Contract:

- New helper `render_field(text: str, *, specs, values, field: str) -> str`
  (domain/templates.py; specs = recipe.inputs mapping, values = resolved
  per-target inputs). Behavior: scans bare tokens BEFORE rendering; a token
  naming a `sensitive: true` input →
  `ValueError("sensitive input {name!r} cannot be used in path field
  {field!r}")`; a token naming a structured input →
  `ValueError("structured input {name!r} cannot be rendered; hooks receive
  it natively")`; then renders via `render_template(...,
  unknown_tokens="error")` — fields are ALWAYS strict regardless of any
  step-level `unknown_tokens` setting (that setting governs template file
  BODIES only; record this in docs).
- Templated fields (rendered per target in the planner, AFTER input
  resolution, immediately before each handler uses the value):
  `TemplateStep.template` + `.dest`; `CopyStep.source` + `.dest`;
  `TransformStep.file`; `RemoveStep.file`; every `globs` and `exclude`
  entry (rendered BEFORE glob expansion). `files:` fan-out entries carry
  tokens through parse-time expansion into their single-file steps and
  render at plan time like any `file`. NOT rendered: `args` (hooks own
  args; yaml_edit renders values hook-side — engine-side would
  double-render), `hook` names, and every non-step surface.
- Post-render validation: the rendered string re-passes
  `safe_relative_path(field=...)` before the existing `confined_path`
  call — an input value of `../escape` or an absolute path MUST fail the
  target's plan with the existing safe-path error text. Parse-time
  validators are untouched (token-bearing strings already pass them
  harmlessly; the plan-time re-check is the enforcement point).
- `if_absent` exists-check uses the RENDERED dest. Zero-match glob warning
  reports the RENDERED patterns.
- `render_template` itself gains the structured-value guard: rendering a
  token whose value is a list/dict raises the same "structured input …
  cannot be rendered" error — this covers template file bodies too (new
  input types, so no compatibility concern; hook-side rendering via the
  exported helper inherits the same rule).

Test intent: `dest: "{{ service }}.yml"` renders per target (two targets,
from:-derived service → different dests); token in `globs`/`exclude`
renders before expansion; `files:` fan-out with a token entry; if_absent
against rendered dest; `../`-escape via input value fails with the
safe-path error (THE security test); absolute-path injection fails;
sensitive-in-path pinned error; structured-in-path pinned error;
structured-in-template-body pinned error; unknown token in a field errors
even when the step sets `unknown_tokens: keep`; tokenless fields behave
byte-identically (regression guard).

## Task E — quickies (code)

Contract:

- **Typed hook stubs** (infrastructure/pack_scaffold.py): scaffolded
  transform/validate stubs carry full parameter and return annotations
  (`content: str`, `inputs: dict[str, object]`, `target: Path`,
  `file: Path`, `args: dict[str, object]`, `helpers: "HookHelpers"`,
  transform `-> str`; validate return annotated with the helpers verdict
  type if cleanly importable under TYPE_CHECKING, else `-> object`). The
  scaffolded pack must remain mypy-clean when a user runs mypy inside it
  (add `Path` import to the stub preamble). Update the exact-content
  scaffold tests.
- **`_new_pack_child` hint** (cli/commands.py): the `pack not found:
  {pack}` error gains, when `Path(pack)` is an existing directory relative
  to cwd:
  ` (a directory named '{pack}' exists — use ./{pack}/{name}, or install
  it with add ./{pack})`. Existing exact-message assertions for misses
  with nothing on disk stay untouched (suffix extends, never replaces).

Test intent: scaffolded stub content updated in the exact-content tests +
a mypy run inside a scaffolded pack stays clean (extend the existing
scaffold-check test if it types-check; otherwise assert content only);
hint appears when ./demo exists, absent otherwise.

## Task F — docs batch

README + docs/recipes.md + docs/hooks.md + packaged SKILL.md (+ AGENTS.md
where contract surfaces are listed), covering:

- Structured inputs (shapes, --var YAML form, from: derivation, redaction,
  prompting limitation).
- Field templating: which fields, always-strict rule, per-target
  rendering, sensitive/structured prohibitions, the no-Jinja rationale
  (one language, expressions unrepresentable), and the from:-derived-input
  idiom for target/record context in paths.
- The args/inputs contract paragraph (engine passes args VERBATIM and
  never templates them; hooks receive inputs natively; yaml_edit +
  render_template helper template string arg values hook-side).
- YAML anchors as the structural-reuse pattern (worked example).
- Design-rationale section (docs/recipes.md or a new docs/design.md —
  implementer's choice, recorded): recipes are dumb by design, a decision
  is a hook, recipes own the input contract / hooks own args
  interpretation, plan is the only execution.

## Task G — version 0.15.0 + parity

- Version literals: pyproject.toml, `_version.py`, `uv.lock` (relock),
  `tests/test_hook_api_contract.py` `verify_versions("0.15.0")` (both call
  sites), `tests/test_infrastructure.py` wheel glob.
- `HOOK_API_VERSION` stays `0.9.0` — record the wire-compat reasoning
  (inputs object values may now be lists/dicts; JSON-representable, no
  protocol shape change) in the PR body release notes.
- Release-notes block: new input types (additive), field templating
  (additive; tokenless recipes byte-identical), scalar `--var` behavior
  unchanged, sensitive/structured path prohibitions.

## Self-review gates (before opening the PR)

**Field-walk** (every pinned field/flag to its producing type):
`list`/`dict` → `InputType` + `items`/`values` on `InputSpec` → `coerce`
element paths; YAML `--var` parse → `_coerce_fixed_values` gate;
prompting guard → `_resolve_one` interactive branch; `structured=` →
`ensure_derived_value_within_bound` + `_coerce_derived_value` call sites;
`render_field` → every path-bearing field listed in Task D → post-render
`safe_relative_path` + `confined_path`; structured-render guard →
`render_template` value substitution; stub annotations → `_hook_stub`
strings; hint → `_new_pack_child` raise site.

**Decision-walk** (every locked ruling maps to a task or explicit
deferral):

| Locked decision | Where |
|---|---|
| Bare-token language, no Jinja (ruling 1) | Task D (render_template reuse) |
| Dotted/sandboxed field access | DEFERRED — trigger = from:-input idiom insufficient in real packs |
| Path-bearing fields only; not args/hook names (ruling 2) | Task D field list |
| No `vars:` block; inputs-with-defaults + anchors (ruling 3) | Task F docs (pattern), no schema change |
| Non-overridable constants | DEFERRED — trigger = overridability causing real harm |
| Shallow list/dict shapes (ruling 4) | Task A |
| Recursive shapes / JSON Schema | DEFERRED — `items:` nesting is the extension path |
| `--var` YAML-parse for structured only (ruling 5) | Task B |
| from:/--input-from structured derivation, caps kept (ruling 6) | Task C |
| Sensitive × path fields forbidden (ruling 7) | Task D `render_field` guard |
| Quickies ride (ruling 8) | Tasks E + F |
| Manifest args-schema | DEFERRED — BACKLOG trigger updated (vocabulary ships here) |
| Structured-render error / prompting error / whole-value redaction (ruling 10) | Tasks D / B / B |
| 0.9-spec invariant: no control flow in schema | Task D (expressions unrepresentable in bare tokens) |
| Parking-lot disposition (8 items) | list inputs → A–C; typed stubs + hint → E; args/inputs doc + anchors doc + rationale → F; args-schema deferred; --register + vars:+Jinja-in-files remain rejected/deferred (DECISIONS) |

## Implementation-plan review (pre-implementation, adjudicated)

- No STOP-level technical contradiction found against `origin/main` at `77dae3351942e3c14af3646a3a6a604babd9b10c`.
- Task B: structured `--var` parsing uses `yaml.safe_load` — the same loader family as recipe/`--vars` intake. No loader change is approved.
- Task B: add explicit scalar byte-identical regression tests — scalar-declared inputs given `--var` values that look like YAML (`[a, b]`, `a: b`, `{x: y}`, `true`) must stay literal strings / follow existing scalar coercion, with unchanged error text.
- Task D: rendered field values become `Path` only after `render_field`, then immediately re-pass `safe_relative_path` with the ORIGINAL field name in the error text, then confinement against the correct base: `recipe_dir` for `source`/`template`, target root for `dest`/`file`/`files`/glob-expansion results.
- Task G: update every intentional `0.14.0` version/parity assertion found by grep (currently: `pyproject.toml`, `_version.py`, both `verify_versions` calls in `test_hook_api_contract.py`, wheel glob in `test_infrastructure.py`), not an enumerated subset. Re-grep before committing.
