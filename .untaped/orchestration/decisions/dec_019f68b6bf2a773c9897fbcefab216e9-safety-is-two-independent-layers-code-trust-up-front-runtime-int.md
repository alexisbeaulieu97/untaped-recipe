+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6bf2a773c9897fbcefab216e9"
kind = "decision"
title = "Safety is two independent layers: code trust up front, runtime integrity at write time"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

Whether a pack's *code* is safe to run at all, and whether a *write* is confined
and recoverable, are separate concerns handled by separate mechanisms.

**Code trust — the pip model, no sandbox.** Installing a pack is installing code,
on the same trust model as `pip install`. There is deliberately no sandbox;
sandboxing trusted local code is the wrong investment. The mitigations are
evaluate-before-trust surfaces that make what you are trusting *visible*: the `add`
confirmation listing what comes in, structured `show`, `check`'s no-import AST scan,
and the golden test harness (#7). The tool's job is to surface what you are
trusting, not to pretend the code is contained.

**Runtime integrity — confinement plus VCS-agnostic backups.** Every recipe-local
and target-relative path is validated as a safe relative path (no absolute, no
`..`, no bare `.`, no symlink component, no escape) before any read or write, and
re-validated after per-target rendering. Every apply captures one backup bundle by
default, storing pre-apply text content and before/after hashes; restore reuses the
same transactional, symlink-confined write path and refuses to overwrite a file
that changed since the backup unless `--force` is passed.

Backups are **deliberately VCS-agnostic** — text content, on by default, no git
awareness — because the tool itself is VCS-agnostic and must protect targets that
are not under version control. This holds even though many real targets happen to
be clean git checkouts: git-aware backup skipping was evaluated and the
VCS-agnostic default was kept. Three distinct integrity hashes each guard a
different thing and should not be conflated: the install content hash (pack drift),
the backup before/after hashes (recovery + restore guard), and `uv.lock` freshness
(pack lockfile staleness at check time).
