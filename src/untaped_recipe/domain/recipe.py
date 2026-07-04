"""Pydantic models for recipe schema v1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from untaped_recipe.domain.paths import safe_relative_path

InputType = Literal["str", "int", "bool", "float"]
InputScope = Literal["target", "global"]


class InputSpec(BaseModel):
    """One declared recipe input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: InputType = "str"
    default: object | None = None
    required: bool = False
    description: str = ""
    sensitive: bool = False
    scope: InputScope = "global"
    from_: tuple[str, ...] = Field(default=(), alias="from")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input_metadata(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        raw_from = data.get("from", ())
        if raw_from is None:
            from_values: tuple[str, ...] = ()
        elif isinstance(raw_from, str):
            from_values = (raw_from,)
        elif isinstance(raw_from, Sequence) and not isinstance(raw_from, bytes):
            from_values = tuple(raw_from)
        else:
            data["from"] = raw_from
            return data
        data["from"] = from_values
        if data.get("scope") is None:
            data["scope"] = "target" if from_values else "global"
        return data

    @model_validator(mode="after")
    def _validate_scope(self) -> InputSpec:
        if self.scope == "global" and self.from_:
            raise ValueError("input with scope global cannot declare from")
        return self

    def coerce(self, value: object) -> object:
        """Coerce a CLI/YAML-supplied value to this input's declared type."""
        if self.type == "str":
            return str(value)
        if self.type == "int":
            try:
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
                if isinstance(value, float):
                    return int(value)
                return int(str(value))
            except TypeError, ValueError, OverflowError:
                raise ValueError("cannot coerce value to int") from None
        if self.type == "float":
            try:
                if isinstance(value, int | float) and not isinstance(value, bool):
                    return float(value)
                return float(str(value))
            except TypeError, ValueError, OverflowError:
                raise ValueError("cannot coerce value to float") from None
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError("cannot coerce value to bool")


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
    optional: bool = False

    @field_validator("file")
    @classmethod
    def _safe_file(cls, value: Path) -> Path:
        return safe_relative_path(value, field="file")


class TemplateStep(BaseStep):
    """Render a recipe-local template into a target file."""

    type: Literal["template"]
    template: Path
    dest: Path
    unknown_tokens: Literal["error", "keep"] = "error"

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
    description: str = ""
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    steps: tuple[Step, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalize_file_fanout(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        steps = data.get("steps")
        if not isinstance(steps, list | tuple):
            return value
        normalized: list[object] = []
        for step in steps:
            normalized.extend(_normalize_file_step(step))
        data["steps"] = normalized
        return data


def _normalize_file_step(step: object) -> list[object]:
    if not isinstance(step, Mapping):
        return [step]
    step_type = step.get("type")
    if step_type not in {"transform", "remove"}:
        return [step]

    has_file = "file" in step
    has_files = "files" in step
    if has_file == has_files:
        raise ValueError(f"{step_type} step requires exactly one of file or files")
    if not has_files:
        return [step]

    files = step["files"]
    if not isinstance(files, Sequence) or isinstance(files, str | bytes) or not files:
        raise ValueError("files must not be empty")

    base = dict(step)
    del base["files"]
    expanded: list[object] = []
    for file_value in files:
        single = dict(base)
        single["file"] = file_value
        expanded.append(single)
    return expanded
