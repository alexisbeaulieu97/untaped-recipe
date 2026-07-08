# Real-Pack Exercise — Findings

**Date:** 2026-07-07
**Protocol:** [2026-07-07-real-pack-exercise-design.md](./2026-07-07-real-pack-exercise-design.md)
**Tool under test:** released `untaped-recipe==0.15.1` via uvx, frozen throughout.
**Artifacts:** `docs/superpowers/exercises/2026-07-07-real-pack/` (fleet builder +
both pack sources); raw NDJSON friction logs lived in the session job tmp dir and
are distilled here.

## Executive summary

Both packs were authored **from the concept docs alone** — zero source reading
until the bug hunt — and the core flow (scaffold → hooks → golden test → add →
check → fleet apply → idempotent re-run) is production-shaped: 8-repo applies in
0.5–0.8s, formatting-preserving YAML edits (comments, anchors, merge keys,
inline comments all survived), correct if_absent creation, byte-identical no-ops,
no backup bundle on no-op runs.

One **blocker-severity bug** found; the ruled uv.lock asymmetry reproduced
exactly; and each of the four adjudication challenges now has real evidence
against its pre-registered criteria.

## The blocker: relative target paths reach workers unresolved

`apply <recipe> app-alpha app-bravo ...` (relative paths, valid from the
invoking cwd) hands the worker a `target` Path that the hook resolves against
the **worker's own cwd** (the pack project dir). `target.rglob()` on the
nonexistent path returns empty — no error, silently wrong verdicts/transforms.
The same targets pass with absolute paths; `hook run` masks the bug because
`--target` is typically given absolute.

Any target-reading hook + relative CLI paths = silent wrong answers. The engine
should absolutize `target` (and `file`) before worker handoff. **Fix candidate
for 0.16 — bug, no ruling needed.**

(Authoring lesson that compounded it: my detection hook's
`except Exception: continue` swallowed the diagnostic. A docs note on hook
exception hygiene would have saved the hunt.)

## Adjudication evidence vs pre-registered criteria

### 1. Tiered zero-dep hook execution — evidence says REJECT

- Warm worker overhead: **~90ms per 7-target apply** (0.38–0.39s vs 0.29–0.30s
  builtin). Cold env rebuild: **0.63s** (warm uv cache).
- The domain's flagship hooks are **not zero-dep**: `helpers.load_yaml`/
  `dump_yaml` require `ruamel.yaml` in `[project].dependencies`. An in-process
  tier would cover only dep-free string/regex hooks — unless the engine lends
  its own ruamel, which trades away exactly the version independence the worker
  buys.
- Even hookless packs lock 38 packages (dev group), so index-lag pain
  (Artifactory) is not deleted by tiered execution — it lives at lock/add time
  too.
- No counterweight (timeout, crash isolation) fired, but the criterion required
  a **material toll**, and none was measured.
- **Environment caveat:** home network + warm uv cache; the work-Artifactory
  toll is index latency, which this exercise could not reproduce. If that toll
  is the real complaint, the fix is index-side (or pre-`uv sync`), not an
  execution tier.

### 2. Git-aware backup skip — evidence bundle for Alexis's ruling

- **Population (this fleet):** 4/8 clean git → skip candidates; 3/8
  dirty/untracked → backups still valuable (git cannot separate pre-existing
  dirt from recipe changes after the fact); 1/8 non-git → backup essential.
  Post-apply, all 7 git repos held every recipe change as ordinary uncommitted
  paths — `git checkout`/`stash` is a complete undo for them.
- **Cost:** 2 bundles, **40KB total**, no measurable time. Bytes and latency are
  NOT material at this scale; the real dimensions are bundle accumulation
  (`backup_keep` defaults to keep-forever) and redundancy.
- **Semantics sketch (read-only, plan-time, outside the write path):** target
  has `.git` AND `git status --porcelain` empty → skip backup by default;
  dirty, untracked-only, or non-git → keep. Per-target decision, `--backup`
  forces keep everywhere. Write machinery stays VCS-agnostic; detection is two
  read-only probes.
- **Honest read of the criteria:** population leans PROMOTE, cost says the
  friction has not actually bitten. Ruling is Alexis's; DEFER-with-trigger
  (backup clutter observed in real use) is a defensible outcome.

### 3. `input-from` simplification — both criteria technically met; ruling nuance

- Neither real pack needed `from:` at all — the domain's inputs were
  fleet-constant (`--var` list) or defaulted.
- Every derivation actually exercised (`record.repo`, `target.name`
  fall-through) is **plain dotted-path-shaped**.
- The first genuine transformation want
  (`{{ target.name | replace('-','_') }}`) hit the sandbox wall with a clear
  load-time error — but **dotted-path syntax would not cover it either**; a
  transformation is hook territory by the tool's own decision-is-a-hook
  invariant.
- Net: the sandbox's extra surface (literals + access, operator/filter
  rejection machinery) bought nothing over plain dotted paths in real use, and
  its errors are good. Replacing it now is churn for surface reduction, not
  capability. Recommend **REJECT the replacement** (row closes; revisit only if
  the evaluator itself becomes a maintenance burden). The `input_jinja` → SDK
  promotion row stays gated on adopter #2 regardless.

### 4. `add --editable` — evidence #2 logged; still below promotion threshold

- 4 authoring round-trips: `add --yes --force` at **0.3s each** — temporal cost
  nil; plain re-add refuses without `--force` (clear error); hash guard +
  `--discard-edits` both fired correctly when the library copy drifted.
- The real risk is **cognitive**: forget to re-add and `apply` silently uses the
  stale library copy. During S1 hook development I organically switched to
  `hook run --project` (source-direct) to dodge the loop — then the first
  `apply` is where staleness bites.
- The 0.15 pack-not-found hint fires correctly and names both recovery paths.
- Recommend: **keep deferred**, evidence #2 recorded on the row. The
  pointer-entry design becomes worth it when a pack is edited across sessions
  routinely, which one exercise cannot demonstrate.

## Rider evidence

- **`yaml_edit ensure` op (unvetted row): adopter #2 is real.** The replica pack
  IS the second want, and generic membership-ensure proved genuinely subtle:
  ruamel round-trip preserves comments/quotes/anchors but **drops `---` and
  normalizes sequence indent** unless dump options are pinned; appending a dict
  into a flow-style string list emits ugly-but-valid YAML, needing a
  style-matching heuristic. All of that is exactly what a built-in `ensure`
  should own. Recommend promoting into the 0.16 candidate list.
- **Warn channel (row 41):** out-of-scope repos surface as target status
  `error` — no skip/not-applicable tri-state, so summary counts read as
  failures. Second shape of the same gap: a transform that wants to report
  "requirements.yml absent — seeded" non-fatally.
- **uv.lock asymmetry (ruled fix):** reproduced verbatim — `check` passes a
  lockless hookless pack, `add` refuses it.
- **Misc UX parking lot:** `--version` reports the SDK version (row 56);
  `new hook` default kind is silent and wrong-kind cleanup is 2 files (stub +
  paired pytest — the test debris is easy to miss); `hook run` rejects the
  `./pack/hook` ref form that `new hook` accepts (sibling-command grammar
  drift); `list packs` → cyclopts "Unused Tokens" jargon (wants `--packs`);
  default table truncates every column at 80 cols.
- **Docs:** carried the entire exercise — the concept-page restructure paid off
  immediately. One gap: nothing warns that `dump_yaml` defaults normalize
  indentation/`---`, and nothing on hook exception hygiene.

## Seat-sensitive replay list (before ruling)

From the exercise directory (`build_fleet.py <tmp>/fleet` first, isolate with
`UNTAPED_CONFIG` + `UNTAPED_RECIPE__LIBRARY_ROOT`):

1. `new hook collections-ensure/has_playbooks` (bare form) — judge the hint.
2. Edit a hook source file, then `add ./collections-ensure --yes` → `--force`
   dance — judge whether the remember-to-re-add loop annoys you.
3. `apply collections-ensure/ensure-collections <relative paths> --check` —
   experience the blocker's silent wrong answer.
4. `apply` the fleet with defaults and run `backup list` — judge whether the
   bundles feel like value or clutter given the repos are git checkouts.

## Recommended 0.16 wave contents (pending rulings)

1. **Bug:** absolutize `target`/`file` before worker handoff (+ regression test
   with relative CLI paths).
2. **Ruled:** `add`/`check` uv.lock hookless exemption alignment.
3. **If promoted:** `yaml_edit` `ensure` op (design carries the style-matching
   and dump-options lessons).
4. Cheap riders: wrong-kind scaffold cleanup (or `--kind` prompt), `hook run`
   ref-grammar alignment, `list` token error message.
