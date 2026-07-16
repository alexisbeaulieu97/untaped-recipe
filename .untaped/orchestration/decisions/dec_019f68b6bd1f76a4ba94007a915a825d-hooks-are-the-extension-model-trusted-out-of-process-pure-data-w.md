+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6bd1f76a4ba94007a915a825d"
kind = "decision"
title = "Hooks are the extension model: trusted, out-of-process, pure-data wire contract"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

Builtins stay minimal; typed uv hook packs are the extension story. The hook
boundary is deliberately narrow and stable.

- **The exported function name is the contract.** A hook module exports
  `transform()`, `validate()`, or both; the recipe step `type` selects which runs.
  Hook manifest rows carry no `kind`.
- **External hooks run out-of-process under locked uv execution** (`uv run --locked
  --no-dev`) through a newline-delimited JSON worker protocol. Worker stdout is
  protocol-only; engine-side models validate every response before a change enters
  a plan. Built-ins are engine-owned and run in-process.
- **The hook boundary stays pure data.** The worker is stdlib-only; a hook never
  imports `untaped-recipe` at runtime. This keeps hooks decoupled from engine
  internals and safe to run in a `--no-dev` environment.
- **The helper API is versioned independently.** A `requires_hook_api`
  compatibility floor tracks the *helper-API contract*, not the CLI release cadence,
  so it moves independently of tool version bumps and the engine fails fast when the
  installed helper API is older than a pack requires.
- **Locked execution implies the lockfile rules.** A hook-declaring pack must ship a
  `uv.lock`, and `check` runs `uv lock --check` so a stale lock fails at check time,
  not hook-run time. Hookless and recipe-only projects need no lockfile and are
  exempt from the probe.

**Rejected alternatives** (do not reintroduce without a new design review):
importlib file loading for arbitrary `.py` hooks, pluggy, and PEP 723 hooks. Each
would erode the stdlib-only, locked, out-of-process guarantees above.

**Planning-time purity.** Hooks are trusted code, but hook behavior must be pure at
planning time: a hook may read the target tree and its own pack directory
(deterministic inputs) and must never write, reach the network, or read outside
those roots. Planning's truth depends on it, and the golden harness (#7) catches
violations as unstable goldens.
