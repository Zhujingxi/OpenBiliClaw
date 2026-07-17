"""Typed vNext browser and extension authentication routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw import auth_core
from openbiliclaw.api.dependencies import (
    Container,
    DependencyUnavailableError,
    require_access,
)

_REMEMBER_MAX_AGE = 10 * 365 * 24 * 3600


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    password: str = Field(min_length=1, max_length=4096)


class AuthenticatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: bool


class AuthStatusResponse(AuthenticatedResponse):
    enabled: bool
    password_configured: bool
    installer_bearer_configured: bool
    extension_access_enabled: bool
    trust_loopback: bool


class ExtensionTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1, max_length=512)


class ExtensionTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    expires_at: int


router = APIRouter(prefix="/auth", tags=["auth"])


def _access_control(container: Container) -> object | None:
    return getattr(container.settings.get(), "access_control", None)


def _same_origin(request: Request) -> bool:
    origin_value = request.headers.get("Origin")
    if not origin_value:
        return True
    origin = auth_core.parse_origin(origin_value)
    effective = auth_core.effective_scheme_host(
        url_scheme=request.url.scheme,
        host_header=request.headers.get("Host"),
        xf_proto=None,
        xf_host=None,
        peer="",
        trusted_proxies=(),
    )
    return auth_core.same_origin(origin, effective)


def _set_cookie(response: Response, token: str, *, ttl_hours: int, secure: bool) -> None:
    response.set_cookie(
        auth_core.COOKIE_NAME,
        token,
        max_age=ttl_hours * 3600 if ttl_hours > 0 else _REMEMBER_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_cookie(response: Response, *, secure: bool) -> None:
    response.set_cookie(
        auth_core.COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


@router.get("/status", operation_id="v1_auth_status", response_model=AuthStatusResponse)
def auth_status(request: Request, container: Container) -> AuthStatusResponse:
    access_control = _access_control(container)
    authenticated = False
    try:
        container.access.authenticate_request(request, access_control)
        authenticated = True
    except (DependencyUnavailableError, HTTPException):
        pass
    web_enabled = bool(getattr(access_control, "web_password_enabled", True))
    extension_enabled = bool(
        getattr(
            access_control,
            "extension_access_enabled",
            container.access.extension_access_enabled,
        )
    )
    return AuthStatusResponse(
        enabled=(
            container.access.installer_bearer_configured
            or (web_enabled and container.access.password_configured)
            or (
                extension_enabled
                and bool(container.access.session_secret)
                and bool(container.access.extension_access_records)
            )
        ),
        authenticated=authenticated,
        password_configured=container.access.password_configured,
        installer_bearer_configured=container.access.installer_bearer_configured,
        extension_access_enabled=extension_enabled,
        trust_loopback=bool(getattr(access_control, "trust_loopback", False)),
    )


@router.post("/login", operation_id="v1_auth_login", response_model=AuthenticatedResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    container: Container,
) -> AuthenticatedResponse:
    access_control = _access_control(container)
    if auth_core.is_extension_origin(request.headers.get("Origin")) or not _same_origin(request):
        raise HTTPException(status_code=403, detail="login origin is not allowed")
    if not bool(getattr(access_control, "web_password_enabled", True)):
        raise HTTPException(status_code=403, detail="web password authentication is disabled")
    if not container.access.password_configured:
        raise HTTPException(status_code=503, detail="password authentication is unavailable")
    if not container.access.verify_password(payload.password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    ttl_hours = int(
        getattr(access_control, "session_ttl_hours", container.access.session_ttl_hours)
    )
    token = container.access.mint_session(ttl_hours=ttl_hours)
    _set_cookie(
        response,
        token,
        ttl_hours=ttl_hours,
        secure=request.url.scheme == "https",
    )
    return AuthenticatedResponse(authenticated=True)


@router.post(
    "/logout",
    operation_id="v1_auth_logout",
    response_model=AuthenticatedResponse,
    dependencies=[Depends(require_access)],
)
def logout(request: Request, response: Response, container: Container) -> AuthenticatedResponse:
    _clear_cookie(response, secure=request.url.scheme == "https")
    return AuthenticatedResponse(authenticated=False)


@router.post(
    "/extension-token",
    operation_id="v1_auth_extension_token",
    response_model=ExtensionTokenResponse,
)
def extension_token(
    payload: ExtensionTokenRequest,
    request: Request,
    container: Container,
) -> ExtensionTokenResponse:
    if not auth_core.is_extension_origin(request.headers.get("Origin")):
        raise HTTPException(status_code=403, detail="extension origin required")
    access_control = _access_control(container)
    if not bool(getattr(access_control, "extension_access_enabled", True)):
        raise HTTPException(status_code=403, detail="extension access is disabled")
    ttl_hours = int(
        getattr(
            access_control,
            "extension_session_ttl_hours",
            container.access.extension_session_ttl_hours,
        )
    )
    token = container.access.exchange_extension_key(payload.key, ttl_hours=ttl_hours)
    expires_at = auth_core.token_expires_at(token)
    if expires_at is None:
        raise HTTPException(status_code=503, detail="finite extension session required")
    return ExtensionTokenResponse(token=token, expires_at=expires_at)


@router.post(
    "/revoke",
    operation_id="v1_auth_revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_access)],
)
def revoke(container: Container) -> Response:
    container.access.revoke_sessions()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
