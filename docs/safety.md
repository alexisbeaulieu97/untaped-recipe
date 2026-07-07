# Safety

`untaped-recipe` writes to real directories, so its runtime integrity rests on
two mechanisms: every path a recipe touches is confined to a known root, and
every apply captures a backup you can restore. This page covers the path-safety
rules and the backup and restore system, then contrasts the integrity hashes the
tool keeps so you know which one to reach for.

This page is about runtime integrity — keeping writes inside their roots and
recoverable. Deciding whether a pack's code is safe to run at all is a separate,
up-front concern, owned by [packs](./packs.md#trust).

## Path safety

Every recipe-local and target-relative path is validated before any
engine-mediated read or write. A path is accepted only when it is a safe
relative path: absolute paths, `..` segments, and a bare `.` are all rejected.
The check then walks the path one component at a time against its base and
rejects it if any component is a symlink, or if an existing component resolves
to somewhere outside the base. This confines two roots independently:
recipe-local `source`/`template` paths stay under the recipe directory, and
target `dest`/`file`/`files` paths and glob-expansion results stay under the
target root.

Because path-bearing fields can contain input tokens, they are validated a
second time after per-target rendering, so an input that renders to an absolute
path or a `..` escape is caught before any read or write. That post-render
recheck is owned by [templating](./templating.md#confinement-recheck).

Planning's truthfulness also depends on hooks not writing outside their roots or
reaching the network; those hook-purity rules are owned by
[hooks](./hooks.md).

## Backups

Backups are created by default before an apply writes anything. Pass
`--no-backup` only when the target tree is already protected another way (a clean
VCS checkout, for instance). One apply invocation produces one backup bundle
covering every target it writes.

A backup bundle records, per touched file: the target path, the relative file
path, the before and after content hashes, the saved before-content, and the
redacted per-target inputs for that file. The bundle also stores the canonical
recipe ref and its creation time. Backups store **text content only** for the
engine-managed files a recipe edits; a restore does not preserve file mode or
mtime. Backup metadata never stores the full incoming pipe record — only the
resolved declared inputs, with sensitive values redacted.

Bundle ids use the form `YYYYMMDDTHHMMSSffffffZ-8hex` (a UTC timestamp to
microseconds plus eight hex characters). `show` and `restore` accept a full id,
an unambiguous id prefix, or `latest`.

```bash
untaped-recipe backup list
untaped-recipe backup show 20260619T120000000000Z-a1b2c3d4
untaped-recipe backup restore 20260619T120000000000Z-a1b2c3d4
untaped-recipe backup restore latest
```

`backup list` shows each bundle's id and path. `backup show` renders the bundle
metadata, one line per file in the table view.

### Restore

`backup restore` reinstates a bundle's saved content. It previews the file
actions and asks for confirmation like an [apply](./apply.md) (`--yes` skips the
prompt), and uses the same symlink-confined, staged,
rollback-aware write path — the whole bundle restores as one transaction, so a
partial failure rolls back and reports any incomplete rollback.

Restore is guarded: it refuses to overwrite a file that changed after the backup
was created, comparing the file's current hash to the after-hash the apply
recorded. Pass `--force` only after inspecting those later edits; it overrides
the guard. Because backups hold text only, a restored file comes back with its
original content; file mode and mtime are not preserved.

### Retention and `backup prune`

`backup prune` deletes old bundles behind the standard destructive confirmation
(`--yes` to skip):

```bash
untaped-recipe backup prune --keep 20
untaped-recipe backup prune --older-than 30
```

`--keep N` retains only the newest N bundles; `--older-than DAYS` prunes bundles
older than that age. When a flag is omitted, prune falls back to the
`backup_keep` and `backup_max_age_days` settings; with neither a flag nor a
configured value, prune errors rather than guess. Both bounds apply together —
a bundle is pruned when it falls outside the newest `keep` **or** is older than
the age bound. Bundles whose id carries no parseable timestamp are never pruned
and do not consume a `keep` slot. See [reference](./reference.md) for the
`backup_keep` and `backup_max_age_days` settings.

## Integrity mechanisms at a glance

The tool keeps three distinct content hashes, each guarding a different thing.
Reach for the right one:

| Mechanism | Guards | Owned by |
| --- | --- | --- |
| Install content hash | drift of an installed library pack copy from its install-time tree, so local edits are not silently overwritten on reinstall | [packs](./packs.md#add) |
| Backup before/after hashes | recovery of edited files, and the changed-since-backup restore guard | this page |
| `uv.lock` freshness | staleness of a pack lockfile at check time | [testing](./testing.md) |

The backup hashes are the ones this page owns. Each backup file entry stores a
before-hash (the pre-apply content) and an after-hash (what the apply wrote). At
restore time the tool hashes the file as it stands now and compares it to the
recorded after-hash: an exact match means nothing has touched the file since the
apply, so restoring is safe; a mismatch means someone changed it, and restore
refuses unless `--force` is passed.
