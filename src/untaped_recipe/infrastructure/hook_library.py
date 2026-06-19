"""Local reusable uv hook project library storage."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from untaped_recipe.domain.hook_project import HookProjectMetadata
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

    def init(self, name: str) -> Path:
        """Scaffold a uv hook project in the global hook library."""
        public_name = _public_hook_name(name)
        project_name = _project_name_for(public_name)
        project_root = self.hooks_dir / project_name
        if project_root.exists():
            raise ValueError(f"hook already exists: {project_name}")

        module_leaf = public_name.rsplit(".", maxsplit=1)[-1]
        package = _package_name(project_name)
        module = f"{package}.hooks.{module_leaf}"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        (project_root / "src" / package / "hooks").mkdir(parents=True)
        (project_root / "src" / package / "__init__.py").write_text("")
        (project_root / "src" / package / "hooks" / "__init__.py").write_text("")
        (project_root / "src" / package / "hooks" / f"{module_leaf}.py").write_text(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n"
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
        _lock_project(project_root)
        return project_root

    def add(self, source: Path, *, name: str | None = None) -> Path:
        """Copy a uv hook project into the library."""
        if not source.exists():
            raise ValueError(f"hook source not found: {source}")
        if not source.is_dir():
            raise ValueError("hook source must be a uv hook project directory")
        _metadata_for(source)
        if not (source / "uv.lock").is_file():
            raise ValueError(f"hook project is missing uv.lock: {source}")
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_name = safe_library_name(name or source.name)
        dest = self.hooks_dir / hook_name
        if dest.exists():
            raise ValueError(f"hook already exists: {hook_name}")
        shutil.copytree(source, dest)
        return dest

    def resolve(self, name: str) -> Path:
        """Resolve a reusable hook project name or explicit project path."""
        explicit = Path(name).expanduser()
        if explicit.is_dir() and (explicit / "pyproject.toml").is_file():
            return explicit
        hook_name = safe_library_name(_project_name_for(name), field="hook")
        path = self.hooks_dir / hook_name
        if path.is_dir() and (path / "pyproject.toml").is_file():
            return path
        raise ValueError(f"hook not found: {name}")

    def resolve_editable(self, name: str) -> Path:
        """Resolve the best editable source file for a hook project or hook name."""
        project = self.resolve(name)
        metadata = _metadata_for(project)
        definition = metadata.hooks.get(name)
        if definition is None:
            project_name = project.name
            definition = metadata.hooks.get(project_name)
        if definition is not None:
            module_path = _module_file(project, definition.module)
            if module_path.is_file():
                return module_path
        return project / "pyproject.toml"

    def remove(self, name: str) -> Path:
        """Remove a reusable hook project."""
        hook_name = safe_library_name(_project_name_for(name), field="hook")
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
            metadata = _metadata_for(path)
            entries.append(
                HookEntry(
                    name=path.name,
                    path=path,
                    hooks=tuple(sorted(metadata.hooks)),
                )
            )
        return entries


def _metadata_for(project_root: Path) -> HookProjectMetadata:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ValueError(f"hook project must contain pyproject.toml: {project_root}")
    try:
        data = tomllib.loads(pyproject.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid hook project pyproject: {pyproject}") from exc
    return HookProjectMetadata.from_pyproject(data)


def _public_hook_name(name: str) -> str:
    metadata = HookProjectMetadata(hooks={name: {"module": "valid.module"}})
    return next(iter(metadata.hooks))


def _project_name_for(name: str) -> str:
    return name.split(".", maxsplit=1)[0]


def _package_name(project_name: str) -> str:
    return "untaped_recipe_hooks_" + project_name.replace("-", "_").replace(".", "_")


def _module_file(project_root: Path, module: str) -> Path:
    return project_root / "src" / Path(*module.split(".")).with_suffix(".py")


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
