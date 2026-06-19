"""Shared test fixtures for untaped-recipe."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from untaped.settings import get_settings


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
