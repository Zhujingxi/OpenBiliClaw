"""Central secret-safe error mapping for the vNext HTTP boundary."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

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

_CONFLICTS: tuple[type[Exception], ...] = (
    ProfileRevisionConflict,
    StaleProfileRevisionError,
    SourceTaskCompletionConflictError,
    StaleSourceTaskLeaseError,
    CancelledSourceTaskError,
    AbandonedSourceTaskError,
)


def _response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install stable mappings without echoing request data or exception values."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _response(422, "validation_error", "request validation failed")

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        message = str(error.detail) if isinstance(error.detail, str) else "request failed"
        response = _response(error.status_code, "http_error", message)
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
    for conflict_type in _CONFLICTS:
        app.add_exception_handler(conflict_type, conflict_error)
    for validation_type in validation_errors:
        app.add_exception_handler(validation_type, value_error)
    for unavailable_type in unavailable_errors:
        app.add_exception_handler(unavailable_type, unavailable_error)


__all__ = ["install_error_handlers"]
