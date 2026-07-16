
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
