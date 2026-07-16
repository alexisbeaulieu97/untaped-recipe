# Orchestration v1 migration review

## Verdict

**ACCEPT — no Critical, Important, or Minor findings.**

- Reviewed range: `3cf3df1559893f0a5b0cb3addb4f2216f6fc0e7b..8aef2c3592cd7ac827a9f905f8d7ed11ebb0ada7`
- Independent reviewer: Codex review subagent
- Review date: 2026-07-15
- Source SHA-256: `90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4`
- Durable source OID: `643303ae0eab942956c48df627582f406ed5ec5b`
- Original local-only OID: `0fd6f8164329477f4627ba68987ed56ebea4ccb5`

## Mechanical verification

The durable hub snapshot resolves on GitHub at the path recorded in
`coverage.toml`. It is exactly 13,241 bytes and 221 LF-terminated lines, has no
CR bytes, and is byte-identical to the available original local-only object.
All 16 coverage blocks were independently recomputed: their ranges are gapless
over lines 1–221, their byte counts total 13,241, and every block SHA-256
matches. This includes the complete 421-byte preamble and its terminal LF.

All eight numbered decision headings map to the committed metadata titles by
removing only the ordinal. For each decision, every byte after the heading is
identical in the import body and canonical decision body; heading plus body
reconstructs the frozen source block exactly. IDs, timestamps, source order,
and the query-bearing durable `source_ref` are exact and uniform.

## Semantic and operational verification

The pointer preserves both source ownership meanings: root `AGENTS.md` owns
permanent invariants and the concept pages under `docs/` own behavior facts.
Root instructions contain the decision ownership row and Decisions link while
preserving existing Recipe rules. The store is public, UTC, childless, and
decision-only with exactly eight decisions and no tasks.

The full released `untaped-orchestration==0.1.0` flow was independently
reproduced in a fresh store: guarded dry-run previewed eight records without
writes, apply produced the locked state, replay returned
`already_present=true` for all records without changes, and a task-create probe
returned ORC009 without changing the revision. Released `check --local`,
`fmt --check --local`, and `render --check` all pass.

The workflow is read-only, path-filtered, exactly pinned, and runs only the
released package. Runtime/editor ignores do not hide canonical state, views,
or migration evidence. Focused tests, pre-commit, Ruff, formatting, strict
mypy, the full 568-test suite at 86.76% coverage, build, and base-to-head diff
checks all passed. No repository-scope or package artifacts changed outside
the accepted adoption surface.
