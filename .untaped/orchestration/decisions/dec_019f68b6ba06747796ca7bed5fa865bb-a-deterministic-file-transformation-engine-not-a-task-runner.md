+++
schema = "untaped.orchestration.decision/v1"
id = "dec_019f68b6ba06747796ca7bed5fa865bb"
kind = "decision"
title = "A deterministic file-transformation engine, not a task runner"
created_at = "2026-07-08T22:30:57.000Z"
tags = []

[[evidence]]
relation = "tracked-by"
reference = "url:https://github.com/alexisbeaulieu97/untaped-dev/blob/643303ae0eab942956c48df627582f406ed5ec5b/orchestration/migration-inputs/untaped-recipe/0fd6f81/docs/decisions.md?original_repository=alexisbeaulieu97%2Funtaped-recipe&original_oid=0fd6f8164329477f4627ba68987ed56ebea4ccb5&original_path=docs%2Fdecisions.md&original_reachability=local-only#sha256:90f4b10e963b4e8b2027c0dacffd2211c0abc63f2310fdd52ee0106f6d7c1fb4"
+++

`untaped-recipe` is a deterministic transformation engine over file trees —
"moderne for files, driven by hooks." A recipe's scope is anything expressible as
planned file edits: version migrations, bulk config rewrites, scaffolding, drift
checks. The comparison that holds is OpenRewrite/moderne (transformation recipes,
previewable at scale), not Ansible (general task execution). "You could write a
recipe for anything" is bounded to *anything that is a file transformation*.

This is the domain lock (AGENTS.md invariant #0) and it settles every future scope
argument. The **never-build** list falls directly out of it:

- **No exec/shell/API step types.** They kill truthful preview; this is the
  boundary decision itself. "Ensure"-style capabilities enter as *planned*
  mutations resolved at planning time, never as execution-time convergence.
- **No control flow in the recipe schema** (`when:`, loops). A decision is a hook.
- **No state, inventory, or remote execution.** Targets come from arguments and
  pipes.
- **No hook sandbox** (see #6).

**Consequences.** Agent-authored packs are a first-class use case precisely
because a human reviews the *plan*, not the agent. Follow-up commands ("now run
`uv lock`") are data: recipes may *declare* them and preview/outcome may *display*
them, but the engine never executes them.
