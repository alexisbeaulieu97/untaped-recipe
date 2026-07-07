# Running recipes

`apply` is how a recipe actually runs against target directories. This page
covers the invocation forms, where targets come from, and the plan → preview →
confirm → write flow — including the preview modes, the confirmation and `--yes`
rules, `--dry-run` and `--check`, per-target failure isolation, transactional
writes, and the parallelism and timeout controls. For the recipe schema itself
see [recipes](./recipes.md); for path safety and backups see [safety](./safety.md).

## Invocation forms

```bash
untaped-recipe apply add-config ./service-a ./service-b --var service=api
untaped-recipe apply ansible/playbook-migration ./service-a --yes
untaped-recipe apply ./pack-project ./service-a --recipe playbook-migration --yes
untaped-recipe apply ./recipe.yml ./service-a --yes
```

The first argument selects the recipe. It may be a bare recipe name, a
`pack/recipe` ref, an explicit path to a single-file `recipe.yml`, or an explicit
local pack path combined with `--recipe`. Whether the engine reads the first
argument as a library ref or a filesystem path is governed by one grammar, owned
by [packs](./packs.md#references): a value is a path only when it starts with
`./`, `../`, `/`, or `~`, or ends in `.yml`/`.yaml`; anything else is a library
ref and is never reclassified by probing the disk.

## Target sources

Targets are the directories a recipe is applied to. Provide them either as
positional directory arguments or on stdin with `--stdin`, never both — passing
both is a usage error.

```bash
untaped-recipe apply add-config ./service-a ./service-b --var service=api
find services -maxdepth 1 -type d | untaped-recipe apply add-config --stdin --yes
```

With `--stdin`, targets are read as bare paths or as untaped pipe records, so
another tool's output can drive the run. The parsing rules for those records —
bare paths versus JSON objects, `target_path` before `path`, `.summary` skips,
and the repo-grain requirements — are owned by [pipes](./pipes.md#reading-targets-from-stdin).

## Plan before write

Every target is planned in full before any write begins. Planning renders each
step into an in-memory buffer of file changes; nothing touches the target tree
until planning has succeeded and confirmation has been given. A planning failure
for one target is recorded as an error on that target and does not block the
other targets from planning or applying.

## Preview

The plan is previewed on **stderr** (stdout stays reserved for structured
outcome rows). Every preview opens with an exact aggregate summary line —
targets, changing, unchanged, failed, and files changed — and that summary is
re-echoed at the confirmation prompt. `--preview` selects how much detail
accompanies it:

- `--preview table` (the default for a normal apply and for `--dry-run`) shows a
  file-level table of changed files with absolute paths, change kind, and
  per-file line counts.
- `--preview diff` shows patch-compatible unified diffs with `a/` and `b/`
  relative paths — the full-detail escape hatch.
- `--preview none` prints only the summary line.

`--check` defaults to `--preview none` for quiet CI output; pass `--preview table`
or `--preview diff` in check mode when you want the detail.

Table previews stay file-level only while the total changed-file count is at or
below the `preview_max_rows` setting (default `50`; `0` means unlimited). Above
that threshold the table collapses to one row per target with the target's file
count and aggregate `+adds -dels`; once the number of target rows itself exceeds
the same threshold, the table is truncated to the first `preview_max_rows`
targets and a `showing first N of M targets` notice points at `--preview diff`
for full detail. Collapsed previews are summaries backed by the exact totals in
the summary line, never partial-success claims. See
[reference](./reference.md) for the `preview_max_rows` setting.

File-level preview detail and diffs are suppressed for any target that has
sensitive inputs, because generated content may embed secret values; such
targets appear as a target/files-changed row instead. The `sensitive` semantics
that trigger this are owned by [inputs](./inputs.md#sensitive-inputs).

## Confirmation and `--yes`

After previewing, `apply` asks for confirmation before writing. `--yes` (`-y`)
skips the prompt for non-interactive runs. When targets come from `--stdin`,
`--yes` is **required** before planning unless the run is `--dry-run` or
`--check`; a piped apply without one of those is refused up front so a stream of
records cannot write silently.

## Dry run and check

`--dry-run` plans and previews, then reports outcome rows, without writing or
creating any backup.

`--check` is the CI/compliance mode: it previews without writing, creates no
backups, prompts for nothing, and exits non-zero when any target would change or
fail. Check-mode outcome rows carry `status: check`. `--check` cannot be combined
with `--interactive`.

## Failure isolation and transactional writes

Failures are contained to a single target. A target whose plan fails writes
nothing and is reported as an error; the remaining targets still apply.

Within a target, the write is transactional. Planned changes are staged to
temporary files and swapped into place atomically; if any file in the target
cannot be written, the already-applied files for that target are rolled back to
their pre-apply content and the target is reported as failed. The engine also
re-verifies each file's on-disk content against what planning saw and aborts that
target's write if the file changed since planning. If a rollback cannot fully
complete, the per-target error says so. Backups are created before writing and
are the recovery path for anything beyond a single target — see
[safety](./safety.md).

## Parallelism and timeouts

`--parallel N` (`-j N`) plans targets concurrently and sizes the per-hook-project
worker pool, clamped to a maximum of 32 workers. `--interactive` forces
single-target planning so prompts stay ordered.

`--hook-timeout SECONDS` overrides the configured per-hook request timeout for
this run (`0` disables it for trusted long-running hooks). The timeout, the
worker-pool model, and the separate environment-startup bound are owned by
[hooks](./hooks.md#execution-model).

## Output scope

Outcome rows are written to stdout as `recipe.outcome` records; their schema,
the `--format`/`--columns` controls, and the redaction rules are owned by
[pipes](./pipes.md). `--quiet` (`-q`) mutes only post-run
success chatter — it does not silence selected preview detail, warnings, errors,
or the destructive confirmation prompt.
