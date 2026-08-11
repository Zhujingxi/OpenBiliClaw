"""Scoped httpx client construction and bounded safe retries."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

from .policy import HttpPolicy

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"})


class HttpClientFactory:
    """Own explicitly scoped ``httpx.AsyncClient`` lifetimes."""

    def __init__(self, policy: HttpPolicy | None = None) -> None:
        self._policy = policy or HttpPolicy()
        self._clients: set[httpx.AsyncClient] = set()
        self._closed = False

    @property
    def open_client_count(self) -> int:
        return len(self._clients)

    @asynccontextmanager
    async def client(
        self, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> AsyncIterator[httpx.AsyncClient]:
        """Yield one configured client and always close it."""

        if self._closed:
            raise RuntimeError("HTTP client factory is closed")
        client = httpx.AsyncClient(
            timeout=self._policy.timeout_seconds,
            verify=self._policy.verify_tls,
            trust_env=self._policy.trust_env,
            proxy=self._policy.proxy,
            headers={"user-agent": self._policy.user_agent},
            transport=transport,
        )
        self._clients.add(client)
        try:
            yield client
        finally:
            await client.aclose()
            self._clients.discard(client)

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        idempotency_key: str | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        """Request with retries restricted to safe or explicitly idempotent operations."""

        normalized = method.upper()
        if idempotency_key is not None and (
            not idempotency_key.strip() or len(idempotency_key) > 256
        ):
            raise ValueError("idempotency key must contain 1 to 256 characters")
        retryable = normalized in _SAFE_METHODS or idempotency_key is not None
        attempts = self._policy.retry.max_attempts if retryable else 1
        request_headers = dict(headers or {})
        if idempotency_key is not None:
            request_headers["idempotency-key"] = idempotency_key
        for attempt in range(1, attempts + 1):
            try:
                response = await client.request(
                    normalized, url, headers=request_headers, content=content
                )
            except httpx.TransportError:
                if attempt == attempts:
                    raise
            else:
                if (
                    response.status_code not in self._policy.retry.retry_statuses
                    or attempt == attempts
                ):
                    return response
                await response.aclose()
            if self._policy.retry.backoff_seconds:
                await asyncio.sleep(self._policy.retry.backoff_seconds * attempt)
        raise RuntimeError("unreachable retry state")

    async def close(self) -> None:
        """Close clients whose scopes have not exited yet."""

        self._closed = True
        clients = tuple(self._clients)
        self._clients.clear()
        await asyncio.gather(*(client.aclose() for client in clients))
