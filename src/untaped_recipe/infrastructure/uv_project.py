"""Shared helpers for uv-backed recipe, pack, and hook projects."""

from __future__ import annotations

import subprocess
from pathlib import Path


def check_lock(project_root: Path) -> None:
    """Assert ``uv.lock`` is up to date with the project (``uv lock --check``)."""
    try:
        result = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("uv executable not found for project lock") from exc
    if result.returncode != 0:
        message = f"lockfile is stale — run 'uv lock' in {project_root}"
        detail = result.stderr.strip() or result.stdout.strip()
        if detail:
            message = f"{message}: {detail}"
        raise ValueError(message)


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
