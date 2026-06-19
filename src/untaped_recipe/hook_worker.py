"""Stdlib-only NDJSON worker for uv-managed external hook projects."""

from __future__ import annotations

import importlib
import json
import re
import sys
import traceback
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


class HookHelpers:
    """Minimal helpers available inside external hook workers."""

    def pass_(self, message: str = "") -> dict[str, str]:
        """Return a passing validation verdict."""
        return {"status": "pass", "message": message}

    def warn(self, message: str) -> dict[str, str]:
        """Return a warning validation verdict."""
        return {"status": "warn", "message": message}

    def fail(self, message: str) -> dict[str, str]:
        """Return a failing validation verdict."""
        return {"status": "fail", "message": message}

    def render_template(self, template: str, inputs: dict[str, object]) -> str:
        """Render simple `{{ input }}` placeholders."""

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in inputs:
                raise ValueError(f"missing template input: {key}")
            return str(inputs[key])

        return _PLACEHOLDER_RE.sub(replace, template)

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content if ruamel.yaml is installed in the hook project."""
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.preserve_quotes = True
        return yaml.load(content)

    def dump_yaml(self, data: object) -> str:
        """Round-trip-dump YAML content if ruamel.yaml is installed in the hook project."""
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.preserve_quotes = True
        out = StringIO()
        yaml.dump(data, out)
        return out.getvalue()


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one decoded worker request."""
    request_id = _required_str(request, "id")
    kind = _required_str(request, "kind")
    module_name = _required_str(request, "module")
    module = importlib.import_module(module_name)
    helpers = HookHelpers()
    if kind == "transform":
        transform = getattr(module, "transform", None)
        if transform is None:
            raise ValueError(f"transform hook module {module_name!r} has no transform callable")
        with redirect_stdout(sys.stderr):
            result = transform(
                _required_str(request, "content"),
                inputs=_mapping(request.get("inputs"), "inputs"),
                target=Path(_required_str(request, "target")),
                file=Path(_required_str(request, "file")),
                args=_mapping(request.get("args"), "args"),
                helpers=helpers,
            )
        if not isinstance(result, str):
            raise ValueError("transform hook must return str")
        return {"id": request_id, "ok": True, "result": result}
    if kind == "validate":
        validate = getattr(module, "validate", None)
        if validate is None:
            raise ValueError(f"validate hook module {module_name!r} has no validate callable")
        with redirect_stdout(sys.stderr):
            result = validate(
                inputs=_mapping(request.get("inputs"), "inputs"),
                target=Path(_required_str(request, "target")),
                args=_mapping(request.get("args"), "args"),
                helpers=helpers,
            )
        return {"id": request_id, "ok": True, "result": _wire_value(result)}
    raise ValueError(f"unsupported hook request kind: {kind}")


def main() -> int:
    """Run the NDJSON worker loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = ""
        try:
            decoded = json.loads(line)
            if not isinstance(decoded, dict):
                raise ValueError("worker request must be a JSON object")
            request_id = str(decoded.get("id", ""))
            response = handle_request(decoded)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(response, default=str) + "\n")
        sys.stdout.flush()
    return 0


def _required_str(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str):
        raise ValueError(f"worker request field {key!r} must be a string")
    return value


def _mapping(value: object, field: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"worker request field {field!r} must be an object")
    return dict(value)


def _wire_value(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
