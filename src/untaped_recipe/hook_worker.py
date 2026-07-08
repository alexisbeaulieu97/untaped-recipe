"""Stdlib-only NDJSON worker for uv-managed external hook projects."""

from __future__ import annotations

import importlib
import json
import re
import sys
import traceback
from collections.abc import Mapping
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

if __package__:
    from untaped_recipe import worker_protocol as protocol
else:  # pragma: no cover - used when executed as a script in a hook env.
    import worker_protocol as protocol  # type: ignore[import-not-found,no-redef]

if __package__:
    from untaped_recipe.yaml_options import apply_yaml_dump_options
else:  # pragma: no cover - used when executed as a script in a hook env.
    from yaml_options import apply_yaml_dump_options  # type: ignore[import-not-found,no-redef]

_TOKEN_RE = re.compile(r"{{.*?}}")
_BARE_TOKEN_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_UNKNOWN_TOKEN_MODES = {"error", "keep"}


class HookHelpers:
    """Minimal helpers available inside external hook workers.

    A fresh instance is built per request so ``warn`` accumulates warnings
    for exactly one hook invocation.
    """

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def pass_(self, message: str = "") -> dict[str, str]:
        """Return a passing validation verdict."""
        return {"status": "pass", "message": message}

    def fail(self, message: str) -> dict[str, str]:
        """Return a failing validation verdict."""
        return {"status": "fail", "message": message}

    def skip(self, message: str = "") -> dict[str, str]:
        """Return a skip verdict marking the target not applicable."""
        return {"status": "skip", "message": message}

    def warn(self, message: str) -> None:
        """Accumulate a non-fatal warning for the current target."""
        self._warnings.append(str(message))

    @property
    def warnings(self) -> list[str]:
        """Warnings accumulated during this invocation."""
        return list(self._warnings)

    def render_template(
        self,
        template: str,
        inputs: dict[str, object],
        *,
        unknown_tokens: str = "error",
    ) -> str:
        """Render simple `{{ input }}` placeholders."""
        if unknown_tokens not in _UNKNOWN_TOKEN_MODES:
            raise ValueError("unknown_tokens must be 'error' or 'keep'")

        def replace(match: re.Match[str]) -> str:
            token = match.group(0)
            bare_token = _BARE_TOKEN_RE.fullmatch(token)
            if bare_token is not None:
                key = bare_token.group(1)
                if key in inputs:
                    value = inputs[key]
                    if isinstance(value, Mapping | list | tuple):
                        raise ValueError(
                            f"structured input {key!r} cannot be rendered; "
                            "hooks receive it natively"
                        )
                    return str(value)
                if unknown_tokens == "keep":
                    return token
                raise ValueError(f"template input {key!r} is not defined")
            if unknown_tokens == "keep":
                return token
            raise ValueError(
                f"template token {token!r} is not a bare input name; "
                "set unknown_tokens: keep to pass it through"
            )

        return _TOKEN_RE.sub(replace, template)

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content if ruamel.yaml is installed in the hook project."""
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        yaml.preserve_quotes = True
        return yaml.load(content)

    def dump_yaml(self, data: object, *, options: Mapping[str, object] | None = None) -> str:
        """Round-trip-dump YAML content if ruamel.yaml is installed in the hook project."""
        from ruamel.yaml import YAML  # noqa: PLC0415

        yaml = YAML()
        apply_yaml_dump_options(yaml, options)
        out = StringIO()
        yaml.dump(data, out)
        return out.getvalue()


if TYPE_CHECKING:
    from untaped_recipe.hook_api import HookHelpers as ExternalHookHelpers

    _external_helper_contract: ExternalHookHelpers = HookHelpers()


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one decoded worker request."""
    request_id = _required_str(request, protocol.ID)
    kind = _required_str(request, protocol.KIND)
    module_name = _required_str(request, protocol.MODULE)
    with redirect_stdout(sys.stderr):
        module = importlib.import_module(module_name)
    helpers = HookHelpers()
    if kind == protocol.TRANSFORM:
        transform = getattr(module, "transform", None)
        if transform is None:
            raise ValueError(f"transform hook module {module_name!r} has no transform callable")
        with redirect_stdout(sys.stderr):
            result = transform(
                _required_str(request, protocol.CONTENT),
                inputs=_mapping(request.get(protocol.INPUTS), protocol.INPUTS),
                target=Path(_required_str(request, protocol.TARGET)),
                file=Path(_required_str(request, protocol.FILE)),
                args=_mapping(request.get(protocol.ARGS), protocol.ARGS),
                helpers=helpers,
            )
        if not isinstance(result, str):
            raise ValueError("transform hook must return str")
        return {
            protocol.ID: request_id,
            protocol.OK: True,
            protocol.RESULT: result,
            protocol.WARNINGS: helpers.warnings,
        }
    if kind == protocol.VALIDATE:
        validate = getattr(module, "validate", None)
        if validate is None:
            raise ValueError(f"validate hook module {module_name!r} has no validate callable")
        with redirect_stdout(sys.stderr):
            result = validate(
                inputs=_mapping(request.get(protocol.INPUTS), protocol.INPUTS),
                target=Path(_required_str(request, protocol.TARGET)),
                args=_mapping(request.get(protocol.ARGS), protocol.ARGS),
                helpers=helpers,
            )
        return {
            protocol.ID: request_id,
            protocol.OK: True,
            protocol.RESULT: _wire_value(result),
            protocol.WARNINGS: helpers.warnings,
        }
    raise ValueError(f"unsupported hook request kind: {kind}")


def main() -> int:
    """Run the NDJSON worker loop."""
    _configure_standard_streams()
    # The ready line ends the client's startup wait: everything before it
    # (uv env creation on first use, module imports) is environment setup,
    # charged against the startup bound rather than the per-hook timeout.
    sys.stdout.write(json.dumps({protocol.READY: True}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = ""
        try:
            decoded = json.loads(line)
            if not isinstance(decoded, dict):
                raise ValueError("worker request must be a JSON object")
            request_id = str(decoded.get(protocol.ID, ""))
            response = handle_request(decoded)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {
                protocol.ID: request_id,
                protocol.OK: False,
                protocol.ERROR: f"{type(exc).__name__}: {exc}",
            }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


def _configure_standard_streams() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


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
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if not isinstance(dumped, Mapping):
            raise ValueError(f"invalid validate verdict: {value!r}")
        return _json_safe_mapping(dumped)
    raise ValueError(f"invalid validate verdict: {value!r}")


def _json_safe_mapping(value: Mapping[object, object]) -> dict[str, object]:
    result = {str(key): item for key, item in value.items()}
    try:
        json.dumps(result)
    except TypeError as exc:
        raise ValueError(f"invalid validate verdict: {exc}") from exc
    return result


if __name__ == "__main__":
    raise SystemExit(main())
