+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6bb0e75d8a9dd9fd5832740ea"
kind = "decision"
title = "Truthful preview is the product: plan → preview → confirm → flush"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

Planning is the only execution; a write is nothing more than a flush of the plan
buffer. Every target is planned in full, in memory, before any byte is written;
the preview renders that buffer to stderr; only after confirmation are successful
target plans flushed. This ordering is what makes the preview trustworthy, and the
trustworthy preview is the product.

**Rationale.** The moment anything ran outside the plan buffer at apply time, the
preview would lie — so nothing does. The same discipline drives the surrounding
guarantees:

- **Per-target failure isolation and transactional writes.** A target that fails
  to plan or write is contained and reported; the rest still apply. Within a
  target, writes stage → re-verify against what planning saw → swap atomically →
  roll back on failure.
- **Collapsed previews are summaries, never partial-success claims.** A preview is
  truthful when totals are exact and everything hidden is counted and reachable
  (`--preview diff`). Large plans may collapse to per-target rows, but the
  aggregate summary line stays exact and is re-echoed at the confirmation prompt.
- **stdout is data only.** Diffs, prompts, progress, and status go to stderr, so a
  structured stream stays clean for a downstream consumer.
