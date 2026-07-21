"""Secret-safe health projection for the three LiteLLM aliases."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

from openbiliclaw.infrastructure.ai.spec import (  # noqa: TC001 - Pydantic resolves it
    ModelAlias,
)

ALIASES: tuple[ModelAlias, ...] = (
    "obc-interactive",
    "obc-analysis",
    "obc-embedding",
)


class AliasHealth(BaseModel):
    """Safe public status for one application alias."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alias: ModelAlias
    available: bool
    state: Literal["healthy", "degraded", "unavailable"]
    reason: str | None = None


class AIHealthResult(BaseModel):
    """LiteLLM reachability plus stable alias statuses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proxy_reachable: bool
    aliases: tuple[AliasHealth, ...]
    admin_url: str | None = None


class _ProxyHealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    healthy_count: int = Field(default=0, ge=0)
    unhealthy_count: int = Field(default=0, ge=0)


class AIHealthService:
    """Query LiteLLM-owned health checks without exposing deployment payloads."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        public_admin_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._api_key = SecretStr(api_key)
        self._public_admin_url = _safe_public_admin_url(public_admin_url)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, trust_env=False
        )

    async def check_aliases(self) -> AIHealthResult:
        """Probe aliases and return only stable, redacted failure classifications.

        LiteLLM's ``/health?model=...`` may perform provider calls. Callers should
        treat this method as an explicit diagnostic, not a cheap liveness probe.
        """

        statuses: list[AliasHealth] = []
        reached_proxy = False
        for alias in ALIASES:
            try:
                response = await self._client.get(
                    "/health",
                    params={"model": alias},
                    headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                )
                reached_proxy = True
            except httpx.TransportError:
                statuses.append(_unavailable(alias, "proxy_transport_error"))
                continue

            if response.status_code in (401, 403):
                statuses.append(_unavailable(alias, "proxy_auth_failed"))
                continue
            if response.status_code == 404:
                statuses.append(_unavailable(alias, "alias_not_configured"))
                continue
            if response.status_code >= 500:
                statuses.append(_unavailable(alias, "proxy_server_error"))
                continue
            if response.is_error:
                statuses.append(_unavailable(alias, "proxy_request_rejected"))
                continue
            try:
                payload = TypeAdapter(_ProxyHealthResponse).validate_python(response.json())
            except (ValueError, TypeError):
                statuses.append(_unavailable(alias, "proxy_invalid_response"))
                continue
            if payload.healthy_count > 0:
                degraded = payload.unhealthy_count > 0
                statuses.append(
                    AliasHealth(
                        alias=alias,
                        available=True,
                        state="degraded" if degraded else "healthy",
                        reason="provider_degraded" if degraded else None,
                    )
                )
            elif payload.unhealthy_count > 0:
                statuses.append(_unavailable(alias, "provider_unhealthy"))
            else:
                statuses.append(_unavailable(alias, "alias_not_configured"))
        return AIHealthResult(
            proxy_reachable=reached_proxy,
            aliases=tuple(statuses),
            admin_url=self._public_admin_url,
        )

    @property
    def public_admin_url(self) -> str | None:
        """Return only the explicitly configured browser-safe Admin URL."""

        return self._public_admin_url

    async def aclose(self) -> None:
        """Close the HTTP client only when this service created it."""

        if self._owns_client:
            await self._client.aclose()


def _unavailable(alias: ModelAlias, reason: str) -> AliasHealth:
    return AliasHealth(alias=alias, available=False, state="unavailable", reason=reason)


def _safe_public_admin_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("LiteLLM public Admin URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("LiteLLM public Admin URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("LiteLLM public Admin URL cannot contain a query or fragment")
    return normalized


def public_admin_url_from_environment() -> str | None:
    """Read the explicit browser URL without deriving it from the internal proxy base."""

    return _safe_public_admin_url(os.getenv("OPENBILICLAW_LITELLM_ADMIN_URL"))


__all__ = [
    "AIHealthResult",
    "AIHealthService",
    "ALIASES",
    "AliasHealth",
    "public_admin_url_from_environment",
]
