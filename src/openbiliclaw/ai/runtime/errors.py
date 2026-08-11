"""Safe normalized failures for the model-execution boundary."""

from __future__ import annotations

import asyncio
from enum import StrEnum

from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded


class ErrorCode(StrEnum):
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    TIMEOUT = "timeout"
    INVALID_OUTPUT = "invalid_output"
    BUDGET_EXHAUSTED = "budget_exhausted"


class AIRuntimeError(RuntimeError):
    """Base safe error carrying no provider response or credential text."""

    code = ErrorCode.UNAVAILABLE
    retryable = False

    def __init__(self, *, model_instance: str) -> None:
        self.model_instance = model_instance
        super().__init__(f"AI run failed: {self.code.value} ({model_instance})")


class UnavailableError(AIRuntimeError):
    code = ErrorCode.UNAVAILABLE
    retryable = True


class RateLimitedError(AIRuntimeError):
    code = ErrorCode.RATE_LIMITED
    retryable = True


class UnauthorizedError(AIRuntimeError):
    code = ErrorCode.UNAUTHORIZED


class RunTimedOutError(AIRuntimeError):
    code = ErrorCode.TIMEOUT
    retryable = True


class InvalidOutputError(AIRuntimeError):
    code = ErrorCode.INVALID_OUTPUT


class BudgetExhaustedError(AIRuntimeError):
    code = ErrorCode.BUDGET_EXHAUSTED


def _chain(exc: BaseException) -> tuple[BaseException, ...]:
    result: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        result.append(current)
        current = current.__cause__ or current.__context__
    return tuple(result)


def normalize_error(exc: BaseException, *, model_instance: str) -> AIRuntimeError:
    """Classify an exception chain without copying unsafe upstream text."""

    chain = _chain(exc)
    if any(isinstance(item, UsageLimitExceeded) for item in chain):
        return BudgetExhaustedError(model_instance=model_instance)
    if any(isinstance(item, (TimeoutError, asyncio.TimeoutError)) for item in chain):
        return RunTimedOutError(model_instance=model_instance)
    http_errors = tuple(item for item in chain if isinstance(item, ModelHTTPError))
    if any(item.status_code in {401, 403} for item in http_errors):
        return UnauthorizedError(model_instance=model_instance)
    if any(item.status_code == 429 for item in http_errors):
        return RateLimitedError(model_instance=model_instance)
    if any(isinstance(item, UnexpectedModelBehavior) for item in chain):
        return InvalidOutputError(model_instance=model_instance)
    return UnavailableError(model_instance=model_instance)
