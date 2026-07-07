# Testing packs

This page covers the two ways to trust a pack before you apply it: `check`, a
static preflight that never imports or runs hook code, and `test`, a golden-
fixture harness that plans real recipes against fixtures and compares trees. It
also covers the pytest stubs scaffolded for hook unit tests. For the pack model
these commands operate on, see [packs](./packs.md).

## check

`check` is a static preflight. It validates structure and wiring without
importing hook code or writing anything.

```bash
untaped-recipe check
untaped-recipe check ansible
untaped-recipe check ansible/playbook-migration
untaped-recipe check ./ansible
untaped-recipe check yaml_edit
```

With no ref, `check` validates the whole installed library plus `packs.toml`
reconciliation (index rows without a directory, and pack directories missing
from the index). With a ref it validates one installed pack, a bare or qualified
recipe ref, an explicit pack or recipe path, or a bare built-in hook name when
no library entry shadows it.

For a pack or recipe, `check` verifies:

- pack metadata and the declared recipe files load,
- each recipe's template and copy sources resolve inside the recipe directory,
- recipe input source expressions are valid (see [inputs](./inputs.md)),
- every `transform`/`validate` step resolves to a hook that exports the matching
  function, and
- lockfile freshness for hook-declaring projects.

### No-import hook scan

`check` never imports hook code. It reads each resolved hook module and
AST-scans its top level for `def transform` and `def validate`, then rejects any
step wired to a hook that does not export the function its step type needs. A
module that exports neither function fails the pack.

### Lockfile freshness

A hook-declaring pack must ship a `uv.lock`, and `check` runs `uv lock --check`
against it so a stale lock fails at check time instead of at hook-run time.
Hookless packs and recipe-only projects do not need a lockfile and skip this
probe. Each project root is probed at most once per command.

When uv reports the lock is out of date, `check` fails with
`lockfile is stale — run 'uv lock' in <project>`. When freshness cannot be
verified for some other reason, `check` reports
`could not verify lockfile freshness in <project>` with uv's detail appended
when available. This is one of three integrity mechanisms; see
[safety](./safety.md) for how it contrasts with backup hashes and the pack
install hash.

### Orphaned test directories

`check` fails a pack whose `tests/` contains a directory that names no recipe in
the manifest, so a renamed or deleted recipe cannot leave dead fixtures behind.
`test` reports the same directories as error rows (see below).

## Golden tests

`test` runs golden-fixture cases packs ship under `tests/<recipe>/<case>/`. It
mirrors `check`'s ref grammar:

```bash
untaped-recipe test
untaped-recipe test ansible
untaped-recipe test ansible/playbook-migration
untaped-recipe test ./ansible
```

Planning is the only execution: the harness runs the same planner as
[apply](./apply.md) with the normal hook resolution order, against a temporary
copy of the fixtures. The original fixtures and the installed pack are never
written by a test run.

### Case layout

Each case is one directory with a single fixture target:

```text
tests/
└── playbook-migration/
    └── basic/
        ├── case.yml
        ├── given/
        │   └── site.yml
        └── expected/
            └── site.yml
```

`given/` is the fixture target directory. It is copied to a temporary target
named after the case before planning. `expected/` is the full expected target
tree after planning: extra, missing, and changed files all fail the case. If
`expected/` is omitted, the case asserts that the recipe plans no changes.
Fixture files must be UTF-8 text, because the harness compares text trees.

### case.yml

`case.yml` is optional and data-only. Every field is optional, and unknown
fields are rejected:

```yaml
inputs:
  owner: platform-team
expect: success
error_contains: "..."
verdict:
  status: warn
  message_contains: "tabs"
```

- `inputs` supplies recipe inputs, using the same names and types `apply`
  accepts (see [inputs](./inputs.md)).
- `expect` is `success` (default) or `error`. `expect: error` requires
  `error_contains` and forbids an `expected/` tree; the case passes when
  planning fails with a message containing that substring. `error_contains` is
  forbidden for success cases.
- `verdict` asserts on validate-hook verdicts and is valid only with
  `expect: success`. `status` asserts the worst produced verdict (`pass`,
  `warn`, or `fail`); `message_contains` asserts that at least one produced
  verdict message contains the substring. A `verdict` block must declare at
  least one of the two.

There is no control flow or assertion DSL in `case.yml`; hook-level logic
belongs in pytest.

### --update

`--update` regenerates `expected/` from the current plan and deletes it when the
plan is empty. It requires an explicit pack or recipe argument and rejects
`expect: error` cases:

```bash
untaped-recipe test ansible/playbook-migration --update
```

### Output and exit behavior

`test` emits one `recipe.test` row per case with `pack`, `recipe`, `case`,
`status`, and `detail`. Statuses are `pass`, `fail`, `error`, and — under
`--update` — `updated`. See [pipes](./pipes.md) for the output envelope and
`--format`/`--columns` handling. Mismatched files also render unified diffs on
stderr, followed by a summary line:

```json
[{"pack":"ansible","recipe":"playbook-migration","case":"basic","status":"pass","detail":""}]
```

```text
Recipe tests: 1 passed, 0 failed, 0 errored
```

`test` exits non-zero on any failed or errored case. An explicitly named pack or
recipe with no cases emits an error row `no test cases found` and exits 1. Bare
`test` reports packs without a `tests/` directory on stderr but does not fail
only for that. A `tests/` directory that names no manifest recipe is reported as
an error row, matching `check`.

### Scaffolded case

`new recipe` scaffolds `tests/<recipe>/basic/` with an empty `given/` and a
fully commented `case.yml`, so the initial case passes as "no changes" until you
add fixtures and run `test <pack>/<recipe> --update`. See [packs](./packs.md)
for the scaffolding commands.

## Hook unit tests

Hooks are pure functions, so they can be unit-tested directly with pytest, no
worker or recipe needed. `new hook` scaffolds `tests/test_hook_<name>.py`, a
pytest that imports and calls the exported hook function. New packs get `pytest`
in their dev group and a pytest `pythonpath = ["src"]` setting, so
`uv run --project <pack> pytest` works out of the box.

Packs scaffolded before 0.13.0 do not gain `pytest` or the `pythonpath` setting
automatically — add them to the pack's `pyproject.toml` if you want hook tests
to run there.

## Authoring workflow

A typical loop for a new recipe:

1. `new recipe <pack>/<recipe>` scaffolds the recipe and its `basic` case.
2. Populate `given/` with a representative fixture target, and set any `inputs`
   in `case.yml`.
3. Run `test <pack>/<recipe> --update` to generate `expected/` from the plan,
   then review that tree as the golden.
4. Add more cases (edge layouts, `verdict` assertions, `expect: error` cases)
   as their own directories.
5. Put branching, parsing, and edit logic under hook pytest, keeping `case.yml`
   to data-only assertions.

Run `check` and `test` together before publishing a pack: `check` catches
wiring and lockfile drift statically, and `test` proves the recipes still plan
the trees you expect.
