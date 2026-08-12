"""Bounded Bilibili HTTP client with strict response validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.infrastructure.http.clients import HttpClientFactory

from .models import (
    ITEM_ADAPTER,
    BilibiliActionData,
    BilibiliItem,
    BilibiliNavData,
    BilibiliPageData,
    BilibiliResponse,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.access.models import CredentialAccessHandle


class BilibiliTransport(Protocol):
    async def __call__(
        self,
        method: str,
        path: str,
        query: str,
        cookie: str | None,
        body: bytes,
    ) -> bytes: ...


class CredentialResolver(Protocol):
    async def __call__(self, handle: CredentialAccessHandle) -> str: ...


def cookie_parts(cookie: str) -> tuple[str | None, str | None]:
    values: dict[str, str] = {}
    for segment in cookie.split(";"):
        name, separator, value = segment.strip().partition("=")
        if separator and name in {"SESSDATA", "bili_jct"}:
            values[name] = value.strip()
    return values.get("SESSDATA"), values.get("bili_jct")


class HttpxBilibiliTransport:
    """Real network boundary; tests inject MockTransport."""

    _BASE = "https://api.bilibili.com"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._factory = HttpClientFactory()
        self._transport = transport

    @property
    def open_client_count(self) -> int:
        return self._factory.open_client_count

    async def __call__(
        self, method: str, path: str, query: str, cookie: str | None, body: bytes
    ) -> bytes:
        headers = {"referer": "https://www.bilibili.com"}
        if cookie is not None:
            headers["cookie"] = cookie
        if body:
            headers["content-type"] = "application/x-www-form-urlencoded"
        url = f"{self._BASE}{path}" + (f"?{query}" if query else "")
        async with self._factory.client(transport=self._transport) as client:
            try:
                response = await self._factory.request(
                    client,
                    method,
                    url,
                    headers=headers,
                    content=body or None,
                )
            except httpx.TransportError as exc:
                raise ContentIntegrationError(
                    IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider request failed"
                ) from exc
            if response.status_code in {412, 429}:
                raise ContentIntegrationError(
                    IntegrationErrorCode.RATE_LIMITED, "provider rate limited request"
                )
            if response.status_code >= 400:
                raise ContentIntegrationError(
                    IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider request failed"
                )
            # Validate the envelope immediately, before response bytes cross the boundary.
            try:
                BilibiliResponse.model_validate_json(response.content)
            except ValidationError as exc:
                raise ContentIntegrationError(
                    IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
                ) from exc
            return response.content


class BilibiliClient:
    """Typed endpoint client; contains no provider-independent policy."""

    def __init__(self, transport: BilibiliTransport, resolver: CredentialResolver) -> None:
        self._transport = transport
        self._resolver = resolver

    def __repr__(self) -> str:
        return "BilibiliClient(credentials=<opaque>)"

    async def page(
        self,
        path: str,
        params: Mapping[str, str | int],
        access: CredentialAccessHandle | None = None,
    ) -> BilibiliPageData:
        payload = await self._request("GET", path, params, access=access)
        data = payload.data
        if not isinstance(data, BilibiliPageData):
            raise self._invalid()
        return data

    async def item(
        self,
        path: str,
        params: Mapping[str, str | int],
        access: CredentialAccessHandle | None = None,
    ) -> BilibiliItem:
        payload = await self._request("GET", path, params, access=access)
        data = payload.data
        try:
            return ITEM_ADAPTER.validate_python(data)
        except ValidationError as exc:
            raise self._invalid() from exc

    async def nav_with_cookie(self, cookie: str) -> BilibiliNavData:
        payload = await self._request_with_cookie("GET", "/x/web-interface/nav", {}, cookie, b"")
        if not isinstance(payload.data, BilibiliNavData):
            raise self._invalid()
        return payload.data

    async def action(
        self,
        path: str,
        params: Mapping[str, str | int],
        access: CredentialAccessHandle,
        *,
        idempotency_key: str,
    ) -> BilibiliActionData:
        cookie = await self._resolver(access)
        session, csrf = cookie_parts(cookie)
        if not session or not csrf:
            raise ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "login expired")
        data = dict(params)
        data["csrf"] = csrf
        data["csrf_token"] = csrf
        data["idempotency_key"] = idempotency_key
        body = urlencode(data).encode()
        payload = await self._request_with_cookie("POST", path, {}, cookie, body)
        if not isinstance(payload.data, BilibiliActionData):
            raise self._invalid()
        return payload.data

    async def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int],
        *,
        access: CredentialAccessHandle | None,
    ) -> BilibiliResponse:
        cookie = await self._resolver(access) if access is not None else None
        return await self._request_with_cookie(method, path, params, cookie, b"")

    async def _request_with_cookie(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int],
        cookie: str | None,
        body: bytes,
    ) -> BilibiliResponse:
        raw = await self._transport(method, path, urlencode(params), cookie, body)
        try:
            payload = BilibiliResponse.model_validate_json(raw)
        except ValidationError as exc:
            raise self._invalid() from exc
        if payload.code == 0:
            return payload
        if payload.code == -101:
            raise ContentIntegrationError(IntegrationErrorCode.ACCESS_DENIED, "login expired")
        if payload.code in {-412, -429}:
            raise ContentIntegrationError(
                IntegrationErrorCode.RATE_LIMITED, "provider rate limited"
            )
        raise ContentIntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider request failed"
        )

    @staticmethod
    def _invalid() -> ContentIntegrationError:
        return ContentIntegrationError(
            IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
        )
