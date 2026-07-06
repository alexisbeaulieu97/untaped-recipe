# Packs

A pack is the library and sharing unit for `untaped-recipe`. It is a directory
whose top-level `pyproject.toml` declares `[tool.untaped_recipe]` with optional
`recipes` and `hooks` tables. A pack may contain many recipes, one recipe, no
recipes, many hooks, or only hooks.

Single YAML recipe files remain runnable by explicit path:

```bash
untaped-recipe apply ./recipe.yml ./repo --yes
```

They are not installed as library items. Put reusable recipes and hooks in a
pack.

## Manifest

Pack identity comes from `[project].name`. Project names may use the
`untaped-recipe-` prefix; the public pack name drops that prefix. For example,
`untaped-recipe-ansible` installs as pack `ansible`.

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = []

[dependency-groups]
dev = ["untaped-recipe>=0.9"]

[tool.untaped_recipe]
requires_hook_api = ">=0.9,<1"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Both `recipes` and `hooks` are explicit. There is no `recipes/*/recipe.yml`
auto-discovery. Hook entries do not declare `kind`; the exported function names
inside the module are the hook contract.

## Library

The default library root is `~/.untaped/untaped-recipes`:

```text
packs/
packs.toml
backups/
```

Installed packs live under `<library_root>/packs/<pack-name>/`. Source tracking
lives in `<library_root>/packs.toml`, not inside installed pack directories, so
installed packs stay byte-identical to their source.

`add --name <name>` overrides the installed library key. That installed key is
the pack identity everywhere: refs, `list`, `check`, `remove`, ambiguity
messages, and output rows. `show <pack>` also displays the manifest project
identity when it differs from the installed key.

## Commands

```bash
untaped-recipe new pack ansible
untaped-recipe new recipe ansible/playbook-migration
untaped-recipe new hook ansible/add_play_collections
untaped-recipe add ./ansible --yes
untaped-recipe add https://github.com/example/untaped-recipe-ansible.git --rev v0.1.0
untaped-recipe list
untaped-recipe list --packs
untaped-recipe list --hooks
untaped-recipe show ansible
untaped-recipe show ansible/playbook-migration
untaped-recipe show ansible/add_play_collections
untaped-recipe check
untaped-recipe check ansible
untaped-recipe check ansible/playbook-migration
untaped-recipe test ansible
untaped-recipe test ansible/playbook-migration --update
untaped-recipe edit ansible/add_play_collections
untaped-recipe remove ansible --yes
```

`add <path>` copies a local pack. `add <git-url>` fetches a git source into a
temporary directory and installs it through the same validation path. Before
installing, `add` lists the pack's recipes and hooks and asks for confirmation;
`--yes` skips the prompt.

`remove <pack>` is destructive because library packs are editable in place and
removal discards edits. It asks for confirmation; `--yes` skips. Non-tty stdin
without `--yes` is refused by the SDK destructive confirmation path.

## References

Recipe and hook refs use `pack/name`:

```bash
untaped-recipe apply ansible/playbook-migration ./repo --yes
untaped-recipe hook run ansible/add_play_collections --target ./repo --file site.yml --diff
```

Bare recipe and hook names are accepted only when they resolve uniquely across
installed packs. Ambiguous bare names are errors and list the qualified
candidates. Qualified refs must use exactly `<pack>/<name>`; bare `a/b/c` is
rejected.

For `apply`, local paths must be explicit. A path is explicit only when it
starts with `./`, `../`, `/`, or `~`, or ends in `.yml` or `.yaml`. Anything
else, including `a/b`, is a library ref and is never classified by checking
whether a matching path happens to exist on disk.

For `new recipe` and `new hook`, explicit local paths start with `./`, `../`,
`/`, or `~` and split on the final path segment:

```bash
untaped-recipe new hook ./some-local-pack/probe
```

This targets `./some-local-pack` and creates hook `probe`.

## Testing Packs

Packs can ship golden-fixture cases under `tests/<recipe>/<case>/`. The `test`
command mirrors `check`'s grammar:

```bash
untaped-recipe test
untaped-recipe test ansible
untaped-recipe test ansible/playbook-migration
untaped-recipe test ./ansible
```

Each case has one fixture target directory:

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

`given/` is copied to a temporary target named after the case before planning.
The original fixtures and pack are never written by a normal test run.
`expected/` is the full expected target tree after planning. Extra, missing,
and changed files all fail. If `expected/` is omitted, the case asserts that
the recipe plans no changes.

`case.yml` is optional and data-only. Every field is optional:

```yaml
inputs:
  owner: platform-team
expect: success
error_contains: "..."
verdict:
  status: warn
  message_contains: "tabs"
```

`expect: error` requires `error_contains` and forbids `expected/`.
`error_contains` is forbidden for success cases. `verdict.status` asserts the
worst produced validate-hook verdict (`pass`, `warn`, or `fail`), and
`verdict.message_contains` asserts that at least one produced verdict message
contains the substring. There is no control flow or assertion DSL in `case.yml`;
hook-level logic belongs in pytest.

`--update` regenerates `expected/` from the current plan and deletes it when
the plan is empty. It requires an explicit pack or recipe argument and rejects
`expect: error` cases:

```bash
untaped-recipe test ansible/playbook-migration --update
```

`test` emits one `recipe.test` row per case with `pack`, `recipe`, `case`,
`status`, and `detail`. Mismatched files also render unified diffs on stderr,
followed by a summary:

```json
[{"pack":"ansible","recipe":"playbook-migration","case":"basic","status":"pass","detail":""}]
```

```text
Recipe tests: 1 passed, 0 failed, 0 errored
```

An explicitly named pack or recipe with no cases emits an error row
`no test cases found` and exits 1. Bare `test` reports packs without `tests/`
on stderr but does not fail only for that. `check` fails a pack whose `tests/`
contains a directory that names no manifest recipe; pack-scoped `test` also
reports those orphaned directories as error rows.

`new recipe` scaffolds `tests/<recipe>/basic/` with an empty `given/` and a
fully commented `case.yml`, so the initial case passes as "no changes" until
you add fixtures and run `test <pack>/<recipe> --update`.

## Trust

Trust stance, explicitly: installing a pack is installing code, on the same trust
model as `pip install` — there is deliberately no sandbox (see Never build). The
mitigations are evaluate-before-trust surfaces: the `add` confirmation listing what
comes in, structured `show`, `check`'s no-import AST scan, and (0.10) the test
harness. A user who `add`s a pack is trusting its author; the tool's job is to make
what they are trusting visible, not to pretend the code is contained.
