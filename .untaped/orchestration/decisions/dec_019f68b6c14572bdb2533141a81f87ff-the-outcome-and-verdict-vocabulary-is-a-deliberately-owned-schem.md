+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6c14572bdb2533141a81f87ff"
kind = "decision"
title = "The outcome and verdict vocabulary is a deliberately-owned schema"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

Per-target outcome statuses and validate verdicts are a first-class, deliberately
curated vocabulary, surfaced identically in the stderr preview and in the
`recipe.outcome` pipe rows. Non-fatal reporting from both hook kinds converges on a
single warn accumulator rather than fragmented ad-hoc channels — a `fail` verdict
aborts a target before any write, a `warn` is recorded and continues.

**Rationale.** Because this vocabulary is both a human-facing preview and a
machine-readable wire contract, it must be curated as a schema, not accreted
per-feature. Keeping one warn accumulator (instead of a separate string field, a
naming-collided helper, and a preview blind spot) is what makes non-fatal reporting
consistent across validate and transform hooks.

**Consequence.** The vocabulary evolves only through *deliberate schema changes*,
taken while the sole-user window keeps such breaks cheap and gated on the hook-API
floor (#4) — for example, adding a distinct skip verdict so a hook can report a
no-op-by-design outcome as something other than a warning. Producers own their
record fields, but the shared status/verdict names are governed here.
