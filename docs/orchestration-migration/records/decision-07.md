
Recipe-level behavior is pinned by in-pack golden-fixture cases under
`tests/<recipe>/<case>/` with `given/` and `expected/` trees. A test run plans
through the *same* planner as `apply` against a temporary copy, then does a
full-tree byte comparison in a single golden format. `case.yml` is optional and
**data-only** — inputs, an `expect` flag, an `error_contains` substring, verdict
assertions — with unknown fields rejected and no control flow or assertion DSL.

**Rationale.** Recipe-level behavior was previously unpinnable, and data-only cases
are what keep the anti-DSL invariant intact: the moment the test layer grows a
mini-language, the tool has a second programming surface to learn and maintain.
Hook-level logic (branching, parsing) belongs in ordinary hook pytest, which is
cheap because hooks are pure functions callable directly with no worker.

**Consequence.** `check` is the static complement: it validates structure and
wiring — including AST-scanning each resolved hook module for the required exported
function — without ever importing or running hook code.
