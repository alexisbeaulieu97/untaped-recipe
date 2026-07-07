"""Shared test fixtures for untaped-recipe."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped.settings import get_settings


@pytest.fixture(autouse=True)
def stub_lock_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real `uv lock --check` probe in unit tests.

    The suite's packs write placeholder uv.lock files (existence-only
    convention); a real probe would need uv resolution per check. Tests that
    exercise the probe re-patch `check_pack.check_lock` explicitly.
    """
    monkeypatch.setattr(
        "untaped_recipe.application.check_pack.check_lock",
        lambda project_root: None,
    )


@pytest.fixture
def isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Use an isolated untaped config for CLI tests."""
    cfg = tmp_path / "config.yml"
    library = tmp_path / "recipe-library"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.setenv("UNTAPED_RECIPE__LIBRARY_ROOT", str(library))
    get_settings.cache_clear()
    yield cfg
    get_settings.cache_clear()
