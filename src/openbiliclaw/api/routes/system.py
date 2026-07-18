"""System liveness / QR-info router (pilot extraction).

Contains ``/api/ping`` and ``/api/qr-info`` — the two endpoints whose only
runtime dependency is a LAN-IP lookup. They are extracted first because
their closure footprint is the smallest of any endpoint family in
``api/app.py``.

Externally visible behavior is unchanged: paths, methods, response bodies,
and content types match the legacy inline handlers exactly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from openbiliclaw.api.dependencies import SystemRouteDeps


def build_system_router(deps: SystemRouteDeps) -> APIRouter:
    """Build the system liveness / QR-info router.

    The router serves:

    - ``GET /api/ping`` — pure liveness probe (no DB, no provider round-trips).
    - ``GET /api/qr-info`` — mobile QR-code helper returning the LAN IP.

    Both handlers are intentionally trivial; ``/api/health`` and richer
    readiness logic remain in ``api/app.py`` for now (Phase 1 follow-up).
    """
    router = APIRouter()

    @router.get("/api/ping")
    async def ping() -> JSONResponse:
        """Pure liveness probe: no DB, no provider round-trips.

        ``/api/health`` is a READINESS endpoint — its embedding probe can
        take seconds when the cache is cold (Ollama model reload), which
        made the extension's connection badge sit on "未连接" after opening
        the panel. UI liveness indicators should hit this instead and keep
        ``/api/health`` for profile/embedding state.
        """
        return JSONResponse({"status": "ok", "service": "openbiliclaw-api"})

    @router.get("/api/qr-info")
    async def qr_info() -> JSONResponse:
        """Lightweight endpoint for mobile QR code: LAN IP only.

        Unlike ``/api/health``, this skips the embedding readiness probe
        so the QR drawer never blocks on a cold Ollama model load.
        """
        return JSONResponse({"lan_ip": deps.get_lan_ip()})

    return router


__all__ = ["build_system_router"]
