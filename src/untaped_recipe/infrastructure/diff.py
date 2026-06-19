"""Unified diff rendering for planned file changes."""

from __future__ import annotations

import difflib

from untaped_recipe.domain.plan import FileChange


def unified_diff(change: FileChange) -> str:
    """Render a unified diff for one change."""
    before = [] if change.before is None else change.before.splitlines(keepends=True)
    after = [] if change.after is None else change.after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before,
            after,
            fromfile=f"a/{change.relative_path}",
            tofile=f"b/{change.relative_path}",
        )
    )
