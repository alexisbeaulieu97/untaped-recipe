
Recipes stay declarative and intentionally dumb: a recipe owns the input contract,
file paths, step ordering, and hook arguments. A hook owns decisions — parsing,
branching, validation, nontrivial edits. The plan is the execution record produced
after inputs resolve, not a second programming surface.

**Inputs are typed, and structured inputs are shallow.** Scalars (`str`/`int`/
`bool`/`float`) coerce from the command line, `from` derivations, and defaults;
`list`/`dict` inputs carry a scalar element type and reach hooks as native Python
values, one level deep by design. Per-target values may derive with `from` in a
*restricted, sandboxed* native-Jinja environment that permits only literals plus
attribute/item access on `target`/`record` — control blocks, filters, calls, and
operators are rejected at load time, because `from` exists only to derive declared
values, never to change recipe structure.

**Templating is two surfaces, both deliberately small.** Template *bodies* use the
`{{ name }}` token language with an `unknown_tokens: error|keep` policy so a
template can intentionally emit another tool's syntax. Path-bearing *fields* accept
the same bare tokens but render **always-strict** and are re-checked as confined
relative paths after rendering. Field templating is confined to path-bearing fields
only — never hook `args` (hooks own their args and render them themselves) and never
hook names or step types (that would be input-driven control flow).

**Things ruled out and why:**

- **No `vars:` block.** A constant is just an input with a default; YAML anchors
  cover structural reuse across steps. Adding `vars:` would grow a second value
  namespace for no capability.
- **Sensitive and structured inputs are forbidden in path fields.** Paths are
  preview/outcome identity and are stored in plans and backups — they cannot redact
  a secret, and a list/dict has no meaningful single-path rendering.

The payoff of the split: scalar input behavior stays byte-identical to earlier
recipes, structured data flows to hooks as data, and engine templating never has to
grow into a general expression language.
