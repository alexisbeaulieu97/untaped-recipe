"""Load trusted local Python hooks from recipe, global, or built-in locations."""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from pathlib import Path
from threading import Lock
from types import ModuleType

_HOOK_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class HookLoader:
    """Resolve and import trusted hook modules."""

    def __init__(self, *, global_hooks: Path, builtins: tuple[Path, ...] = ()) -> None:
        self._global_hooks = global_hooks
        self._builtins = builtins
        self._cache: dict[Path, tuple[int, ModuleType]] = {}
        self._lock = Lock()

    def load(self, name: str, recipe_dir: Path) -> ModuleType:
        """Load a hook module by file path or logical name."""
        path = self.resolve(name, recipe_dir)
        resolved = path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
        with self._lock:
            cached = self._cache.get(resolved)
            if cached is not None and cached[0] == mtime_ns:
                return cached[1]
            module_name = f"_untaped_recipe_hook_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(module_name, resolved)
            if spec is None or spec.loader is None:
                raise ValueError(f"could not load hook: {name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            self._cache[resolved] = (mtime_ns, module)
            return module

    def resolve(self, name: str, recipe_dir: Path) -> Path:
        """Resolve a hook name to a Python file."""
        if not _HOOK_NAME_RE.fullmatch(name):
            raise ValueError(f"hook must be a safe hook name: {name}")
        filename = f"{name}.py"
        candidates = [
            recipe_dir / "hooks" / filename,
            self._global_hooks / filename,
            *(root / filename for root in self._builtins),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise ValueError(f"hook not found: {name}")
