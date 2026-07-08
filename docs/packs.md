# Packs

A pack is the library and sharing unit for `untaped-recipe`. This page covers
what a pack is, how it is identified, where installed packs live, how you refer
to their recipes and hooks, and the commands that scaffold, install, inspect,
and remove them. For the recipe YAML schema see [recipes](./recipes.md); for
hook code see [hooks](./hooks.md).

A pack is a uv project whose top-level `pyproject.toml` declares
`[tool.untaped_recipe]` with optional `recipes` and `hooks` tables. One pack may
contain many recipes, one recipe, no recipes, many hooks, or only hooks.

A single YAML recipe file stays runnable by explicit path for quick local use,
but it is never installed as a library item. Put reusable recipes and hooks in a
pack. See [recipes](./recipes.md) for single-file recipes and
[running recipes](./apply.md) for how paths and refs are resolved at apply time.

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
dev = ["untaped-recipe>=0.10"]

[tool.untaped_recipe]
requires_hook_api = ">=0.10,<1"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

Both `recipes` and `hooks` are explicit. There is no `recipes/*/recipe.yml`
auto-discovery. Hook entries do not declare `kind`; the exported function names
inside the module are the hook contract (see [hooks](./hooks.md)). The dev-only
`untaped-recipe` dependency exists for editor type discovery — its floor tracks
the hook API contract, not the CLI release cadence, so scaffolds pin
`untaped-recipe>=0.10`. Pack code must not depend on `untaped-recipe` at runtime.

A pack lays out its recipes, templates, hook package, and lockfile under the
project root:

```text
ansible/
├── pyproject.toml
├── uv.lock
├── recipes/
│   └── playbook-migration/
│       ├── recipe.yml
│       └── templates/
│           └── config.yml
├── src/
│   └── ansible_hooks/
│       └── hooks/
│           └── add_play_collections.py
└── tests/
    └── playbook-migration/
        └── basic/
```

Nested uv projects or workspaces inside a pack are opaque. Untaped reads only
the top-level project metadata and the declared recipe paths; it never descends
into a nested project's own manifest. The `tests/` tree holds golden cases — see
[testing](./testing.md).

## Library

The default library root is `~/.untaped/untaped-recipes`:

```text
packs/
packs.toml
backups/
```

Installed packs live under `<library_root>/packs/<pack-name>/`. Source tracking
lives in `<library_root>/packs.toml`, not inside installed pack directories, so
an installed pack stays byte-identical to its source. Each `packs.toml` row
records the install `source` (path or git URL), `rev`, installed `version`, and
a `content_hash` of the pack's install-relevant files. `backups/` holds backup
bundles — see [safety](./safety.md).

The installed key is the pack identity everywhere: refs, `list`, `check`,
`remove`, ambiguity messages, and output rows. `add --name <name>` overrides
that key (see [add](#add)). `show <pack>` also displays the manifest project
identity when it differs from the installed key.

`library_root` is a setting; see [reference](./reference.md) for how to change
it.

## References

Recipe and hook refs use `pack/name`:

```bash
untaped-recipe apply ansible/playbook-migration ./repo --yes
untaped-recipe hook run ansible/add_play_collections --target ./repo --file site.yml --diff
```

A ref is either qualified (`pack/name`) or a bare name:

```bash
untaped-recipe apply ansible/playbook-migration ./repo --yes   # qualified
untaped-recipe apply playbook-migration ./repo --yes           # bare, if unique
untaped-recipe apply a/b/c ./repo --yes                        # rejected
```

- Bare recipe and hook names are accepted only when they resolve uniquely across
  installed packs. An ambiguous bare name is an error that lists the qualified
  candidates.
- Qualified refs must use exactly `<pack>/<name>`; a bare `a/b/c` is rejected.

For `apply`, a local path must be explicit. A value is a path only when it
starts with `./`, `../`, `/`, or `~`, or ends in `.yml` or `.yaml`. Anything
else, including `a/b`, is a library ref and is never reclassified by checking
whether a matching path happens to exist on disk. This grammar is the single
source for how [running recipes](./apply.md) tells a ref from a path.

For `new recipe` and `new hook`, an explicit local pack path starts with `./`,
`../`, `/`, or `~` and splits on its final segment:

```bash
untaped-recipe new hook ./some-local-pack/probe
```

This targets `./some-local-pack` and creates hook `probe`.

## Scaffolding

`new pack`, `new recipe`, and `new hook` generate pack projects and their
manifest rows:

```bash
untaped-recipe new pack ansible
untaped-recipe new recipe ansible/playbook-migration
untaped-recipe new hook ansible/add_play_collections
```

`new pack` creates an empty uv pack project with `pytest` and the dev-only
`untaped-recipe` typing dependency in its dev group and a pytest
`pythonpath = ["src"]` setting. `new recipe <pack>/<recipe>` adds a recipe under
`recipes/<recipe>/`, updates the manifest, and scaffolds a starter golden case.
`new hook <pack>/<hook>` adds a hook module stub under `src/`, updates
`[tool.untaped_recipe.hooks]`, pins `requires_hook_api = ">=0.10,<1"`, and
scaffolds a unit-test stub. See [testing](./testing.md) for the scaffolded
golden case and hook pytest.

All three refresh the pack `uv.lock` by default so hooks can run under
`uv run --locked --no-dev`. Locking needs access to PyPI or a configured uv
source for `untaped-recipe`, and uv also provisions Python interpreters from
GitHub on demand.

### Recovering from a failed lock

If file creation itself fails, the scaffold rolls back the newly written paths
and manifest rows. If the files are written but `uv lock` fails afterward, the
command leaves the completed pack, recipe, hook module, tests, and manifest rows
in place and prints a repairable error. To repair:

1. Fix the package index, or add a package-specific `[tool.uv.sources]`
   override.
2. Run `uv lock` in the pack.

For example, a lagging corporate mirror can route only `untaped-recipe` to an
approved fallback index:

```toml
[tool.uv.sources]
untaped-recipe = { index = "approved-pypi" }

[[tool.uv.index]]
name = "approved-pypi"
url = "https://pypi.org/simple"
explicit = true
```

On networks that block GitHub, point `UV_PYTHON_INSTALL_MIRROR` at an approved
mirror of python-build-standalone (or preinstall a matching interpreter) so pack
environments can build at all.

### Skipping the lock step

Pass `--no-lock` to `new pack`, `new recipe`, or `new hook` to skip the lock
step entirely. The command then exits successfully and prints a stderr note, but
hooks cannot run until `uv lock` succeeds because workers execute with
`uv run --locked --no-dev`.

## add

`add` installs a pack from a local path or a git URL:

```bash
untaped-recipe add ./ansible --yes
untaped-recipe add https://github.com/example/untaped-recipe-ansible.git --rev v0.1.0
```

`add <path>` copies a local pack. `add <git-url>` clones the git source into a
temporary directory and installs it through the same validation path; `--rev`
selects the git revision. Before installing, `add` lists the pack's recipes and
hooks and asks for confirmation; `--yes` skips the prompt. Non-tty stdin without
`--yes` is refused by the SDK destructive-confirmation path.

`add` validates the pack before copying: its declared recipe files must load,
each hook module must export the required function, and a hook-declaring pack
must contain a `uv.lock` — hookless packs are exempt, the same rule
[check](./testing.md) applies. The install copies the pack tree minus dev and
build junk — `.git`,
`.venv`, `__pycache__`, `dist`, `build`, `.pytest_cache`, `.mypy_cache`,
`.ruff_cache`, `.uv-cache`, and `*.egg-info` — and records a `content_hash` of
the copied tree in `packs.toml`.

`--name <name>` installs under a different library key than the manifest name.
That key becomes the pack identity used by refs, output rows, ambiguity errors,
`check`, and `remove`.

Because library packs are editable in place (`edit`, and `new recipe`/`new hook`
into an installed pack), reinstalling is guarded:

```bash
untaped-recipe add ./ansible --force                  # refused when the copy has local edits
untaped-recipe add ./ansible --force --discard-edits  # overwrite deliberately
```

- Installing over an existing pack requires `--force`; otherwise `add` refuses
  and suggests `--force` or `--name`.
- `--force` still refuses when the installed copy has diverged from its recorded
  `content_hash`, so local edits are not silently discarded. Re-run with
  `--discard-edits` to overwrite deliberately.
- The preview warns before the confirmation prompt when the library copy has
  local edits.

## list, show, edit

```bash
untaped-recipe list
untaped-recipe list --packs
untaped-recipe list --hooks
untaped-recipe show ansible
untaped-recipe show ansible/playbook-migration
untaped-recipe show ansible/add_play_collections
untaped-recipe edit ansible/add_play_collections
```

`list` shows recipes by default. `list --packs` shows installed packs and
`list --hooks` shows hook refs, including built-in hooks such as `yaml_edit`
(marked `(builtin)`). Choose one of `--packs` or `--hooks`, not both.

`show` renders structured pack, recipe, or hook detail. It resolves a bare
built-in hook name when no library entry shadows it. When a pack's installed key
differs from its manifest project name, `show <pack>` reports both.

`edit` opens a pack `pyproject.toml`, a recipe file, or a hook module in
`$VISUAL` or `$EDITOR`. Built-in hooks are engine-owned, so `edit` rejects them.

To validate or test what you installed, see [testing](./testing.md) for `check`
and `test`.

## remove

```bash
untaped-recipe remove ansible --yes
```

`remove <pack>` is destructive because library packs are editable in place and
removal discards those edits. It previews the pack being removed and asks for
confirmation; `--yes` skips the prompt. The preview warns before confirmation
when the installed copy has local edits. Non-tty stdin without `--yes` is
refused by the SDK destructive-confirmation path.

## Trust

Trust stance, explicitly: installing a pack is installing code, on the same
trust model as `pip install` — there is deliberately no sandbox. The mitigations
are evaluate-before-trust surfaces: the `add` confirmation listing what comes in,
structured `show`, `check`'s no-import AST scan, and the golden
[test harness](./testing.md). A user who `add`s a pack is trusting its author;
the tool's job is to make what they are trusting visible, not to pretend the
code is contained.
