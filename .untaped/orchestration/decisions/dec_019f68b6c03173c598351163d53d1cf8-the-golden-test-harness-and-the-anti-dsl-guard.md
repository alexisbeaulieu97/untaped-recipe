+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6c03173c598351163d53d1cf8"
kind = "decision"
title = "The golden test harness and the anti-DSL guard"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

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
