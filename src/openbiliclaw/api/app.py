"""Thin FastAPI composition root for the authoritative vNext API."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from openbiliclaw import __version__
from openbiliclaw.api.dependencies import ApplicationContainer, build_application_container
from openbiliclaw.api.errors import install_error_handlers
from openbiliclaw.api.routers import ROUTERS
from openbiliclaw.api.v1_models import SSE_COMPONENT_MODELS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

logger = logging.getLogger(__name__)


def create_app(*, container: ApplicationContainer | None = None) -> FastAPI:
    """Construct dependencies, middleware, v1 routers, lifecycle, and static web."""

    resolved = container or build_application_container()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await resolved.startup()
        except BaseException:
            try:
                await resolved.shutdown()
            except BaseException as cleanup_error:
                logger.warning(
                    "application cleanup failed after startup error: %s",
                    type(cleanup_error).__name__,
                )
            raise
        try:
            yield
        finally:
            await resolved.shutdown()

    app = FastAPI(
        title="OpenBiliClaw API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.container = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"^(chrome-extension://[^/]+|moz-extension://[^/]+|http://(127\.0\.0\.1|localhost)(:\d+)?)$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router, prefix="/api/v1")
    app.openapi = _openapi_factory(app)  # type: ignore[method-assign]
    _mount_static_web(app)
    return app


def _openapi_factory(app: FastAPI) -> Callable[[], dict[str, Any]]:
    """Build the client-generation schema, including conditional auth and SSE metadata."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
        )
        _register_sse_components(schema)
        _finalize_operations(schema)
        app.openapi_schema = schema
        return schema

    return openapi


def _register_sse_components(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {})
    assert isinstance(components, dict)
    schemas = components.setdefault("schemas", {})
    assert isinstance(schemas, dict)
    for model in SSE_COMPONENT_MODELS:
        model_schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        definitions = model_schema.pop("$defs", {})
        if isinstance(definitions, dict):
            schemas.update(definitions)
        schemas[model.__name__] = model_schema


def _finalize_operations(schema: dict[str, Any]) -> None:
    paths = schema.get("paths", {})
    assert isinstance(paths, dict)
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if isinstance(operation_id, str) and operation_id.startswith("v1_onboarding_"):
                operation["security"] = [{}, {"BearerAuth": []}]
            responses = operation.get("responses", {})
            if not isinstance(responses, dict):
                continue
            success = responses.get("200")
            if not isinstance(success, dict):
                continue
            content = success.get("content")
            if isinstance(content, dict) and "text/event-stream" in content:
                success["content"] = {"text/event-stream": content["text/event-stream"]}


def _mount_static_web(app: FastAPI) -> None:
    web = Path(__file__).resolve().parent.parent / "web"
    if not web.is_dir():
        return
    setup = web / "setup"
    desktop = web / "desktop"
    if setup.is_dir():
        app.mount("/setup", StaticFiles(directory=setup, html=True), name="setup-web")
    if desktop.is_dir():
        app.mount("/web", StaticFiles(directory=desktop, html=True), name="desktop-web")
    app.mount("/m", StaticFiles(directory=web, html=True), name="mobile-web")


def generate_openapi(path: Path = Path("openapi/openapi.json")) -> Path:
    """Write deterministic OpenAPI JSON for shared-client generation."""

    schema = create_app().openapi()
    payload = json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


app = create_app()


__all__ = ["app", "create_app", "generate_openapi"]
