# Real-Pack Exercise — Protocol Design

**Date:** 2026-07-07
**Status:** Draft — awaiting Alexis approval
**Type:** Evidence-collection protocol. This package changes **no recipe source
code**. Its output is evidence, rulings, and a follow-up wave brief.

## Goal

Exercise untaped-recipe 0.15.1 end-to-end as a real pack author and operator,
against a simulated repo fleet, to produce adjudication evidence for the
deferred architecture challenges that name the real-pack exercise as their
trigger:

| Challenge (BACKLOG row) | Question the exercise must answer |
|---|---|
| Tiered zero-dep hook execution | Does the worker/lock path impose a material authoring toll on zero-dep packs that in-process execution would delete? |
| Git-aware backup skip | Is the target population mostly clean git checkouts, and is backup cost material? (Evidence only — challenges a locked decision; Alexis rules.) |
| `input-from` simplification | Do real packs hit the sandboxed-Jinja walls, and would plain dotted paths have covered the real derivations? |
| `add --editable` | Does the edit → re-add round-trip recur as real friction (evidence #2+), or is the 0.13 hash-guard path adequate? |

Riders that accrete evidence but are **not** adjudicated here: `yaml_edit
ensure` op (adopter-#2 counter), transform warn channel (trigger = first real
pack needing it), misc UX paper-cuts (parking lot).

## Non-goals

- Fixing anything mid-exercise. The tool is frozen at released 0.15.1 so
  friction is measured against a fixed target. Even the already-ruled
  `add`/`check` uv.lock asymmetry is only *logged* here; it ships in the
  follow-up wave.
- Deciding the git-aware backup question. The exercise produces population
  evidence and a skip-semantics sketch; the ruling is Alexis's because it
  challenges the locked VCS-agnostic decision.
- Byte-mode binary handling and `state: dir` — their rows keep their own
  triggers; incidental evidence gets logged to them, nothing more.

## Deliverables

1. This protocol spec (draft PR, reviewed before execution).
2. A reproducible simulated fleet: one builder script, committed under
   `docs/superpowers/exercises/2026-07-07-real-pack/`.
3. The exercise packs (sources committed in the same directory — re-runnable
   against the real fleet at work; the replica pack graduates to a real
   shareable pack if it proves good).
4. A structured friction log distilled into
   `docs/superpowers/specs/2026-07-07-real-pack-exercise-findings.md`
   (same PR, added after the run).
5. An adjudication session: one DECISIONS entry with the rulings; BACKLOG
   rows promoted or rejected; ROADMAP updated; a 0.16 wave brief drafted
   carrying the promoted items plus the ruled uv.lock fix.

## The simulated fleet

Built under the orchestration session's job tmp directory by
`build_fleet.py` (stdlib-only, deterministic, destroys and recreates the
fleet root on each invocation so every scenario starts fresh).

Eight repos mimicking ansible-collections consumers. Content variance
(exercises real edges, one repo each unless noted):

| Repo | Content shape | Why |
|---|---|---|
| `app-alpha`, `app-bravo`, `app-charlie` | `site.yml`, `playbooks/*.yml`, `requirements.yml` with a `collections:` list, `roles/`, `group_vars/` | The common case; alpha/bravo differ in list formatting (inline vs block) |
| `app-delta` | No `requirements.yml` at all | The skip-or-create decision; warn-channel pressure |
| `app-echo` | Collection already present in `requirements.yml` | No-op population; formatting must survive untouched |
| `app-foxtrot` | Comments + YAML anchors + odd indentation in `requirements.yml` | Formatting-preservation pressure on `yaml_edit` |
| `app-golf` | A non-UTF-8 file (`docs/legacy.txt`, latin-1) beside normal YAML | Incidental byte-mode evidence when globs sweep it |
| `infra-hotel` | Playbooks in a nonstandard dir (`automation/`) | Forces the detection hook to actually decide |

VCS population, engineered for the backup verdict:

| State | Repos |
|---|---|
| Clean git checkout (committed, no changes) | `app-alpha`, `app-bravo`, `app-charlie`, `app-foxtrot` |
| Dirty tree (uncommitted modifications) | `app-delta`, `app-echo` |
| Untracked-only changes | `app-golf` |
| Plain directory, no git | `infra-hotel` |

## Scenarios

Each scenario runs the full lifecycle from the user seat: `new pack` → write
hooks/recipes → `add` → `show`/`list` → `check` → `apply` (preview → confirm)
→ edit → re-add. Order below is execution order.

### S1 — Ansible-collections replica

A pack `collections-ensure` that ensures a set of collections is present in
each repo's `requirements.yml`:

- **Custom detection hook** (decision-is-a-hook): classifies which files are
  playbooks / which repos are in scope, including `infra-hotel`'s
  nonstandard layout.
- **Membership-ensure via transform hook** (today's only path — `yaml_edit`
  has no `ensure` op): add collections to the list only if absent, preserving
  formatting on no-ops (`app-echo`, `app-foxtrot` are the honesty checks).
- **0.15 list input** for the collections to ensure (structured input in
  anger: `--var` YAML parsing, prompting, `from:` derivation where sensible).
- `app-delta` (no requirements.yml) must be reported without failing the
  run — however 0.15.1 allows; the workaround shape is warn-channel evidence.

Evidence targets: input-from walls (log every rejected expression verbatim +
whether `record.x.y` dotted paths would cover it), manifest args-schema pain,
`yaml_edit ensure` adopter-#2 counter, warn-channel trigger.

### S2 — Zero-dep + authoring loop

Two packs: `fleet-hygiene` (hookless: pure ensure/template steps — e.g.,
seed a standard `.yamllint` + CI stub) and `collections-ensure` from S1
(zero-dep hooks). For each:

- Timed comparison (3-run `time` samples): `apply` via the worker path vs an
  equivalent builtin-only recipe; plus `add`-time lock cost.
- Authoring round-trip ×3: edit pack source → `add --force` (hash guard
  fires) → resolve → verify. Count commands, wall time, and mistakes per
  loop; flag each loop **seat-sensitive**.
- Log the uv.lock asymmetry when `add`-ing the hookless pack (already ruled —
  evidence recorded, no adjudication).
- Note typed-stub quality while writing the hooks (0.13/0.15 scaffold
  output).

Evidence targets: tiered-execution toll (quantified), `add --editable`
round-trip friction, uv.lock occurrence, stub quality notes.

### S3 — Mixed-VCS apply

The S1 pack applied across the engineered VCS population with default
backups on:

- Measure backup volume (bytes, file count), creation time, and
  `backup list` clutter after 3 fleet-wide applies.
- Reconstruct per-repo what a git-aware skip would have done (clean → skip,
  dirty/untracked → ?, non-git → backup) and write the skip-semantics sketch,
  including exactly what read-only detection needs (presence of `.git` +
  `git status --porcelain` emptiness) and where it would sit outside the
  write path.

Evidence targets: population evidence + semantics sketch for the Alexis
ruling. No verdict from the exercise itself.

### S4 — 0.15 features in anger (threaded)

Not a standalone run: S1–S3 must deliberately route through list inputs,
`from:` derivation, bare-token field templating, and `--var` structured
parsing wherever a real pack plausibly would. Every sandbox rejection, every
templating surprise, every "had to ask the docs" moment gets a log entry.

## Friction log

One NDJSON file per scenario in the exercise working dir (job tmp — not
committed); the findings report is their durable distillation. Entry schema:

```json
{"scenario": "S2", "step": "re-add after source edit", "command": "…",
 "expected": "…", "observed": "…",
 "severity": "blocker|major|minor|paper-cut",
 "evidences": "<BACKLOG row short-name>", "seat_sensitive": true}
```

Severity is about the *author's workflow impact*, not code correctness.
`seat_sensitive: true` marks entries where human annoyance is the evidence;
these get copy-paste replay commands in the findings report for Alexis to
re-experience before ruling.

## Pre-registered verdict criteria

Locked now so rulings measure evidence, not narrative:

- **Tiered zero-dep execution.** PROMOTE if the worker/lock path shows
  material quantified friction on zero-dep packs (lock latency, failure
  modes, artifact clutter) that in-process execution would delete, AND no
  counterweight (timeout enforcement, crash isolation, version independence)
  actually fired during the exercise. REJECT if the toll is negligible or a
  counterweight proved load-bearing.
- **Git-aware backup skip.** Evidence bundle = VCS population stats + backup
  cost numbers + skip-semantics sketch. PROMOTE-shaped: population mostly
  clean git, cost material, detection stays read-only and outside the write
  path. Ruling: Alexis.
- **input-from simplification.** PROMOTE dotted-path replacement if the
  majority of real derivations in S1–S4 are dotted-path-shaped AND at least
  one real expression hit the sandbox wall. REJECT (sandbox stays, row
  closes) if no real expression was rejected.
- **add --editable.** PROMOTE (with the pointer-entry design from its row)
  if S2's round-trips log recurring friction — the row's own trigger,
  evidence #2+. Otherwise stays deferred: hash guard adequate.

## Execution mechanics

- Everything under the session job tmp dir; nothing in `/tmp` proper.
- Tool = `uvx untaped-recipe@0.15.1` (released artifact, user seat, the
  session's real tool installs untouched). Version pinned for the whole
  exercise.
- Fleet rebuilt fresh via `build_fleet.py` before each scenario.
- Timings = 3-run `time` samples where a verdict needs numbers; no benchmark
  machinery.
- Orchestrator (Claude session) drives; Alexis replays only the
  seat-sensitive command list before adjudicating.

## Close-out

1. Findings report added to this PR; builder + packs committed under
   `docs/superpowers/exercises/2026-07-07-real-pack/`.
2. Adjudication session with Alexis → one DECISIONS entry (rulings +
   evidence pointers), BACKLOG rows promoted/rejected, ROADMAP updated.
3. 0.16 wave brief drafted (promoted items + the ruled `add`/`check` uv.lock
   hookless-exemption fix) as the next recipe package.
4. Fleet and all applied changes die with the job tmp dir; the committed
   builder + packs are the reproducibility artifact for a later work-fleet
   re-run.
