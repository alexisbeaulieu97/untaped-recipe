# Migration 0.9

`untaped-recipe` 0.9.0 unifies recipes and hooks under packs. This is a
breaking release with no compatibility shims and no migration command.

## Manual Steps

Move existing library content into packs:

- Move old `hooks/<name>/` projects and recipe/pack entries under
  `<library_root>/packs/<pack>/`.
- Move reusable standalone recipe projects into a pack and expose each recipe
  in `[tool.untaped_recipe.recipes]`.
- Remove `kind` from hook manifest rows. Hook modules now advertise their verb
  by exporting `transform()`, `validate()`, or both.
- Set `[tool.untaped_recipe].requires_hook_api = ">=0.9,<1"` in packs that
  contain hooks.
- Set scaffolded dev dependencies to `untaped-recipe>=0.9`.
- Keep recipe files at `version: 1`; the `recipe.yml` schema version did not
  change.

## Manifest Changes

Old hook row:

```toml
[tool.untaped_recipe.hooks]
"set_owner" = { kind = "transform", module = "service_hooks.hooks.set_owner" }
```

New hook row:

```toml
[tool.untaped_recipe.hooks]
"set_owner" = { module = "service_hooks.hooks.set_owner" }
```

Pack manifests use `[project].name` for identity. Project names may use the
`untaped-recipe-` prefix; the public pack name drops it. A pack may expose
recipes and hooks together:

```toml
[project]
name = "untaped-recipe-ansible"
version = "0.1.0"

[dependency-groups]
dev = ["untaped-recipe>=0.9"]

[tool.untaped_recipe]
requires_hook_api = ">=0.9,<1"

[tool.untaped_recipe.recipes]
"playbook-migration" = { path = "recipes/playbook-migration/recipe.yml" }

[tool.untaped_recipe.hooks]
"add_play_collections" = { module = "ansible_hooks.hooks.add_play_collections" }
```

## CLI Renames

| Before | After |
|---|---|
| `recipe init <recipe>` | `new pack <pack>` then `new recipe <pack>/<recipe>` |
| `pack init <pack>` | `new pack <pack>` |
| `pack recipe init <pack> <recipe>` | `new recipe <pack>/<recipe>` |
| `hook init <hook>` | `new pack <pack>` then `new hook <pack>/<hook>` |
| `recipe hook init <recipe> <hook>` | `new hook <pack>/<hook>` |
| `pack hook init <pack> <hook>` | `new hook <pack>/<hook>` |
| `recipe add <path>` | move the recipe into a pack, then `add <pack-path>` |
| `pack add <path>` | `add <path>` |
| `hook add <path>` | move the hook into a pack, then `add <pack-path>` |
| `recipe list` | `list` |
| `pack list` | `list --packs` |
| `hook list` | `list --hooks` |
| `recipe show <recipe>` | `show <recipe>` or `show <pack>/<recipe>` |
| `pack show <pack>` | `show <pack>` |
| `pack recipe show <pack> <recipe>` | `show <pack>/<recipe>` |
| `hook show <hook>` | `show <hook>` or `show <pack>/<hook>` |
| `recipe check <recipe>` | `check <recipe>` or `check <pack>/<recipe>` |
| `pack check <pack>` | `check <pack>` |
| `recipe edit <recipe>` | `edit <recipe>` or `edit <pack>/<recipe>` |
| `pack edit <pack>` | `edit <pack>` |
| `pack recipe edit <pack> <recipe>` | `edit <pack>/<recipe>` |
| `hook edit <hook>` | `edit <hook>` or `edit <pack>/<hook>` |
| `recipe remove <recipe>` | remove or edit the containing pack |
| `pack remove <pack>` | `remove <pack>` |
| `pack recipe remove <pack> <recipe>` | edit the containing pack |
| `hook remove <hook>` | remove or edit the containing pack |
| `hook run <hook>` | `hook run <hook>` or `hook run <pack>/<hook>` |

`check` with no arguments validates the whole installed pack library and its
`packs.toml` index.

## Reference Syntax

Qualified recipe and hook refs use `pack/name`, not `pack:name`.

For `apply`, paths must be explicit. A value is a path only when it starts with
`./`, `../`, `/`, or `~`, or ends in `.yml` or `.yaml`. Anything else,
including `a/b`, is a library ref and is not classified by probing the
filesystem.

## Structured Output

The emit-kind set is:

- `recipe.outcome`
- `recipe.backup`
- `recipe.hook_run`
- `recipe.recipe`
- `recipe.hook`
- `recipe.pack`
- `recipe.check`

`recipe.pack_check` and `recipe.pack_recipe` were removed. Pipe consumers
should rekey check rows to `recipe.check` and recipe rows to `recipe.recipe`.

## Template Tokens

Template steps are strict by default. Unknown bare input names still fail, and
0.9.0 also fails non-bare `{{ ... }}` tokens by default. Use
`unknown_tokens: keep` to preserve nested template syntax while still rendering
known inputs:

```yaml
steps:
  - type: template
    template: templates/workflow.yml
    dest: .github/workflows/ci.yml
    unknown_tokens: keep
```

This keeps tokens such as `${{ github.ref }}` and `{{ .Values.image.tag }}`
verbatim.
