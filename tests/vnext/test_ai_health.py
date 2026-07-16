"""Contract tests for LiteLLM proxy and stable-alias health reporting."""

from __future__ import annotations

import httpx

from openbiliclaw.infrastructure.ai.health import AIHealthService


async def test_health_reports_each_exact_alias_without_exposing_deployment_details() -> None:
    requested: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        alias = request.url.params["model"]
        requested.append(alias)
        if alias == "obc-analysis":
            return httpx.Response(
                200,
                json={
                    "healthy_endpoints": [],
                    "unhealthy_endpoints": [
                        {"model": "provider/private-deployment", "error": "credential detail"}
                    ],
                    "healthy_count": 0,
                    "unhealthy_count": 1,
                },
            )
        return httpx.Response(
            200,
            json={
                "healthy_endpoints": [{"model": "provider/private-deployment"}],
                "unhealthy_endpoints": [],
                "healthy_count": 1,
                "unhealthy_count": 0,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://litellm.test"
    ) as client:
        service = AIHealthService(
            base_url="http://litellm.test",
            api_key="synthetic-proxy-key",
            client=client,
        )
        result = await service.check_aliases()

    assert requested == ["obc-interactive", "obc-analysis", "obc-embedding"]
    assert [(item.alias, item.healthy) for item in result.aliases] == [
        ("obc-interactive", True),
        ("obc-analysis", False),
        ("obc-embedding", True),
    ]
    assert "private-deployment" not in repr(result)
    assert "credential detail" not in repr(result)


async def test_health_maps_proxy_transport_failure_to_safe_unavailable_results() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-bearing transport diagnostic", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://litellm.test"
    ) as client:
        result = await AIHealthService(
            base_url="http://litellm.test",
            api_key="synthetic-proxy-key",
            client=client,
        ).check_aliases()

    assert result.proxy_reachable is False
    assert all(item.healthy is False for item in result.aliases)
    assert all(item.reason == "proxy_unavailable" for item in result.aliases)
    assert "secret-bearing" not in repr(result)
