"""Pydantic models for recipe schema v1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from untaped_recipe.domain.paths import safe_relative_path

ScalarInputType = Literal["str", "int", "bool", "float"]
InputType = Literal["str", "int", "bool", "float", "list", "dict"]
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
    items: ScalarInputType | None = None
    values: ScalarInputType | None = None

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
    def _validate_input_spec(self) -> InputSpec:
        if self.scope == "global" and self.from_:
            raise ValueError("input with scope global cannot declare from")
        if self.items is not None and self.type != "list":
            raise ValueError("items is only valid with type list")
        if self.values is not None and self.type != "dict":
            raise ValueError("values is only valid with type dict")
        return self

    def coerce(self, value: object) -> object:
        """Coerce a CLI/YAML-supplied value to this input's declared type."""
        if self.type == "list":
            if not isinstance(value, Sequence) or isinstance(value, str | bytes):
                raise ValueError("cannot coerce value to list")
            item_type = self.items or "str"
            try:
                return [_coerce_scalar_element(item, item_type) for item in value]
            except ValueError:
                raise ValueError("cannot coerce value to list") from None
        if self.type == "dict":
            if not isinstance(value, Mapping):
                raise ValueError("cannot coerce value to dict")
            value_type = self.values or "str"
            coerced: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("dict input keys must be strings")
                try:
                    coerced[key] = _coerce_scalar_element(item, value_type)
                except ValueError:
                    raise ValueError("cannot coerce value to dict") from None
            return coerced
        return _coerce_scalar(value, self.type)


def _coerce_scalar_element(value: object, input_type: ScalarInputType) -> object:
    if isinstance(value, Mapping) or (
        isinstance(value, Sequence) and not isinstance(value, str | bytes)
    ):
        raise ValueError(f"cannot coerce value to {input_type}")
    return _coerce_scalar(value, input_type)


def _coerce_scalar(value: object, input_type: ScalarInputType) -> object:
    if input_type == "str":
        return str(value)
    if input_type == "int":
        try:
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, float):
                return int(value)
            return int(str(value))
        except TypeError, ValueError, OverflowError:
            raise ValueError("cannot coerce value to int") from None
    if input_type == "float":
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
    file: Path | None = None
    globs: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    hook: str
    optional: bool = False

    @field_validator("file")
    @classmethod
    def _safe_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return safe_relative_path(value, field="file")

    @model_validator(mode="after")
    def _validate_file_or_globs(self) -> TransformStep:
        if (self.file is None) == (not self.globs):
            raise ValueError("transform step requires exactly one of file, files, or globs")
        if self.exclude and not self.globs:
            raise ValueError("exclude is only valid with globs")
        if self.optional and self.globs:
            raise ValueError("optional is not valid with globs")
        return self


class TemplateStep(BaseStep):
    """Render a recipe-local template into a target file."""

    type: Literal["template"]
    template: Path
    dest: Path
    unknown_tokens: Literal["error", "keep"] = "error"
    if_absent: bool = False

    @field_validator("template", "dest")
    @classmethod
    def _safe_paths(cls, value: Path) -> Path:
        return safe_relative_path(value, field="path")


class CopyStep(BaseStep):
    """Copy a recipe-local file into a target file."""

    type: Literal["copy"]
    source: Path
    dest: Path
    if_absent: bool = False

    @field_validator("source", "dest")
    @classmethod
    def _safe_paths(cls, value: Path) -> Path:
        return safe_relative_path(value, field="path")


class RemoveStep(BaseStep):
    """Remove one target-relative file."""

    type: Literal["remove"]
    file: Path | None = None
    globs: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @field_validator("file")
    @classmethod
    def _safe_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return safe_relative_path(value, field="file")

    @model_validator(mode="after")
    def _validate_file_or_globs(self) -> RemoveStep:
        if (self.file is None) == (not self.globs):
            raise ValueError("remove step requires exactly one of file, files, or globs")
        if self.exclude and not self.globs:
            raise ValueError("exclude is only valid with globs")
        return self


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
    has_globs = "globs" in step
    if "exclude" in step and not has_globs:
        raise ValueError("exclude is only valid with globs")
    if step_type == "transform" and has_globs and "optional" in step:
        raise ValueError("optional is not valid with globs")
    if sum((has_file, has_files, has_globs)) != 1:
        raise ValueError(f"{step_type} step requires exactly one of file, files, or globs")
    if has_globs:
        _validate_non_empty_strings(step["globs"], field="globs")
        if "exclude" in step:
            _validate_non_empty_strings(step["exclude"], field="exclude")
        return [step]
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


def _validate_non_empty_strings(value: object, *, field: str) -> None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        raise ValueError(f"{field} must not be empty")
    if any(not isinstance(entry, str) or not entry for entry in value):
        raise ValueError(f"{field} entries must be non-empty strings")
