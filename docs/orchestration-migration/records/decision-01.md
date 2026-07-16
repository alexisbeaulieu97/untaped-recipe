
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
