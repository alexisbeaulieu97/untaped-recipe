# Release

`untaped-recipe` remains installed from GitHub tags. The type-only
`untaped-recipe-hook-api` package is published to PyPI so scaffolded hook
projects can resolve their editor/type dependency with a plain requirement.

Use the `Release` GitHub Actions workflow for releases. Do not manually create
the GitHub release/tag for versions that include the hook API package.

## One-Time Setup

Create the `untaped-recipe-hook-api` project on PyPI and TestPyPI, then add
Trusted Publishers for this repository:

- workflow: `.github/workflows/release.yml`
- environments: `pypi` and `testpypi`
- package: `untaped-recipe-hook-api`

Protect the `pypi` GitHub environment with required reviewers. TestPyPI can be
less restrictive, but it should still use Trusted Publishing rather than a
long-lived token.

## Release Flow

1. Dispatch the `Release` workflow with `index = testpypi` and the target
   version. This publishes the hook API package to TestPyPI and runs a real
   scaffold smoke against that index. It does not create a GitHub release.
2. If the TestPyPI run passes, dispatch the same workflow from `main` with
   `index = pypi` and the same version.
3. The production workflow verifies versions, runs tests, builds packages,
   smokes scaffold locking against the local wheel, publishes
   `untaped-recipe-hook-api`, waits for PyPI availability, smokes scaffold
   locking against PyPI, then creates the GitHub release/tag.

## Version Burn Recovery

PyPI does not allow re-uploading the same distribution filename after a publish,
even if the file is deleted. If the publish step succeeds but post-publish
verification never passes, treat that version as permanently burned.

Recovery is to bump the patch version everywhere and rerun the workflow:

- root `pyproject.toml`
- `packages/hook-api/pyproject.toml`
- `untaped_recipe_hook_api.HOOK_API_VERSION`
- the scaffold floor, derived from the same major/minor version

Do not retry a burned version.

## Hook Init Network Requirement

`untaped-recipe hook init`, `recipe hook init`, and `pack hook init` run
`uv lock` after writing hook metadata. The scaffold includes
`untaped-recipe-hook-api` as a dev dependency, so hook initialization needs
package-index access unless the user has configured a uv mirror or local source
that provides the package.
