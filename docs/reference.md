# Reference

Quick-lookup reference for `untaped-recipe`: the settings table, how to
configure them, a one-line index of every command, the exit-code contract, and
skill installation. Each entry links to the concept page that owns the
behavior; this page is a map, not a second home for the rules.

## Settings

`untaped-recipe` stores its settings in the shared untaped config under the
`recipe` section. Defaults and bounds come from the `RecipeSettings` model:

| Setting | Type | Default | Controls |
|---|---|---|---|
| `library_root` | path | `~/.untaped/untaped-recipes` | Where installed packs, the `packs.toml` index, and backup bundles live — see [packs](./packs.md). |
| `hook_timeout_seconds` | float | `60` | Per-hook request timeout; `0` disables it for trusted long-running hooks — see [hooks](./hooks.md). |
| `hook_startup_timeout_seconds` | float | `300` | Separate bound on hook-worker environment startup (uv env create/sync on first use); `0` is unbounded — see [hooks](./hooks.md). |
| `preview_max_rows` | int | `50` | Row count at which table previews collapse from per-file to per-target aggregates; `0` keeps full file-level previews — see [running recipes](./apply.md). |
| `backup_keep` | int (optional) | unset | Retention default: keep the newest N backup bundles; used by `backup prune` when `--keep` is omitted — see [safety](./safety.md). |
| `backup_max_age_days` | int (optional) | unset | Retention default: drop bundles older than N days; used by `backup prune` when `--older-than` is omitted — see [safety](./safety.md). |

### Configuring settings

Profiles, the config file layout, and the generic `config` subcommands (`get`,
`set`, `unset`, `list`, `doctor`, `edit`) are SDK-owned; see the core
[untaped](https://github.com/alexisbeaulieu97/untaped) configuration docs. The
only recipe-specific detail is the section name, `recipe`, and its settings:

```bash
untaped-recipe config set library_root ~/.untaped/untaped-recipes
```

## Commands

Full command reference lives on the concept pages; this index maps each command
to its owner.

| Command | Owner |
|---|---|
| `apply <recipe> <dir>…` | [running recipes](./apply.md) |
| `apply <recipe> --stdin` | [pipes](./pipes.md) (record ingestion), [running recipes](./apply.md) |
| `new pack <name>` | [packs](./packs.md) |
| `new recipe <pack>/<name>` | [packs](./packs.md) |
| `new hook <pack>/<name>` | [packs](./packs.md) |
| `add <path\|git-url>` | [packs](./packs.md) |
| `list [--packs\|--hooks]` | [packs](./packs.md) |
| `show <ref>` | [packs](./packs.md) |
| `edit <ref>` | [packs](./packs.md) |
| `remove <pack>` | [packs](./packs.md) |
| `check [ref\|path]` | [testing](./testing.md) |
| `test [pack\|path\|pack/recipe]` | [testing](./testing.md) |
| `hook run <hook-ref>` | [hooks](./hooks.md) |
| `backup list\|show\|restore\|prune` | [safety](./safety.md) |
| `config get\|set\|unset\|list\|doctor\|edit` | [untaped SDK](https://github.com/alexisbeaulieu97/untaped) |
| `skills list\|install` | this page (below) |

Structured output flags (`--format`, `--columns`) and the emit-kind table are
covered in [pipes](./pipes.md).

## Exit codes

Commands exit non-zero (`1`) on the failures below, and `0` otherwise. Every
command also exits non-zero on a usage or configuration error.

| Command | Non-zero when |
|---|---|
| `apply` | Any target fails to plan or write. |
| `apply --check` | Any target would change, or any target fails. It writes nothing. |
| `apply --dry-run` | Any target fails to plan. It writes nothing. |
| `check` | Any validated pack, recipe, or ref reports an error. |
| `test` | Any case fails or errors, including "no test cases found" for an explicitly named pack or recipe. Bare `test` reports packs without tests but does not fail on them. |
| `test --update` | Any case errors. |
| `hook run` (validate) | The hook returns a fail verdict. |
| `hook run` (transform) | The hook raises an execution error. |

## Installing the skill

`untaped-recipe` ships a packaged agent skill. Install all shipped skills, or
name specific ones:

```bash
untaped-recipe skills install --all
untaped-recipe skills install untaped-recipe
```
