
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
