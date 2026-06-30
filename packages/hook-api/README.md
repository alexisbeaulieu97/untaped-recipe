# untaped-recipe-hook-api

Typed hook authoring contract for `untaped-recipe` hook projects.

Install it as a development dependency in hook projects so editors and type
checkers can discover the injected `HookHelpers` API:

```toml
[dependency-groups]
dev = ["untaped-recipe-hook-api>=0.8,<1"]
```

Hook projects should import from `untaped_recipe_hook_api` only under
`TYPE_CHECKING`; the installed `untaped-recipe` CLI provides the runtime helper
object when hooks execute.
