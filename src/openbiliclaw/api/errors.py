"""Central secret-safe error mapping for the vNext HTTP boundary."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from openbiliclaw.api.dependencies import DependencyUnavailableError
from openbiliclaw.features.profile.service import (
    InvalidProfileDeltaError,
    StaleProfileRevisionError,
)
from openbiliclaw.features.sources.service import (
    AbandonedSourceTaskError,
    CancelledSourceTaskError,
    CredentialShapedPayloadError,
    SourceTaskCompletionConflictError,
    StaleSourceTaskLeaseError,
)
from openbiliclaw.infrastructure.database.repositories import ProfileRevisionConflict
from openbiliclaw.infrastructure.security.credentials import MissingCredentialKeyError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI, Request

_CONFLICTS: tuple[type[Exception], ...] = (
    ProfileRevisionConflict,
    StaleProfileRevisionError,
    SourceTaskCompletionConflictError,
    StaleSourceTaskLeaseError,
    CancelledSourceTaskError,
    AbandonedSourceTaskError,
)

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    """Stable machine code and safe human-readable summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """The only JSON error shape exposed by the v1 HTTP boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: ErrorDetail


_HTTP_ERROR_CONTRACTS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "request could not be processed"),
    401: ("unauthorized", "bearer authentication required"),
    403: ("forbidden", "access denied"),
    404: ("not_found", "resource not found"),
    405: ("method_not_allowed", "method is not allowed"),
    409: ("conflict", "resource state conflict"),
    422: ("validation_error", "request validation failed"),
    429: ("rate_limited", "request rate limit exceeded"),
    500: ("internal_error", "internal server error"),
    503: ("unavailable", "required service is unavailable"),
}

_DOCUMENTED_ERROR_STATUSES = (401, 403, 404, 405, 409, 422, 429, 500, 503)


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
    )


def _http_contract(status_code: int) -> tuple[str, str]:
    return _HTTP_ERROR_CONTRACTS.get(status_code, ("request_failed", "request failed"))


def _register_handlers(
    app: FastAPI,
    exception_types: tuple[type[Exception], ...],
    handler: Callable[[Request, Exception], Awaitable[JSONResponse]],
) -> None:
    for exception_type in exception_types:
        app.add_exception_handler(exception_type, handler)


def install_error_handlers(app: FastAPI) -> None:
    """Install stable mappings without echoing request data or exception values."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _response(422, "validation_error", "request validation failed")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        code, message = _http_contract(error.status_code)
        response = _response(error.status_code, code, message)
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.exception_handler(LookupError)
    async def missing_error(_request: Request, _error: LookupError) -> JSONResponse:
        return _response(404, "not_found", "resource not found")

    async def conflict_error(_request: Request, _error: Exception) -> JSONResponse:
        return _response(409, "conflict", "resource state conflict")

    async def value_error(_request: Request, _error: Exception) -> JSONResponse:
        return _response(422, "validation_error", "request validation failed")

    async def unavailable_error(_request: Request, _error: Exception) -> JSONResponse:
        return _response(503, "unavailable", "required service is unavailable")

    validation_errors: tuple[type[Exception], ...] = (
        ValueError,
        InvalidProfileDeltaError,
        CredentialShapedPayloadError,
    )
    unavailable_errors: tuple[type[Exception], ...] = (
        DependencyUnavailableError,
        OperationalError,
        ConnectionError,
        MissingCredentialKeyError,
    )
    _register_handlers(app, _CONFLICTS, conflict_error)
    _register_handlers(app, validation_errors, value_error)
    _register_handlers(app, unavailable_errors, unavailable_error)

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, error: Exception) -> JSONResponse:
        logger.error("unhandled API exception type=%s", type(error).__name__)
        return _response(500, "internal_error", "internal server error")


def register_error_contracts(schema: dict[str, Any]) -> None:
    """Attach the unified envelope without replacing success/auth/SSE metadata."""

    components = schema.setdefault("components", {})
    assert isinstance(components, dict)
    schemas = components.setdefault("schemas", {})
    assert isinstance(schemas, dict)
    envelope_schema = ErrorEnvelope.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = envelope_schema.pop("$defs", {})
    if isinstance(definitions, dict):
        schemas.update(definitions)
    schemas[ErrorEnvelope.__name__] = envelope_schema

    paths = schema.get("paths", {})
    assert isinstance(paths, dict)
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            responses = operation.setdefault("responses", {})
            assert isinstance(responses, dict)
            for status_code in _DOCUMENTED_ERROR_STATUSES:
                code, message = _http_contract(status_code)
                responses[str(status_code)] = {
                    "description": message,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                        }
                    },
                    "x-error-code": code,
                }


__all__ = [
    "ErrorDetail",
    "ErrorEnvelope",
    "install_error_handlers",
    "register_error_contracts",
]
