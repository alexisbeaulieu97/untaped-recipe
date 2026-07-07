"""Tests for golden-case spec models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped_recipe.domain.testcase import CaseSpec, VerdictExpectation


def test_case_spec_defaults_to_success_with_no_inputs() -> None:
    spec = CaseSpec()

    assert spec.expect == "success"
    assert spec.inputs == {}
    assert spec.error_contains is None
    assert spec.verdict is None


def test_expect_error_requires_error_contains() -> None:
    with pytest.raises(ValidationError, match="expect: error requires error_contains"):
        CaseSpec(expect="error")


def test_error_contains_is_forbidden_on_success_cases() -> None:
    with pytest.raises(ValidationError, match="error_contains is only valid"):
        CaseSpec(error_contains="boom")


def test_verdict_is_forbidden_on_expect_error_cases() -> None:
    with pytest.raises(ValidationError, match="verdict is not valid with expect: error"):
        CaseSpec.model_validate(
            {
                "expect": "error",
                "error_contains": "boom",
                "verdict": {"status": "pass"},
            }
        )


def test_verdict_expectation_requires_an_assertion() -> None:
    with pytest.raises(ValidationError, match="status or message_contains"):
        VerdictExpectation()


def test_unknown_case_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseSpec.model_validate({"targets": ["src/playbook.yml"]})
