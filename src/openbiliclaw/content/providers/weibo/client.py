"""Typed injected Weibo transport boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode

if TYPE_CHECKING:
    from openbiliclaw.access.models import CredentialAccessHandle

from .models import WeiboItem, WeiboPage


class WeiboTransport(Protocol):
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes: ...
    async def fetch(self, content_id: str, credential: str | None) -> bytes: ...


class WeiboClient:
    def __init__(self, transport: WeiboTransport) -> None:
        self._transport = transport

    def __repr__(self) -> str:
        return "WeiboClient(credentials=<opaque>)"

    async def search(
        self, text: str, cursor: str | None, limit: int, access: CredentialAccessHandle | None
    ) -> WeiboPage:
        raw = await self._transport.search(text, cursor, limit, None)
        try:
            return WeiboPage.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc

    async def fetch(self, content_id: str, access: CredentialAccessHandle | None) -> WeiboItem:
        raw = await self._transport.fetch(content_id, None)
        try:
            return WeiboItem.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc
