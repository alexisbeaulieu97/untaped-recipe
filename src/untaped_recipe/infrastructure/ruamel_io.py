"""Comment-preserving YAML helpers backed by ruamel.yaml."""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO

from ruamel.yaml import YAML

from untaped_recipe.yaml_options import apply_yaml_dump_options


def load_yaml(content: str) -> object:
    """Load YAML while preserving round-trip metadata."""
    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml.load(content)


def dump_yaml(data: object, *, options: Mapping[str, object] | None = None) -> str:
    """Dump YAML while preserving round-trip metadata."""
    yaml = YAML()
    apply_yaml_dump_options(yaml, options)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()
