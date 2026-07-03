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

## Trust

Trust stance, explicitly: installing a pack is installing code, on the same trust
model as `pip install` — there is deliberately no sandbox (see Never build). The
mitigations are evaluate-before-trust surfaces: the `add` confirmation listing what
comes in, structured `show`, `check`'s no-import AST scan, and (0.10) the test
harness. A user who `add`s a pack is trusting its author; the tool's job is to make
what they are trusting visible, not to pretend the code is contained.
