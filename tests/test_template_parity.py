"""Parity tests for in-process and worker template renderers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

import pytest

from untaped_recipe.domain.templates import render_template
from untaped_recipe.hook_worker import HookHelpers as WorkerHookHelpers

Renderer = Callable[[str, Mapping[str, object]], str]


def _domain_render(
    template: str,
    inputs: Mapping[str, object],
    *,
    unknown_tokens: str = "error",
) -> str:
    return render_template(template, inputs, unknown_tokens=unknown_tokens)


def _worker_render(
    template: str,
    inputs: Mapping[str, object],
    *,
    unknown_tokens: str = "error",
) -> str:
    return WorkerHookHelpers().render_template(
        template,
        dict(inputs),
        unknown_tokens=unknown_tokens,
    )


@pytest.mark.parametrize("renderer", [_domain_render, _worker_render])
def test_template_renderers_replace_defined_inputs_in_both_modes(
    renderer: Callable[..., str],
) -> None:
    assert renderer("owner={{ owner }}", {"owner": "platform"}) == "owner=platform"
    assert (
        renderer("owner={{ owner }}", {"owner": "platform"}, unknown_tokens="keep")
        == "owner=platform"
    )


@pytest.mark.parametrize("renderer", [_domain_render, _worker_render])
def test_template_renderers_handle_unknown_bare_names(
    renderer: Callable[..., str],
) -> None:
    with pytest.raises(ValueError, match="template input 'owner' is not defined"):
        renderer("owner={{ owner }}", {})

    assert renderer("owner={{ owner }}", {}, unknown_tokens="keep") == "owner={{ owner }}"


@pytest.mark.parametrize("renderer", [_domain_render, _worker_render])
def test_template_renderers_reject_structured_values(
    renderer: Callable[..., str],
) -> None:
    with pytest.raises(
        ValueError,
        match="structured input 'cols' cannot be rendered; hooks receive it natively",
    ):
        renderer("cols={{ cols }}", {"cols": ["a", "b"]})


@pytest.mark.parametrize(
    "template",
    [
        "${{ github.ref }}",
        "{{ .Values.x }}",
    ],
)
@pytest.mark.parametrize("renderer", [_domain_render, _worker_render])
def test_template_renderers_reject_non_bare_tokens_by_default(
    renderer: Callable[..., str],
    template: str,
) -> None:
    token = "{{ github.ref }}" if "github" in template else "{{ .Values.x }}"
    message = (
        f"template token {token!r} is not a bare input name; "
        "set unknown_tokens: keep to pass it through"
    )
    with pytest.raises(ValueError, match=re.escape(message)):
        renderer(template, {})

    assert renderer(template, {}, unknown_tokens="keep") == template


@pytest.mark.parametrize("renderer", [_domain_render, _worker_render])
def test_template_renderers_reject_invalid_unknown_token_mode(
    renderer: Callable[..., str],
) -> None:
    with pytest.raises(ValueError, match="unknown_tokens"):
        renderer("{{ owner }}", {"owner": "platform"}, unknown_tokens="passthrough")
