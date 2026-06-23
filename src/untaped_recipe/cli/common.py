"""Shared CLI helpers."""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from untaped.api import ConfigError, get_config_section, report_errors

from untaped_recipe.settings import RecipeSettings


def settings() -> RecipeSettings:
    """Read active recipe settings."""
    return get_config_section("recipe", RecipeSettings)


def library_root() -> Path:
    """Configured recipe library root."""
    return settings().library_root.expanduser()


def load_yaml_mapping_file(path: Path, *, flag: str) -> dict[str, object]:
    """Load a YAML mapping from a CLI file flag."""
    try:
        loaded = yaml.safe_load(path.expanduser().read_text()) or {}
    except OSError as exc:
        raise ConfigError(f"{flag} file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{flag} file is invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{flag} file must contain a YAML mapping")
    return {str(key): value for key, value in loaded.items()}


def edit_path(path: Path) -> None:
    """Open a path in the user's configured terminal editor."""
    editor = shlex.split(os.environ.get("VISUAL") or os.environ.get("EDITOR") or "")
    if not editor:
        raise ConfigError("set $VISUAL or $EDITOR to use edit")
    try:
        subprocess.run([*editor, str(path)], check=True)
    except FileNotFoundError as exc:
        raise ConfigError(f"editor not found: {editor[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise ConfigError(f"editor exited with status {exc.returncode}") from exc


@contextmanager
def report_config_errors() -> Iterator[None]:
    """Report expected config/library errors without Python tracebacks."""
    with report_errors():
        try:
            yield
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
