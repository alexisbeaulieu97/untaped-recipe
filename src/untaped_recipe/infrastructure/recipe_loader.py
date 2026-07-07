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
        raise ValueError(f"{path}: invalid recipe YAML: {exc}") from exc
    try:
        return Recipe.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{path}: {_validation_message(exc)}") from exc


def _validation_message(exc: ValidationError) -> str:
    """Render schema violations in the recipe's own vocabulary, not pydantic's."""
    parts: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        if error["type"] == "extra_forbidden":
            parts.append(f"{location or 'recipe'} is not allowed here")
        else:
            parts.append(f"{location or 'recipe'}: {error['msg']}")
    return "invalid recipe: " + "; ".join(parts)
