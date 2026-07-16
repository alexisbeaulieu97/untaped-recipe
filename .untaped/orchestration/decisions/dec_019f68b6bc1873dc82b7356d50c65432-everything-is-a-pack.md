+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6bc1873dc82b7356d50c65432"
kind = "decision"
title = "Everything is a pack"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

One pack concept — a uv project whose top-level `pyproject.toml` declares
`[tool.untaped_recipe]` with optional `recipes` and `hooks` tables — is the single
library and sharing unit. There is one `PackLibrary` backed by one `packs.toml`.

**Rationale.** The design previously carried three near-identical shapes (recipe,
pack, hook-project), which tripled the learning and sharing surface. Collapsing
them to one shape happened while the sole-user window made the break cheap.

**Consequences.**

- **Manifest entries are explicit.** Both `recipes` and `hooks` are declared; there
  is no `recipes/*/recipe.yml` auto-discovery.
- **Installed packs stay byte-identical to their source.** Source tracking
  (`source`, `rev`, `version`, `content_hash`) lives in `packs.toml`, not inside
  installed pack directories.
- **A single-file recipe stays runnable by explicit path** for quick local use, but
  is never installed as a library item — reusable recipes and hooks live in a pack.
- **One reference grammar, no disk-sniffing.** A value is a path only when it starts
  with `./`, `../`, `/`, or `~`, or ends in `.yml`/`.yaml`; anything else is a
  library ref (`pack/name`, or a bare name when unique) and is never reclassified by
  probing whether a matching path happens to exist. (The lesson generalizes an
  earlier pipe-ambiguity bug: never let on-disk state silently reclassify an
  argument's kind.)
