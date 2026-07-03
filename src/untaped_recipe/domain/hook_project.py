"""Models for uv-managed hook project metadata."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, field_validator

from untaped_recipe.hook_api import HOOK_API_VERSION

_DOTTED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
HookKind = Literal["transform", "validate"]


class HookDefinition(BaseModel):
    """One public hook entry in a hook project's pyproject metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    kind: HookKind
    module: str = ""

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value: object) -> HookKind:
        if not isinstance(value, str):
            raise ValueError("invalid hook kind")
        value = value.strip()
        if value not in {"transform", "validate"}:
            raise ValueError("invalid hook kind")
        return cast(HookKind, value)

    @field_validator("module")
    @classmethod
    def _module_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("module is required")
        if not is_valid_dotted_name(value):
            raise ValueError(f"invalid module name: {value}")
        return value


class HookProjectMetadata(BaseModel):
    """Parsed `[tool.untaped_recipe.hooks]` table."""

    model_config = ConfigDict(frozen=True)

    hooks: dict[str, HookDefinition]
    requires_hook_api: str | None = None
    runtime_dependencies: tuple[str, ...] = ()

    @field_validator("hooks")
    @classmethod
    def _hook_names(cls, value: dict[str, HookDefinition]) -> dict[str, HookDefinition]:
        for name in value:
            if not is_valid_dotted_name(name):
                raise ValueError(f"invalid hook name: {name}")
        return value

    @classmethod
    def from_pyproject(cls, data: Mapping[str, object]) -> HookProjectMetadata:
        """Build hook metadata from parsed pyproject data."""
        project = _mapping(data.get("project"), "project")
        runtime_dependencies = _runtime_dependencies(project)
        tool_config = _nested_mapping(data, ("tool", "untaped_recipe"))
        if tool_config is not None and not isinstance(tool_config, Mapping):
            raise ValueError("[tool.untaped_recipe] must be a table")
        requires_hook_api = None
        if isinstance(tool_config, Mapping):
            raw_requires = tool_config.get("requires_hook_api")
            if raw_requires is not None:
                if not isinstance(raw_requires, str):
                    raise ValueError("[tool.untaped_recipe].requires_hook_api must be a string")
                requires_hook_api = raw_requires.strip()
                if not requires_hook_api:
                    raise ValueError("[tool.untaped_recipe].requires_hook_api must not be empty")
                _specifier_set(requires_hook_api)
        hooks = _nested_mapping(data, ("tool", "untaped_recipe", "hooks"))
        if hooks is None:
            return cls(
                hooks={},
                requires_hook_api=requires_hook_api,
                runtime_dependencies=runtime_dependencies,
            )
        if not isinstance(hooks, Mapping):
            raise ValueError("[tool.untaped_recipe.hooks] must be a table")
        _reject_legacy_hook_rows(hooks)
        return cls(
            hooks=dict(hooks),
            requires_hook_api=requires_hook_api,
            runtime_dependencies=runtime_dependencies,
        )


def is_valid_dotted_name(name: str) -> bool:
    """Return true when ``name`` is a safe dotted hook/module identifier."""
    return bool(_DOTTED_NAME_RE.fullmatch(name.strip()))


def normalize_hook_name(name: str) -> str:
    """Validate and return a public hook name."""
    normalized = name.strip()
    if not is_valid_dotted_name(normalized):
        raise ValueError(f"invalid hook name: {name}")
    return normalized


def project_name_for_hook(name: str) -> str:
    """Return the hook library project directory for a public hook name."""
    return normalize_hook_name(name).split(".", maxsplit=1)[0]


def project_name_from_metadata(metadata: HookProjectMetadata) -> str:
    """Return the single library project directory implied by hook metadata."""
    if not metadata.hooks:
        raise ValueError("hook project must declare at least one hook")
    project_names = {project_name_for_hook(public_name) for public_name in metadata.hooks}
    if len(project_names) != 1:
        raise ValueError("hook project hooks must share the same namespace")
    return next(iter(project_names))


def read_hook_metadata(project_root: Path) -> HookProjectMetadata:
    """Read hook metadata from a uv hook project's pyproject."""
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ValueError(f"hook project must contain pyproject.toml: {project_root}")
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid hook project pyproject: {pyproject}") from exc
    return HookProjectMetadata.from_pyproject(data)


def hook_module_file(project_root: Path, module: str) -> Path:
    """Return the required src-layout file path for a declared hook module."""
    return project_root / "src" / Path(*module.split(".")).with_suffix(".py")


def validate_hook_modules(project_root: Path, metadata: HookProjectMetadata) -> None:
    """Require every declared hook module to resolve to a file under ``src``."""
    for definition in metadata.hooks.values():
        module_file = hook_module_file(project_root, definition.module)
        if not module_file.is_file():
            raise ValueError(f"hook module file not found: {module_file}")


def validate_hook_project_contract(project_root: Path, metadata: HookProjectMetadata) -> None:
    """Require hook projects to be compatible with the running helper API."""
    for dependency in metadata.runtime_dependencies:
        if dependency_name(dependency) == "untaped-recipe":
            raise ValueError(
                "hook project must not depend on untaped-recipe at runtime; "
                "add untaped-recipe to dependency-groups.dev instead: "
                f"{project_root}"
            )
    if metadata.requires_hook_api is None:
        return
    specifier = _specifier_set(metadata.requires_hook_api)
    if Version(HOOK_API_VERSION) not in specifier:
        raise ValueError(
            f"hook project requires hook API {metadata.requires_hook_api}, "
            f"but untaped-recipe provides {HOOK_API_VERSION}: {project_root}"
        )


def _reject_legacy_hook_rows(hooks: Mapping[object, object]) -> None:
    for name, definition in hooks.items():
        if not isinstance(definition, Mapping):
            continue
        if "kind" not in definition:
            raise ValueError(
                "hook kind is required; update old hook metadata "
                f"{name!r} = {{ module = ... }} to include "
                '{ kind = "transform"|"validate", module = ... }'
            )


def _nested_mapping(data: Mapping[str, object], path: tuple[str, ...]) -> object | None:
    current: object = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _mapping(value: object, field: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"[{field}] must be a table")
    return value


def _runtime_dependencies(project: Mapping[str, object] | None) -> tuple[str, ...]:
    if project is None:
        return ()
    raw_dependencies = project.get("dependencies")
    if raw_dependencies is None:
        return ()
    if not isinstance(raw_dependencies, list):
        raise ValueError("[project].dependencies must be an array")
    dependencies: list[str] = []
    for dependency in raw_dependencies:
        if not isinstance(dependency, str):
            raise ValueError("[project].dependencies entries must be strings")
        dependency_name(dependency)
        dependencies.append(dependency)
    return tuple(dependencies)


def dependency_name(dependency: str) -> str:
    """Return the normalized PEP 508 project name for a dependency string."""
    try:
        return canonicalize_name(Requirement(dependency).name)
    except InvalidRequirement as exc:
        raise ValueError(
            f"[project].dependencies entry must be a valid requirement: {dependency}"
        ) from exc


def _specifier_set(value: str) -> SpecifierSet:
    try:
        return SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise ValueError(f"invalid hook API requirement: {value}") from exc
