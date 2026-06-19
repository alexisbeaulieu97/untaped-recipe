"""Comment-preserving YAML helpers backed by ruamel.yaml."""

from __future__ import annotations

from io import StringIO

from ruamel.yaml import YAML


def load_yaml(content: str) -> object:
    """Load YAML while preserving round-trip metadata."""
    yaml = _yaml()
    return yaml.load(content)


def dump_yaml(data: object) -> str:
    """Dump YAML while preserving round-trip metadata."""
    yaml = _yaml()
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml
