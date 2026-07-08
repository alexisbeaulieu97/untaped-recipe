"""Pure models for golden-fixture recipe test cases."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VerdictStatus = Literal["pass", "fail", "skip"]


class VerdictExpectation(BaseModel):
    """Expected validate-verdict outcome for one case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: VerdictStatus | None = None
    message_contains: str | None = None

    @model_validator(mode="after")
    def _require_assertion(self) -> VerdictExpectation:
        if self.status is None and self.message_contains is None:
            raise ValueError("verdict must declare status or message_contains")
        return self


class CaseSpec(BaseModel):
    """Parsed case.yml contents; every field is optional in the file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inputs: dict[str, object] = Field(default_factory=dict)
    expect: Literal["success", "error"] = "success"
    error_contains: str | None = None
    verdict: VerdictExpectation | None = None

    @model_validator(mode="after")
    def _validate_error_contract(self) -> CaseSpec:
        # verdict + expect: error is rejected because whether validate
        # verdicts exist under an expected error depends on where planning
        # fails relative to validate steps — a fragile contract.
        if self.expect == "error" and not self.error_contains:
            raise ValueError("expect: error requires error_contains")
        if self.expect == "error" and self.verdict is not None:
            raise ValueError("verdict is not valid with expect: error")
        if self.expect == "success" and self.error_contains is not None:
            raise ValueError("error_contains is only valid with expect: error")
        return self
