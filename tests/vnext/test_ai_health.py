"""Contract tests for LiteLLM proxy and stable-alias health reporting."""

from __future__ import annotations

import httpx
import pytest

from openbiliclaw.infrastructure.ai.health import AIHealthService


async def _check(handler: httpx.AsyncBaseTransport):
    async with httpx.AsyncClient(transport=handler, base_url="http://litellm.test") as client:
        return await AIHealthService(
            base_url="http://litellm.test",
            api_key="synthetic-proxy-key",
            client=client,
        ).check_aliases()


async def test_mixed_deployments_are_available_but_explicitly_degraded() -> None:
    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        alias = request.url.params["model"]
        requested.append(alias)
        return httpx.Response(
            200,
            json={
                "healthy_endpoints": [{"model": "provider/private-deployment"}],
                "unhealthy_endpoints": [
                    {"model": "provider/other-private-deployment", "error": "credential detail"}
                ],
                "healthy_count": 1,
                "unhealthy_count": 1,
            },
        )

    result = await _check(httpx.MockTransport(handle))

    assert requested == ["obc-interactive", "obc-analysis", "obc-embedding"]
    assert result.proxy_reachable is True
    assert all(item.available is True for item in result.aliases)
    assert all(item.state == "degraded" for item in result.aliases)
    assert all(item.reason == "provider_degraded" for item in result.aliases)
    assert "private-deployment" not in repr(result)
    assert "credential detail" not in repr(result)


async def test_one_healthy_deployment_makes_alias_available() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"healthy_count": 1, "unhealthy_count": 0},
        )

    result = await _check(httpx.MockTransport(handle))

    assert all(item.available is True for item in result.aliases)
    assert all(item.state == "healthy" for item in result.aliases)
    assert all(item.reason is None for item in result.aliases)


@pytest.mark.parametrize(
    ("status_code", "reason"),
    [
        (401, "proxy_auth_failed"),
        (404, "alias_not_configured"),
        (500, "proxy_server_error"),
    ],
)
async def test_health_distinguishes_http_failure_classes(status_code: int, reason: str) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"detail": "private diagnostic"})

    result = await _check(httpx.MockTransport(handle))

    assert result.proxy_reachable is True
    assert all(item.available is False for item in result.aliases)
    assert all(item.state == "unavailable" for item in result.aliases)
    assert all(item.reason == reason for item in result.aliases)
    assert "private diagnostic" not in repr(result)


async def test_health_distinguishes_provider_unhealthy() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"healthy_count": 0, "unhealthy_count": 2},
        )

    result = await _check(httpx.MockTransport(handle))

    assert result.proxy_reachable is True
    assert all(item.available is False for item in result.aliases)
    assert all(item.reason == "provider_unhealthy" for item in result.aliases)


async def test_health_maps_transport_failure_separately_without_leaking_details() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-bearing transport diagnostic", request=request)

    result = await _check(httpx.MockTransport(handle))

    assert result.proxy_reachable is False
    assert all(item.available is False for item in result.aliases)
    assert all(item.reason == "proxy_transport_error" for item in result.aliases)
    assert "secret-bearing" not in repr(result)
