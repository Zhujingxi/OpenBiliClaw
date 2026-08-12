"""FastAPI factory with security/resource limits and typed router injection."""

from __future__ import annotations

import asyncio
import hmac
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from openbiliclaw.application.errors import ApplicationError

from .dependencies import HostDependencies, get_dependencies
from .errors import (
    COMMON_ERROR_RESPONSES,
    application_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from .routers import (
    assistant,
    content,
    events,
    feedback,
    recommendations,
    runtime,
    sources,
    understanding,
)
from .schemas.models import ErrorCode, ErrorDetail, ErrorEnvelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from starlette.types import ASGIApp, Message, Receive


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, dependencies: HostDependencies) -> None:
        super().__init__(app)
        self._dependencies = dependencies
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def _error(self, status: int, code: ErrorCode, message: str) -> JSONResponse:
        envelope = ErrorEnvelope(error=ErrorDetail(code=code, message=message))
        return JSONResponse(status_code=status, content=envelope.model_dump(mode="json"))

    def _bounded_receive(
        self, receive: Receive, maximum: int, request_too_large: asyncio.Event
    ) -> Receive:
        seen = 0

        async def bounded() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > maximum:
                    # Starlette converts exceptions from receive into a 400 before
                    # BaseHTTPMiddleware can map them. Return disconnect and mark
                    # the request; dispatch translates after downstream returns.
                    request_too_large.set()
                    return {"type": "http.disconnect"}
            return message

        return bounded

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        policy = self._dependencies.security
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > policy.max_body_bytes):
            return self._error(413, ErrorCode.VALIDATION, "request body too large")
        origin = request.headers.get("origin")
        if origin is not None and not policy.origin_allowed(origin):
            return self._error(403, ErrorCode.FORBIDDEN, "origin is not allowed")
        if policy.bearer_token is not None:
            expected = f"Bearer {policy.bearer_token}"
            if not hmac.compare_digest(request.headers.get("authorization", ""), expected):
                return self._error(401, ErrorCode.UNAUTHORIZED, "authentication required")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            device = request.headers.get("x-device-id")
            csrf = request.headers.get("x-csrf-token")
            if not device or not csrf or not hmac.compare_digest(device, csrf):
                return self._error(
                    403, ErrorCode.FORBIDDEN, "valid device and CSRF headers required"
                )
        client = request.client.host if request.client is not None else "local"
        now = time.monotonic()
        bucket = self._requests[client]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if not bucket:
            # Bound memory to clients active in the current rate-limit window.
            for address, stale in tuple(self._requests.items()):
                if address != client and (not stale or now - stale[-1] >= 60):
                    self._requests.pop(address, None)
        if len(bucket) >= policy.requests_per_minute:
            return self._error(429, ErrorCode.RATE_LIMIT, "rate limit exceeded")
        bucket.append(now)
        original_receive = request._receive
        request_too_large = asyncio.Event()
        request._receive = self._bounded_receive(
            original_receive, policy.max_body_bytes, request_too_large
        )
        try:
            async with asyncio.timeout(policy.request_timeout_seconds):
                result = await call_next(request)
            if request_too_large.is_set():
                return self._error(413, ErrorCode.VALIDATION, "request body too large")
            return result
        except TimeoutError:
            return self._error(503, ErrorCode.TEMPORARY_FAILURE, "request timed out")
        except ApplicationError:
            raise
        except Exception:
            return self._error(500, ErrorCode.TEMPORARY_FAILURE, "temporary failure")


def create_app(dependencies: HostDependencies, *, frontend_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        if dependencies.lifespan is not None:
            await dependencies.lifespan.start()
        try:
            yield
        finally:
            if dependencies.lifespan is not None:
                await dependencies.lifespan.stop()

    app = FastAPI(
        title="OpenBiliClaw API",
        version="1.0.0",
        openapi_url="/v1/openapi.json",
        responses=COMMON_ERROR_RESPONSES,
        lifespan=lifespan,
    )
    app.dependency_overrides[get_dependencies] = lambda: dependencies
    app.add_middleware(SecurityMiddleware, dependencies=dependencies)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(dependencies.security.allowed_origins),
        allow_origin_regex=r"^(chrome|moz)-extension://[A-Za-z0-9_-]+$",
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Device-ID"],
    )
    for item in (
        sources.router,
        recommendations.router,
        understanding.router,
        assistant.router,
        content.router,
        feedback.router,
        runtime.router,
        events.router,
    ):
        app.include_router(item, prefix="/v1")

    async def app_error(request: Request, exc: Exception) -> Response:
        return (
            await application_error_handler(request, exc)
            if isinstance(exc, ApplicationError)
            else await unexpected_error_handler(request, exc)
        )

    async def validation_error(request: Request, exc: Exception) -> Response:
        return (
            await validation_error_handler(request, exc)
            if isinstance(exc, RequestValidationError)
            else await unexpected_error_handler(request, exc)
        )

    async def http_error(request: Request, exc: Exception) -> Response:
        return (
            await http_error_handler(request, exc)
            if isinstance(exc, HTTPException)
            else await unexpected_error_handler(request, exc)
        )

    app.add_exception_handler(ApplicationError, app_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unexpected_error_handler)

    frontend = frontend_dir
    if frontend is None:
        candidates = (
            Path(__file__).resolve().parents[2] / "frontend",
            Path.cwd() / "frontend/apps/web/dist",
        )
        frontend = next((candidate for candidate in candidates if candidate.is_dir()), None)

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> Response:
        del exc
        if (
            frontend is not None
            and request.method == "GET"
            and not request.url.path.startswith("/v1/")
        ):
            relative = request.url.path.lstrip("/")
            asset = frontend / relative
            if relative and asset.is_file() and frontend in asset.resolve().parents:
                return FileResponse(asset)
            index = frontend / "index.html"
            if index.is_file():
                return FileResponse(index)
        envelope = ErrorEnvelope(
            error=ErrorDetail(code=ErrorCode.NOT_FOUND, message="route not found")
        )
        return JSONResponse(status_code=404, content=envelope.model_dump(mode="json"))

    @app.exception_handler(405)
    async def method_not_allowed(request: Request, exc: Exception) -> JSONResponse:
        del request, exc
        envelope = ErrorEnvelope(
            error=ErrorDetail(code=ErrorCode.METHOD_NOT_ALLOWED, message="method not allowed")
        )
        return JSONResponse(status_code=405, content=envelope.model_dump(mode="json"))

    return app
