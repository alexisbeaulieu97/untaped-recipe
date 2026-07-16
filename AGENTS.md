# AGENTS.md - `untaped-recipe`

The contract for working in this standalone CLI repo: mission, invariants,
hard rules, architecture, the documentation contract, and the development and
release workflows. Behavior facts — command semantics, schemas, wire contracts
— live in the concept pages under [docs/](./docs/), not here.

## Mission

`untaped-recipe` applies trusted local recipe packs and explicit recipe files
across one or more plain directories. It is intentionally VCS-agnostic: no
clone, branch, git diff, commit, push, or PR behavior belongs here. Workspace
selection can come from another tool through stdin or pipe records, but this
repo owns only recipe execution, previews, backups, and restore.

## Domain (invariant #0)

**untaped-recipe is a deterministic transformation engine over file trees — "moderne
for files, driven by hooks."** A recipe's scope is anything expressible as planned
file edits: version migrations, bulk config rewrites, scaffolding, drift checks. The
comparison that holds is OpenRewrite/moderne (transformation recipes, previewable at
scale), not Ansible (general task execution): "you could write a recipe for anything"
is bounded to *anything that is a file transformation*. This sentence wins every
future scope argument and joins AGENTS.md alongside the Wave-3 invariants.

Two consequences are load-bearing:

- **Truthful preview is the product.** Plan → preview → confirm → flush is only
  trustworthy while every step is a planned file mutation. Agent-authored packs are a
  first-class north-star use case precisely because the human reviews the plan, not
  the agent; anything that executed at apply time outside the plan buffer would make
  the preview lie.
- **Follow-up commands are data.** Real migrations often end with "now run `uv lock`"
  or "re-run the formatter". Recipes will be able to *declare* follow-ups; preview
  and outcome *display* them; recipe never executes them (model locked, design
  deferred — see Deferred designs).

**Never build** (recorded with reasons so future scope arguments end here):

- Exec/shell/API step types — they kill truthful preview; this is the boundary
  decision itself. "Ensure"-style capabilities enter as *planned* mutations resolved
  at planning time (see Deferred designs), never as execution-time convergence.
- Control flow in the recipe schema (`when:`, loops) — a decision is a hook.
- State, inventory, or remote execution — targets come from arguments and pipes.
- Hook sandboxing — packs are trusted code, on the same model as `pip install`; the
  mitigations are evaluate-before-trust surfaces (`show`, `check`'s AST scan, the
  `add` confirmation, the 0.10 harness), not a sandbox.

## Permanent Invariants

- AGENTS.md gains permanent invariants:
  1. Control flow never enters the recipe schema; a decision is a hook.
  2. Planning is the only execution; writes are a flush of the plan buffer.
  3. No state, no inventory; targets come from arguments and pipes.
  4. Builtins stay minimal; typed uv hook packs are the extension story.
  5. The hook boundary stays pure data (stdlib-only worker, JSON protocol, no runtime
     import of untaped-recipe in hooks).
  6. Pipe composability is a protected feature: `apply --stdin` ingests untaped
     NDJSON envelopes (kind tags, `target_path`), and input `from` expressions can
     read the piped `record` — other untaped tools' output drives recipes.
  7. Hooks are pure at planning time: they may read the target tree and their own
     pack directory (deterministic inputs), and must never write, reach the network,
     or read outside those roots. Planning's truth depends on it; the 0.10 harness
     catches violations as unstable goldens.
  (AGENTS.md also carries invariant #0, the domain lock from §Domain.)

## Hard Rules

1. Follow the Documentation contract below: a behavior change updates its
   owning concept page and re-derives every derived surface (README, packaged
   SKILL.md) in the same change.
2. Use the untaped SDK entry point in `src/untaped_recipe/__main__.py`.
   The `ToolSpec` command is `untaped-recipe`, the settings section is
   `recipe`, and the profile model is `RecipeSettings`.
3. Keep the package root import-light. `untaped_recipe.__init__` may lazily
   re-export `app`, but importing the package must not eagerly import the CLI.
4. Use the four-layer layout:
   `cli/` for command signatures, `application/` for use cases and ports,
   `domain/` for pure models, and `infrastructure/` for filesystem, hook,
   backup, diff, and YAML adapters.
5. Use absolute imports. CLI code may import from `untaped.api`; tests may use
   `untaped.testing`.
6. stdout is data only. Diffs, prompts, and status messages go to stderr.
7. Do not add shell-command steps without a new design review. V1 writes are
   engine-mediated so preview, backups, and per-target transactional writes stay
   coherent.
8. External Python hooks are trusted local uv pack hooks executed by pooled
   workers. Built-ins are engine-owned direct imports. Do not reintroduce
   importlib file loading for arbitrary `.py` hooks, pluggy, or PEP 723 hooks
   without a new design review.
9. Backups are on by default for applies. Restore refuses to overwrite edits
   made after the backup unless `--force` is passed and uses the same
   transactional, symlink-confined write path as apply. Backups store text
   content for engine-managed files and do not preserve mode or mtime.
10. Finish changes with the development workflow below.

## Architecture

```text
src/untaped_recipe/
├── __main__.py          # SDK ToolSpec and console-script entry point
├── settings.py          # recipe settings section
├── cli/                 # Cyclopts commands, output rows, preview rendering, test_commands.py
├── application/         # apply/check/golden-test harness use cases, hook run, ports
├── domain/              # schema, verdicts, file changes, plans
├── infrastructure/      # libraries, hook resolution/execution, backups, diffs, YAML
├── builtins/hooks/      # packaged trusted transform hooks
└── skills/              # packaged agent skill
```

The `application` layer plans all target changes in memory and owns hook-run
fixture validation plus hook invocation; the CLI renders stderr previews from
`cli/preview.py` and flushes successful target plans only after confirmation
and one backup bundle has been created for the invocation. Pipe target parsing
preserves optional untaped record context for per-target input derivation;
`ApplyRecipe` receives only a concrete target path plus resolved plain inputs.
The behavior contracts (preview modes, record-parsing rules, outcome rows) are
documented in [apply](./docs/apply.md) and [pipes](./docs/pipes.md).

## Documentation contract

**Every behavior fact has exactly one owning page; README and SKILL.md link or
distill, never originate.** A behavior change updates its owning page and every
derived surface in the same change. The fleet-wide rules live in the core
[documentation standard](https://github.com/alexisbeaulieu97/untaped/blob/main/docs/documentation.md).

| Concept | Owning page |
| --- | --- |
| Recipe YAML schema, step types, fan-out/globs, `if_absent`/`optional`, hook `args` + anchors, design rationale | [docs/recipes.md](./docs/recipes.md) |
| Input contract: spec fields, types, scope, `sensitive`, `from` sandbox, precedence, `--var`/`--vars`/`--input-from`/`--interactive` | [docs/inputs.md](./docs/inputs.md) |
| `{{ name }}` token language, `unknown_tokens`, path-field rendering and confinement recheck | [docs/templating.md](./docs/templating.md) |
| Hook contract, resolution, execution model, helpers, `requires_hook_api`, `hook run`, built-in `yaml_edit` | [docs/hooks.md](./docs/hooks.md) |
| Pack manifest and identity, library layout, reference grammar, `add`/`list`/`show`/`edit`/`remove`, scaffolding, trust stance | [docs/packs.md](./docs/packs.md) |
| Running recipes: target sources, plan-before-write, preview, confirmation, `--dry-run`/`--check`, failure isolation, parallelism | [docs/apply.md](./docs/apply.md) |
| Path safety, backups and restore, retention, integrity-mechanisms contrast | [docs/safety.md](./docs/safety.md) |
| `check` preflight, golden `test` harness, `case.yml`, hook unit tests | [docs/testing.md](./docs/testing.md) |
| Pipe ingestion rules, emit-kind table, `recipe.outcome` schema, `--format`/`--columns` | [docs/pipes.md](./docs/pipes.md) |
| Settings table, command index, exit codes, skills install | [docs/reference.md](./docs/reference.md) |
| Durable architecture decisions and rationale | [docs/decisions.md](./docs/decisions.md) |
| Release runbook | [docs/release.md](./docs/release.md) |

## Orchestration store

The repository has a public decision-only orchestration store; tasks are forbidden.
Use `untaped-orchestration` for canonical reads and mutations, including revision guards
on every mutation. Agents never use `--force-current`. The committed views are
human-only generated state, not tool input. After hand recovery, run
`untaped-orchestration check --local` and `untaped-orchestration render --check`.

## Release Workflow

Use `.github/workflows/release.yml` for PyPI releases; the runbook is
[docs/release.md](./docs/release.md). Non-negotiables: do not manually create a
GitHub release/tag for a version whose PyPI package has not been published and
verified; PyPI versions are permanently burned once uploaded (bump the patch
version in root `pyproject.toml` and `src/untaped_recipe/_version.py` rather
than retrying); the SDK dependency `untaped>=3.0.0,<4` must resolve from PyPI
before publishing. Bump `HOOK_API_VERSION` and the derived `requires_hook_api`
scaffold floor only when the helper contract changes.

## Development Workflow

```bash
uv sync
uv run pre-commit run --all-files
uv run ruff check --fix
uv run ruff format
uv run mypy
uv run pytest
uv build
git diff --check --cached
```

Use `uv --cache-dir .uv-cache run ...` when working from the
`untaped-dev` symlinked workspace.

## See Also

- Decisions: [docs/decisions.md](./docs/decisions.md)
- Core SDK: https://github.com/alexisbeaulieu97/untaped
