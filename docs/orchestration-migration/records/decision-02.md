
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
