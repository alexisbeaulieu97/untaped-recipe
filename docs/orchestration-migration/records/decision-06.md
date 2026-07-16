
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
