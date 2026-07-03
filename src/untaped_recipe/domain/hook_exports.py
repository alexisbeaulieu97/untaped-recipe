"""AST-level discovery of hook entry points, without importing hook code."""

from __future__ import annotations

import ast
from pathlib import Path

HOOK_FUNCTION_NAMES = frozenset({"transform", "validate"})


def hook_exports_from_source(source: str) -> frozenset[str]:
    """Return which hook entry points a module's top level defines."""
    tree = ast.parse(source)
    found = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name in HOOK_FUNCTION_NAMES
    }
    return frozenset(found)


def hook_exports(module_file: Path) -> frozenset[str]:
    """Scan a hook module file for entry points; never import it."""
    try:
        source = module_file.read_text(encoding="utf-8")
        return hook_exports_from_source(source)
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError(f"cannot scan hook module {module_file}: {error}") from error
