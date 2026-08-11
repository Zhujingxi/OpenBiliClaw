from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

from openbiliclaw.infrastructure.http.clients import HttpClientFactory
from openbiliclaw.infrastructure.http.policy import HttpPolicy, RetryPolicy


async def test_scoped_client_has_explicit_policy_and_closes() -> None:
    policy = HttpPolicy(
        timeout_seconds=3.0, verify_tls=True, trust_env=False, user_agent="test-agent"
    )
    factory = HttpClientFactory(policy)
    async with factory.client() as client:
        assert not client.is_closed
        assert client.headers["user-agent"] == "test-agent"
        assert factory.open_client_count == 1
    assert client.is_closed
    assert factory.open_client_count == 0


async def test_retry_only_safe_or_idempotent_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, request=request)

    factory = HttpClientFactory(HttpPolicy(retry=RetryPolicy(max_attempts=2, backoff_seconds=0)))
    async with factory.client(transport=httpx.MockTransport(handler)) as client:
        response = await factory.request(client, "GET", "https://example.test")
        assert response.status_code == 200
    assert calls == 2

    calls = 0
    async with factory.client(transport=httpx.MockTransport(handler)) as client:
        response = await factory.request(client, "POST", "https://example.test")
        assert response.status_code == 503
    assert calls == 1

    calls = 0
    async with factory.client(transport=httpx.MockTransport(handler)) as client:
        response = await factory.request(
            client, "POST", "https://example.test", idempotency_key="request-1"
        )
        assert response.status_code == 200
        assert response.request.headers["idempotency-key"] == "request-1"


async def test_factory_close_closes_leaked_clients() -> None:
    factory = HttpClientFactory()
    context = factory.client()
    client = await context.__aenter__()
    await factory.close()
    assert client.is_closed
    assert factory.open_client_count == 0
    await context.__aexit__(None, None, None)
    with pytest.raises(RuntimeError, match="closed"):
        async with factory.client():
            pass


async def test_invalid_idempotency_key_is_rejected() -> None:
    factory = HttpClientFactory()
    async with factory.client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200))
    ) as client:
        with pytest.raises(ValueError, match="idempotency"):
            await factory.request(client, "POST", "https://example.test", idempotency_key=" ")


async def test_transport_error_exhausts_bounded_retries() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    factory = HttpClientFactory(HttpPolicy(retry=RetryPolicy(max_attempts=2, backoff_seconds=0)))
    async with factory.client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.ConnectError):
            await factory.request(client, "GET", "https://example.test")


@pytest.mark.parametrize(
    "policy",
    [
        lambda: RetryPolicy(max_attempts=0),
        lambda: RetryPolicy(backoff_seconds=-1),
        lambda: HttpPolicy(timeout_seconds=0),
        lambda: HttpPolicy(user_agent=" "),
    ],
)
def test_invalid_policy_is_rejected(policy: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        policy()
