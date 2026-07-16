"""Secret-safe health projection for the three LiteLLM aliases."""

from __future__ import annotations

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
    healthy: bool
    reason: str | None = None


class AIHealthResult(BaseModel):
    """LiteLLM reachability plus stable alias statuses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proxy_reachable: bool
    aliases: tuple[AliasHealth, ...]


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
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10,
    ) -> None:
        self._api_key = SecretStr(api_key)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, trust_env=False
        )

    async def check_aliases(self) -> AIHealthResult:
        """Return one redacted result per alias in stable application order."""

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
                response.raise_for_status()
                payload = TypeAdapter(_ProxyHealthResponse).validate_python(response.json())
                healthy = payload.healthy_count > 0 and payload.unhealthy_count == 0
                statuses.append(
                    AliasHealth(
                        alias=alias,
                        healthy=healthy,
                        reason=None if healthy else "alias_unavailable",
                    )
                )
            except (httpx.HTTPError, ValueError):
                statuses.append(AliasHealth(alias=alias, healthy=False, reason="proxy_unavailable"))
        return AIHealthResult(proxy_reachable=reached_proxy, aliases=tuple(statuses))

    async def aclose(self) -> None:
        """Close the HTTP client only when this service created it."""

        if self._owns_client:
            await self._client.aclose()
