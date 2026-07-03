"""Recipe YAML loading."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from untaped_recipe.domain.recipe import Recipe


def load_recipe_file(path: Path) -> Recipe:
    """Load and validate one recipe YAML file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid recipe YAML: {exc}") from exc
    try:
        return Recipe.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid recipe: {exc}") from exc
