"""Central safe host error translation and OpenAPI error responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from openbiliclaw.application.errors import ApplicationError, ApplicationErrorCode

from .schemas.models import ErrorCode, ErrorDetail, ErrorEnvelope

if TYPE_CHECKING:
    from fastapi import HTTPException, Request
    from fastapi.exceptions import RequestValidationError

_STATUS: dict[ApplicationErrorCode, tuple[int, ErrorCode]] = {
    ApplicationErrorCode.UNAUTHORIZED: (401, ErrorCode.UNAUTHORIZED),
    ApplicationErrorCode.FORBIDDEN: (403, ErrorCode.FORBIDDEN),
    ApplicationErrorCode.NOT_FOUND: (404, ErrorCode.NOT_FOUND),
    ApplicationErrorCode.EXPIRED: (409, ErrorCode.CONFLICT),
    ApplicationErrorCode.CONFLICT: (409, ErrorCode.CONFLICT),
    ApplicationErrorCode.UNAVAILABLE: (503, ErrorCode.UNAVAILABLE),
}

COMMON_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {
        "model": ErrorEnvelope,
        "description": (
            "unavailable capability or temporary timeout" if status == 503 else code.value
        ),
    }
    for status, code in (
        (401, ErrorCode.UNAUTHORIZED),
        (403, ErrorCode.FORBIDDEN),
        (404, ErrorCode.NOT_FOUND),
        (405, ErrorCode.METHOD_NOT_ALLOWED),
        (409, ErrorCode.CONFLICT),
        (413, ErrorCode.VALIDATION),
        (422, ErrorCode.VALIDATION),
        (429, ErrorCode.RATE_LIMIT),
        (500, ErrorCode.TEMPORARY_FAILURE),
        (503, ErrorCode.UNAVAILABLE),
    )
}


def response(status: int, code: ErrorCode, message: str) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))


async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    del request
    status, code = _STATUS[exc.code]
    return response(status, code, exc.safe_message)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    del request, exc
    return response(422, ErrorCode.VALIDATION, "request validation failed")


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    del request
    if exc.status_code == 404:
        return response(404, ErrorCode.NOT_FOUND, "route not found")
    if exc.status_code == 405:
        return response(405, ErrorCode.METHOD_NOT_ALLOWED, "method not allowed")
    return response(exc.status_code, ErrorCode.TEMPORARY_FAILURE, "request failed")


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    del request, exc
    return response(500, ErrorCode.TEMPORARY_FAILURE, "temporary failure")
