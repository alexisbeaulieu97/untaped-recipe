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
outcome rows). A normal apply opens with the aggregate summary line, then a
file-level table:

```
Recipe preview: 1 target, 1 changing, 0 unchanged, 0 failed, 2 files changed
╭───────────────────────────────────┬────────┬─────────╮
│ path                              │ action │ changes │
├───────────────────────────────────┼────────┼─────────┤
│ /home/me/service-a/config/app.yml │ modify │ +3 -1   │
│ /home/me/service-a/config/db.yml  │ create │ +12 -0  │
╰───────────────────────────────────┴────────┴─────────╯
```

Every preview opens with that exact aggregate summary line — targets, changing,
unchanged, failed, and files changed — and that summary is re-echoed at the
confirmation prompt. When any target is [skipped](#target-statuses-and-exit-codes),
a `N skipped` count joins the line and the run summary.

### Preview modes

`--preview` selects how much detail accompanies the summary line:

| Mode | Default for | Shows |
| --- | --- | --- |
| `--preview table` | a normal apply and `--dry-run` | file-level table of changed files: absolute path, change kind, and per-file line counts |
| `--preview diff` | — | patch-compatible unified diffs with `a/` and `b/` relative paths — the full-detail escape hatch |
| `--preview none` | `--check` | only the summary line |

`--check` defaults to `--preview none` for quiet CI output; pass `--preview table`
or `--preview diff` in check mode when you want the detail.

### Large-plan collapse

Table previews stay file-level only while the total changed-file count is at or
below the `preview_max_rows` setting (default `50`; `0` means unlimited):

- **At or below the threshold** — one row per changed file.
- **Above the threshold** — the table collapses to one row per target, with the
  target's file count and aggregate `+adds -dels`.
- **When the target rows themselves exceed the threshold** — the table is
  truncated to the first `preview_max_rows` targets, and a
  `showing first N of M targets` notice points at `--preview diff` for full
  detail.

Collapsed previews are summaries backed by the exact totals in the summary line,
never partial-success claims. See [reference](./reference.md) for the
`preview_max_rows` setting.

### Sensitive targets

File-level preview detail and diffs are suppressed for any target that has
sensitive inputs, because generated content may embed secret values; such
targets appear as a target/files-changed row instead. The `sensitive` semantics
that trigger this are owned by [inputs](./inputs.md#sensitive-inputs).

## Confirmation and `--yes`

```bash
untaped-recipe apply add-config ./service-a --yes
```

After previewing, `apply` asks for confirmation before writing. `--yes` (`-y`)
skips the prompt for non-interactive runs. When targets come from `--stdin`,
`--yes` is **required** before planning unless the run is `--dry-run` or
`--check`; a piped apply without one of those is refused up front so a stream of
records cannot write silently.

## Dry run and check

```bash
untaped-recipe apply add-config ./service-a --dry-run
```

`--dry-run` plans and previews, then reports outcome rows, without writing or
creating any backup.

```bash
untaped-recipe apply add-config ./service-a --check
```

`--check` is the CI/compliance mode: it previews without writing, creates no
backups, prompts for nothing, and exits non-zero when any target would change or
fail. Check-mode outcome rows carry `status: check`. `--check` cannot be combined
with `--interactive`. A skipped target is **not** drift: it stays `skipped` and
does not, on its own, make `--check` exit non-zero.

## Target statuses and exit codes

Each target ends in one status, reported in its outcome row and counted in the
run summary (for example `8 targets: 5 applied, 2 unchanged, 1 skipped`):

| Status | Meaning | Counts as failure? |
| --- | --- | --- |
| `applied` | changes were written | no |
| `unchanged` | the target was already conformant | no |
| `skipped` | a validate hook returned `helpers.skip(...)` — not applicable; no changes, no backup | no |
| `error` | planning or writing failed for that target | yes |
| `check` / `dry-run` | preview-only status for a target that *would* change | see below |

`apply` exits **non-zero** when any target is `error` (or a write fails). Under
`--check` it also exits non-zero when any target *would* change. Skips are always
success. An all-skip run exits 0 and creates no backup bundle. Statuses and the
`recipe.outcome` schema are owned by [pipes](./pipes.md).

## Failure isolation and transactional writes

Failures are contained to a single target. A target whose plan fails writes
nothing and is reported as an error; the remaining targets still apply.

Within a target, the write is transactional:

- **Stage** — planned changes are staged to temporary files.
- **Verify** — the engine re-verifies each file's on-disk content against what
  planning saw and aborts that target's write if the file changed since planning.
- **Swap** — staged files are swapped into place atomically.
- **Rollback** — if any file in the target cannot be written, the already-applied
  files for that target are rolled back to their pre-apply content and the target
  is reported as failed. If a rollback cannot fully complete, the per-target error
  says so.

Backups are created before writing and are the recovery path for anything beyond
a single target — see [safety](./safety.md).

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
