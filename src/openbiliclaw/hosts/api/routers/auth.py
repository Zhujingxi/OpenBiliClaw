"""Password login for the single local user."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel
from openbiliclaw.hosts.api.auth import verify_password

from ..dependencies import HostDependencies, get_dependencies
from ..errors import response
from ..schemas.models import ErrorCode

router = APIRouter(prefix="/auth", tags=["auth"])
_failures: dict[str, deque[float]] = defaultdict(deque)
_failure_lock = asyncio.Lock()
_MAX_FAILURES = 5
_FAILURE_WINDOW_SECONDS = 60


class LoginRequest(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: str
    label: Literal["session"] = "session"


async def _record_failure(client: str, *, succeeded: bool) -> bool:
    """Record one attempt; return False when the brute-force bound is exceeded.

    Per-process bound; shared storage if multi-process is added. Idle buckets linger
    (ponytail: bounded by distinct client IPs on a loopback host).
    """

    now = time.monotonic()
    async with _failure_lock:
        bucket = _failures[client]
        while bucket and now - bucket[0] >= _FAILURE_WINDOW_SECONDS:
            bucket.popleft()
        if succeeded:
            _failures.pop(client, None)
            return True
        if len(bucket) >= _MAX_FAILURES:
            return False
        bucket.append(now)
        return True


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    dependencies: HostDependencies = Depends(get_dependencies),
) -> LoginResponse | JSONResponse:
    password_hash = dependencies.security.password_hash
    if password_hash is None or dependencies.auth_tokens is None:
        return response(503, ErrorCode.UNAVAILABLE, "password login not configured")
    client = request.client.host if request.client is not None else "local"
    valid = verify_password(body.password, password_hash)
    if not await _record_failure(client, succeeded=valid):
        return response(429, ErrorCode.RATE_LIMIT, "too many login attempts")
    if not valid:
        return response(401, ErrorCode.UNAUTHORIZED, "invalid password")
    minted = await dependencies.auth_tokens.mint("session")
    return LoginResponse(token=minted.token)
