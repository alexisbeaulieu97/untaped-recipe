# Release

`untaped-recipe` is published to PyPI so users can install the CLI with a plain
package requirement and scaffolded hook projects can resolve their editor/type
dependency from `untaped_recipe.hook_api`.

Use the `Release` GitHub Actions workflow for releases. Do not manually create
the GitHub release/tag for PyPI-backed versions.

## One-Time Setup

Publish the `untaped` SDK to PyPI first; `untaped-recipe` depends on
`untaped>=2.4.0,<3` and release smokes intentionally resolve that dependency
from PyPI. Once the SDK is available, remove the temporary
`tool.uv.sources.untaped` git source and relock before publishing
`untaped-recipe`. The release workflow verifies that SDK dependency before
publishing `untaped-recipe`.

Create the `untaped-recipe` project on PyPI and TestPyPI, then add Trusted
Publishers for this repository:

- workflow: `.github/workflows/release.yml`
- environments: `pypi` and `testpypi`
- package: `untaped-recipe`

Protect the `pypi` GitHub environment with required reviewers. TestPyPI can be
less restrictive, but it should still use Trusted Publishing rather than a
long-lived token.

## Release Flow

1. Dispatch the `Release` workflow with `index = testpypi` and the target
   version. This publishes `untaped-recipe` to TestPyPI and runs a real
   scaffold smoke against that index while allowing PyPI for dependencies. It
   does not create a GitHub release.
2. If the TestPyPI run passes, dispatch the same workflow from `main` with
   `index = pypi` and the same version.
3. The production workflow verifies versions, runs tests, builds the package,
   smokes scaffold locking against the local wheel, publishes `untaped-recipe`,
   waits for PyPI availability, smokes scaffold locking against PyPI, then
   creates the GitHub release/tag.

## Version Burn Recovery

PyPI does not allow re-uploading the same distribution filename after a publish,
even if the file is deleted. If the publish step succeeds but post-publish
verification never passes, treat that version as permanently burned.

Recovery is to bump the package patch version and rerun the workflow. Bump
`HOOK_API_VERSION` and the derived `requires_hook_api` scaffold floor only when
the helper contract changes.

- root `pyproject.toml`
- `src/untaped_recipe/_version.py`

Do not retry a burned version.

## Hook Init Network Requirement

`untaped-recipe hook init`, `recipe hook init`, and `pack hook init` run
`uv lock` after writing hook metadata. The scaffold includes `untaped-recipe`
as a dev dependency for editor/type discovery, so hook initialization needs
package-index access unless the user has configured a uv mirror or local source
that provides the package.
