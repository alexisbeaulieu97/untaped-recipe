"""Pydantic models for recipe schema v1."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from untaped_recipe.domain.paths import safe_relative_path

InputType = Literal["str", "int", "bool", "float"]


class InputSpec(BaseModel):
    """One declared recipe input."""

    model_config = ConfigDict(frozen=True)

    type: InputType = "str"
    default: object | None = None
    required: bool = False

    def coerce(self, value: object) -> object:
        """Coerce a CLI/YAML-supplied value to this input's declared type."""
        if self.type == "str":
            return str(value)
        if self.type == "int":
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, float):
                return int(value)
            return int(str(value))
        if self.type == "float":
            if isinstance(value, int | float) and not isinstance(value, bool):
                return float(value)
            return float(str(value))
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"cannot coerce {value!r} to bool")

    @staticmethod
    def resolve_all(
        specs: dict[str, InputSpec],
        *,
        overrides: dict[str, object],
    ) -> dict[str, object]:
        """Resolve all declared inputs from overrides and defaults."""
        unknown = sorted(set(overrides) - set(specs))
        if unknown:
            raise ValueError(f"unknown input: {unknown[0]}")
        values: dict[str, object] = {}
        for name, spec in specs.items():
            if name in overrides:
                values[name] = spec.coerce(overrides[name])
            elif spec.default is not None:
                values[name] = spec.coerce(spec.default)
            elif spec.required:
                raise ValueError(f"missing required input: {name}")
        return values


class BaseStep(BaseModel):
    """Common recipe step fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    args: dict[str, object] = Field(default_factory=dict)


class ValidateStep(BaseStep):
    """Read-only validation hook step."""

    type: Literal["validate"]
    hook: str


class TransformStep(BaseStep):
    """Content transform hook step."""

    type: Literal["transform"]
    file: Path
    hook: str

    @field_validator("file")
    @classmethod
    def _safe_file(cls, value: Path) -> Path:
        return safe_relative_path(value, field="file")


class TemplateStep(BaseStep):
    """Render a recipe-local template into a target file."""

    type: Literal["template"]
    template: Path
    dest: Path

    @field_validator("template", "dest")
    @classmethod
    def _safe_paths(cls, value: Path) -> Path:
        return safe_relative_path(value, field="path")


class CopyStep(BaseStep):
    """Copy a recipe-local file into a target file."""

    type: Literal["copy"]
    source: Path
    dest: Path

    @field_validator("source", "dest")
    @classmethod
    def _safe_paths(cls, value: Path) -> Path:
        return safe_relative_path(value, field="path")


class RemoveStep(BaseStep):
    """Remove one target-relative file."""

    type: Literal["remove"]
    file: Path

    @field_validator("file")
    @classmethod
    def _safe_file(cls, value: Path) -> Path:
        return safe_relative_path(value, field="file")


Step = Annotated[
    ValidateStep | TransformStep | TemplateStep | CopyStep | RemoveStep,
    Field(discriminator="type"),
]


class Recipe(BaseModel):
    """Recipe schema v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    name: str
    description: str = ""
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    steps: tuple[Step, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recipe name cannot be blank")
        return value
