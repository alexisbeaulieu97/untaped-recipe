# Pipes

`untaped-recipe` composes with other untaped tools in both directions: it can
read its target directories from a piped stream, and it can emit its results as
a machine-readable stream that another tool consumes. This page covers stdin
target ingestion and structured output — the record grammars, the emit-kind
table, and the `recipe.outcome` row schema.

Both directions rely on the untaped pipe envelope, a frozen NDJSON format shared
across the SDK: one JSON object per line, shaped
`{"untaped": "1", "kind": "<tool>.<kind>", "record": {…}}`.

## Reading targets from stdin

Pass `--stdin` to read target directories from standard input instead of
positional arguments; the two sources are mutually exclusive. Each non-blank
line is one target. See [running recipes](./apply.md) for the `--stdin`
confirmation rule (`--yes` is required unless `--dry-run` or `--check` is used)
and how piped targets combine with input derivation.

A line is resolved as either a bare path or an untaped pipe record:

- **Bare paths.** Any line that is not an untaped envelope object is treated as
  a literal path. Lines that parse as JSON scalars are still paths — a directory
  named `2024` or `true` is a path, not a record. Only JSON objects carrying the
  `untaped` envelope marker enter record parsing.
- **Pipe records.** An untaped envelope object is resolved to a target
  directory by field, in this order:
  1. `record.target_path`, when present, must be a non-empty **absolute** path
     and is used directly.
  2. Otherwise the generic `record.path` field is used.

Two record rules keep piped streams from writing to the wrong place:

- Records whose `kind` ends in `.summary` are informational, not targets, and
  are skipped.
- Repo-grain records such as `workspace.repo` must provide `target_path`. Older
  saved streams that carry only `path` plus `repo` are rejected before planning
  rather than resolved to a possibly-wrong directory.

A per-target pipe record also stays available to input derivation: an input
`from` expression can read the incoming `record`. See [inputs](./inputs.md) for
that sandbox.

```bash
untaped-workspace list --format pipe \
  | untaped-recipe apply add-config --stdin --yes --format json
```

## Emitting structured output

Every command that prints rows accepts `--format`. `apply` and the library
commands (`list`, `show`, `check`, `test`, `backup …`) accept the SDK format set
`json`, `yaml`, `table`, `raw`, and `pipe`; `hook run` accepts `json`, `yaml`,
`table`, and `pipe`. `table` is the default human view; `pipe` emits the untaped
NDJSON envelope for tool-to-tool chaining. Use `--columns`/`-c` (repeatable) to
narrow the emitted fields.

`--format pipe` writes one envelope per row:

```json
{"untaped": "1", "kind": "recipe.outcome", "record": {"recipe": "ansible/playbook-migration", "target": "/srv/service-a", "status": "applied", "files_changed": 2, "warnings": "", "error": null, "inputs": {"service": "api"}}}
```

stdout carries data only. Previews, prompts, progress, warnings, errors, and
summary lines all go to stderr, so a `--format json`/`pipe` stream stays clean
for a downstream consumer. `--format` and `--columns` affect the stdout rows
only, never the stderr preview table.

### Emit kinds

Each command emits rows under one envelope `kind`:

| Kind | Emitted by |
|---|---|
| `recipe.outcome` | `apply` (one row per target) |
| `recipe.check` | `check` (including built-in hook refs) |
| `recipe.test` | `test` (one row per case) |
| `recipe.hook_run` | `hook run` (transform result or validate verdict) |
| `recipe.recipe` | `list` (default), `show <recipe>` |
| `recipe.hook` | `list --hooks`, `show <hook>` |
| `recipe.pack` | `list --packs`, `show <pack>` |
| `recipe.backup` | `backup list`, `backup show`, `backup restore`, `backup prune` |

## `recipe.outcome` rows

`apply` emits one `recipe.outcome` row per target with these fields:

| Field | Meaning |
|---|---|
| `recipe` | Canonical recipe ref, e.g. `pack/recipe`. |
| `target` | Target directory path. |
| `status` | Per-target outcome — see below. |
| `files_changed` | Number of files the plan changed for this target. |
| `warnings` | Per-target warnings joined into one semicolon-delimited string (e.g. skipped optional transforms), empty when none. |
| `error` | Failure detail for an errored target, otherwise null. |
| `inputs` | Resolved declared inputs for this target, as a mapping. |

`status` reports what happened to the target:

- `applied` — the plan wrote changes.
- `unchanged` — the plan produced no writes for this target.
- `error` — the target failed to plan or failed to write; a failed target
  writes nothing and does not block other targets.
- `check` — emitted under `--check`; the run wrote nothing.
- `dry-run` — emitted under `--dry-run`; the run wrote nothing.
- `planned` — emitted when confirmation was declined, so nothing ran.

The `inputs` mapping holds the resolved declared inputs. Values are redacted
according to each input's declared sensitivity — sensitive values render as
`***` in rows, warnings, and errors. [Inputs](./inputs.md) owns the redaction
rules; the full incoming pipe record is never copied into a row. In `table`
format the mapping is flattened to `key=value` pairs; structured formats keep
the real mapping.

See [reference](./reference.md) for the exit-code table across commands.
