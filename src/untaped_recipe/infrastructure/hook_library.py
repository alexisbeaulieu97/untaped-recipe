"""Local reusable uv hook project library storage."""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from untaped_recipe.domain.hook_project import (
    hook_module_file,
    normalize_hook_name,
    project_name_for_hook,
    project_name_from_metadata,
    read_hook_metadata,
    validate_hook_modules,
)
from untaped_recipe.domain.paths import safe_library_name


@dataclass(frozen=True)
class HookEntry:
    """One hook project library entry."""

    name: str
    path: Path
    hooks: tuple[str, ...]


class HookLibrary:
    """Manage global reusable uv hook projects."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def hooks_dir(self) -> Path:
        """Directory containing reusable hook projects."""
        return self._root / "hooks"

    def init(self, name: str, *, kind: Literal["transform", "validate"] = "transform") -> Path:
        """Scaffold a uv hook project in the global hook library."""
        public_name = normalize_hook_name(name)
        project_name = project_name_for_hook(public_name)
        project_root = self.hooks_dir / project_name
        if project_root.exists():
            raise ValueError(f"hook already exists: {project_name}")

        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        temp_root = self.hooks_dir / f".{project_name}.tmp-{uuid.uuid4().hex}"
        try:
            self._scaffold(public_name, project_name, temp_root, kind=kind)
            _lock_project(temp_root)
            temp_root.rename(project_root)
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise
        return project_root

    def _scaffold(
        self,
        public_name: str,
        project_name: str,
        project_root: Path,
        *,
        kind: Literal["transform", "validate"],
    ) -> None:
        """Write a hook project scaffold under ``project_root``."""
        module_leaf = public_name.rsplit(".", maxsplit=1)[-1]
        package = _package_name(project_name)
        module = f"{package}.hooks.{module_leaf}"
        (project_root / "src" / package / "hooks").mkdir(parents=True)
        (project_root / "src" / package / "__init__.py").write_text("")
        (project_root / "src" / package / "hooks" / "__init__.py").write_text("")
        (project_root / "src" / package / "hooks" / f"{module_leaf}.py").write_text(
            _hook_stub(kind)
        )
        (project_root / "pyproject.toml").write_text(
            "[project]\n"
            f'name = "untaped-recipe-hooks-{project_name}"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.14"\n'
            "dependencies = []\n\n"
            "[tool.untaped_recipe.hooks]\n"
            f'"{public_name}" = {{ module = "{module}" }}\n'
        )

    def add(self, source: Path, *, name: str | None = None) -> Path:
        """Copy a uv hook project into the library."""
        if not source.exists():
            raise ValueError(f"hook source not found: {source}")
        if not source.is_dir():
            raise ValueError("hook source must be a uv hook project directory")
        metadata = read_hook_metadata(source)
        declared_name = project_name_from_metadata(metadata)
        validate_hook_modules(source, metadata)
        if not (source / "uv.lock").is_file():
            raise ValueError(f"hook project is missing uv.lock: {source}")
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_name = safe_library_name(name or declared_name)
        if hook_name != declared_name:
            raise ValueError(
                f"hook library name must match declared hook namespace: {declared_name}"
            )
        dest = self.hooks_dir / hook_name
        if dest.exists():
            raise ValueError(f"hook already exists: {hook_name}")
        shutil.copytree(source, dest)
        return dest

    def resolve(self, name: str) -> Path:
        """Resolve a reusable hook project name or explicit project path."""
        explicit = Path(name).expanduser()
        if (
            _is_explicit_path(name)
            and explicit.is_dir()
            and (explicit / "pyproject.toml").is_file()
        ):
            return explicit.resolve()
        hook_name = _library_project_name(name)
        path = self.hooks_dir / hook_name
        if path.is_dir() and (path / "pyproject.toml").is_file():
            return path
        raise ValueError(f"hook not found: {name}")

    def resolve_editable(self, name: str) -> Path:
        """Resolve the best editable source file for a hook project or hook name."""
        project = self.resolve(name)
        metadata = read_hook_metadata(project)
        definition = metadata.hooks.get(name)
        if definition is None:
            project_name = project.name
            definition = metadata.hooks.get(project_name)
        if definition is not None:
            module_path = hook_module_file(project, definition.module)
            if module_path.is_file():
                return module_path
        return project / "pyproject.toml"

    def remove(self, name: str) -> Path:
        """Remove a reusable hook project."""
        hook_name = _library_project_name(name)
        path = self.hooks_dir / hook_name
        if not path.is_dir():
            raise ValueError(f"hook not found: {name}")
        shutil.rmtree(path)
        return path

    def list(self) -> list[HookEntry]:
        """List global reusable hook projects."""
        if not self.hooks_dir.is_dir():
            return []
        entries: list[HookEntry] = []
        for path in sorted(self.hooks_dir.iterdir(), key=lambda p: p.name):
            if not path.is_dir() or not (path / "pyproject.toml").is_file():
                continue
            metadata = read_hook_metadata(path)
            entries.append(
                HookEntry(
                    name=path.name,
                    path=path,
                    hooks=tuple(sorted(metadata.hooks)),
                )
            )
        return entries


def add_hook_to_project(
    project_root: Path,
    name: str,
    *,
    kind: Literal["transform", "validate"] = "transform",
) -> Path:
    """Scaffold a hook module inside an existing recipe or pack uv project."""
    if not (project_root / "pyproject.toml").is_file():
        raise ValueError(f"project must contain pyproject.toml: {project_root}")
    public_name = normalize_hook_name(name)
    metadata = read_hook_metadata(project_root)
    if public_name in metadata.hooks:
        raise ValueError(f"hook already exists: {public_name}")
    module_leaf = public_name.rsplit(".", maxsplit=1)[-1]
    package = _local_package_name(project_root.name)
    module = f"{package}.hooks.{module_leaf}"
    module_path = project_root / "src" / package / "hooks" / f"{module_leaf}.py"
    if module_path.exists():
        raise ValueError(f"hook module already exists: {module_path}")
    module_path.parent.mkdir(parents=True, exist_ok=True)
    (project_root / "src" / package / "__init__.py").touch()
    (project_root / "src" / package / "hooks" / "__init__.py").touch()
    module_path.write_text(_hook_stub(kind))
    _append_hook_metadata(project_root / "pyproject.toml", public_name, module)
    _lock_project(project_root)
    return module_path


def _is_explicit_path(name: str) -> bool:
    return name.startswith(("/", "./", "../", "~")) or "/" in name


def _library_project_name(name: str) -> str:
    try:
        project_name = project_name_for_hook(name)
    except ValueError as exc:
        raise ValueError("hook must be a safe library name") from exc
    return safe_library_name(project_name, field="hook")


def _package_name(project_name: str) -> str:
    return "untaped_recipe_hooks_" + project_name.replace("-", "_").replace(".", "_")


def _local_package_name(project_name: str) -> str:
    return project_name.replace("-", "_").replace(".", "_") + "_hooks"


def _hook_stub(kind: Literal["transform", "validate"]) -> str:
    if kind == "validate":
        return "def validate(*, inputs, target, args, helpers):\n    return helpers.pass_()\n"
    return "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"


def _append_hook_metadata(path: Path, public_name: str, module: str) -> None:
    current = path.read_text().rstrip()
    path.write_text(
        f'{current}\n\n[tool.untaped_recipe.hooks."{public_name}"]\nmodule = "{module}"\n'
    )


def _lock_project(project_root: Path) -> None:
    try:
        subprocess.run(
            ["uv", "lock"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("uv executable not found for hook project initialization") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        message = "failed to create hook project uv.lock"
        if detail:
            message = f"{message}: {detail}"
        raise ValueError(message) from exc
