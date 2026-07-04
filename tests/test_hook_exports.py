"""Tests for hook export discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.domain.hook_exports import hook_exports, hook_exports_from_source


def test_detects_transform_only() -> None:
    src = "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"

    assert hook_exports_from_source(src) == frozenset({"transform"})


def test_detects_dual_exports() -> None:
    src = "def transform(c, **kw):\n    return c\n\ndef validate(**kw):\n    return None\n"

    assert hook_exports_from_source(src) == frozenset({"transform", "validate"})


def test_ignores_nested_and_other_functions() -> None:
    src = "def helper():\n    def transform():\n        pass\n"

    assert hook_exports_from_source(src) == frozenset()


def test_detects_async_defs_as_exports() -> None:
    src = "async def validate(**kw):\n    return None\n"

    assert hook_exports_from_source(src) == frozenset({"validate"})


def test_file_variant_raises_with_path_on_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "hook.py"
    bad.write_text("def transform(:\n", encoding="utf-8")

    with pytest.raises(ValueError, match=str(bad)):
        hook_exports(bad)
