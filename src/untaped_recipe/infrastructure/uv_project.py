"""Shared helpers for uv-backed recipe, pack, and hook projects."""

from __future__ import annotations

import subprocess
from pathlib import Path


def lock_project(project_root: Path) -> None:
    """Refresh ``uv.lock`` for a uv project."""
    try:
        subprocess.run(
            ["uv", "lock"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("uv executable not found for project lock") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        message = "failed to create project uv.lock"
        if detail:
            message = f"{message}: {detail}"
        raise ValueError(message) from exc
