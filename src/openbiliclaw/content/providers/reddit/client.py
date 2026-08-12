"""Typed injected Reddit transport boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode

if TYPE_CHECKING:
    from openbiliclaw.access.models import CredentialAccessHandle

from .models import RedditItem, RedditPage


class RedditTransport(Protocol):
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes: ...
    async def fetch(self, content_id: str, credential: str | None) -> bytes: ...


class CredentialResolver(Protocol):
    async def __call__(self, handle: CredentialAccessHandle) -> str: ...


class RedditClient:
    def __init__(self, transport: RedditTransport, resolver: CredentialResolver) -> None:
        self._transport, self._resolver = transport, resolver

    def __repr__(self) -> str:
        return "RedditClient(credentials=<opaque>)"

    async def search(
        self, text: str, cursor: str | None, limit: int, access: CredentialAccessHandle
    ) -> RedditPage:
        raw = await self._transport.search(text, cursor, limit, await self._resolver(access))
        try:
            return RedditPage.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc

    async def fetch(self, content_id: str, access: CredentialAccessHandle) -> RedditItem:
        raw = await self._transport.fetch(content_id, await self._resolver(access))
        try:
            return RedditItem.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc
