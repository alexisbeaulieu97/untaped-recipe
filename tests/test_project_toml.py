"""Shared pyproject TOML editing helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped_recipe.domain.project_toml import read_toml_document, toml_table


def test_read_toml_document_reports_neutral_pyproject_errors(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project\n")

    with pytest.raises(ValueError, match=r"invalid pyproject\.toml") as exc_info:
        read_toml_document(pyproject)

    assert "recipe project" not in str(exc_info.value)


def test_toml_table_returns_none_for_missing_non_creatable_table(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\n')
    doc = read_toml_document(pyproject)

    assert toml_table(doc, "tool", "tool", create=False) is None


def test_toml_table_rejects_existing_non_table_value(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("tool = true\n")
    doc = read_toml_document(pyproject)

    with pytest.raises(ValueError, match=r"\[tool\] must be a table"):
        toml_table(doc, "tool", "tool", create=True)
