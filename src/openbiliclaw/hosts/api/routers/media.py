"""Public-CDN image proxy.

The route is deliberately exempt from bearer authentication: proxied objects are public
CDN images and URLs are constrained by provider-declared host allowlists. Rate limiting,
HTTPS-only validation, response type checks, timeout, and the 10 MiB cap still apply.
It never proxies video/audio and has no server-side cache.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..dependencies import HostDependencies, get_dependencies
from ..errors import response
from ..media_proxy import MediaProxyError
from ..schemas.models import ErrorCode

router = APIRouter(tags=["media"])


@router.get("/media", response_class=Response, response_model=None)
async def media(
    url: str = Query(min_length=1, max_length=4096),
    dependencies: HostDependencies = Depends(get_dependencies),
) -> Response | JSONResponse:
    if dependencies.media_proxy is None:
        return response(404, ErrorCode.NOT_FOUND, "media not found")
    try:
        result = await dependencies.media_proxy.fetch(url)
    except MediaProxyError as exc:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.TEMPORARY_FAILURE
        return response(exc.status_code, code, exc.safe_message)
    return StreamingResponse(
        iter((result.content,)),
        media_type=result.content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            # SVG is active content; never let it execute on the app origin.
            "Content-Security-Policy": "default-src 'none'",
        },
    )
