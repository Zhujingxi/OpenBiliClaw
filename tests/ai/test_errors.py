from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded

from openbiliclaw.ai.runtime.errors import (
    BudgetExhaustedError,
    InvalidOutputError,
    RateLimitedError,
    RunTimedOutError,
    UnauthorizedError,
    UnavailableError,
    normalize_error,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (ModelHTTPError(401, "test", None), UnauthorizedError),
        (ModelHTTPError(429, "test", None), RateLimitedError),
        (ModelHTTPError(503, "test", None), UnavailableError),
        (UsageLimitExceeded("limit"), BudgetExhaustedError),
        (TimeoutError(), RunTimedOutError),
        (UnexpectedModelBehavior("invalid structured output"), InvalidOutputError),
        (ConnectionError("token=top-secret"), UnavailableError),
    ],
)
def test_errors_are_safely_normalized(source: BaseException, expected: type[Exception]) -> None:
    error = normalize_error(source, model_instance="configured-model")
    assert isinstance(error, expected)
    assert "top-secret" not in str(error)
    assert error.model_instance == "configured-model"


def test_nested_causes_are_classified_cycle_safely() -> None:
    inner = ModelHTTPError(429, "test", None)
    outer = RuntimeError("wrapper")
    outer.__cause__ = inner
    assert isinstance(normalize_error(outer, model_instance="m"), RateLimitedError)
