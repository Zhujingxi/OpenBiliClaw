"""Typed injected X transport boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode

if TYPE_CHECKING:
    from openbiliclaw.access.models import CredentialAccessHandle

from .models import XItem, XPage


class XTransport(Protocol):
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes: ...
    async def fetch(self, content_id: str, credential: str | None) -> bytes: ...


class CredentialResolver(Protocol):
    async def __call__(self, handle: CredentialAccessHandle) -> str: ...


class XClient:
    def __init__(self, transport: XTransport, resolver: CredentialResolver) -> None:
        self._transport, self._resolver = transport, resolver

    def __repr__(self) -> str:
        return "XClient(credentials=<opaque>)"

    async def search(
        self, text: str, cursor: str | None, limit: int, access: CredentialAccessHandle
    ) -> XPage:
        raw = await self._transport.search(text, cursor, limit, await self._resolver(access))
        try:
            return XPage.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc

    async def fetch(self, content_id: str, access: CredentialAccessHandle) -> XItem:
        raw = await self._transport.fetch(content_id, await self._resolver(access))
        try:
            return XItem.model_validate_json(raw)
        except ValidationError as exc:
            raise ContentIntegrationError(
                IntegrationErrorCode.PROVIDER_UNAVAILABLE, "provider returned invalid data"
            ) from exc
