"""Thin FastAPI composition root for the authoritative vNext API."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from openbiliclaw import __version__
from openbiliclaw.api.dependencies import ApplicationContainer, build_application_container
from openbiliclaw.api.errors import install_error_handlers
from openbiliclaw.api.routers import ROUTERS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def create_app(*, container: ApplicationContainer | None = None) -> FastAPI:
    """Construct dependencies, middleware, v1 routers, lifecycle, and static web."""

    resolved = container or build_application_container()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await resolved.startup()
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
    _mount_static_web(app)
    return app


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
