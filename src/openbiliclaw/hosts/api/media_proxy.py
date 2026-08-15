"""Bounded, provider-declared HTTPS image fetching for the local media route."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openbiliclaw.content.integration.manifest import ProviderManifest
    from openbiliclaw.infrastructure.http.clients import HttpClientFactory

_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_TIMEOUT_SECONDS = 5.0


class MediaProxyError(Exception):
    """Safe media failure mapped by the route to a typed 404 or 502."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class MediaResult:
    content: bytes
    content_type: str


class MediaProxy:
    """Fetch images only from declarative provider CDN allowlists."""

    def __init__(
        self,
        manifests: tuple[ProviderManifest, ...],
        http: HttpClientFactory,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = http
        self._transport = transport
        self._sources = tuple(
            sorted(
                (
                    (host, manifest.image_headers)
                    for manifest in manifests
                    for host in manifest.image_hosts
                ),
                key=lambda item: len(item[0]),
                reverse=True,
            )
        )

    def _headers_for(self, url: str) -> Mapping[str, str]:
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise MediaProxyError(404, "media not found") from exc
        if (
            parsed.scheme.lower() != "https"
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or host.endswith(".")
            or port not in (None, 443)
        ):
            raise MediaProxyError(404, "media not found")
        normalized = host.lower()
        try:
            ip_address(normalized)
        except ValueError:
            pass
        else:
            raise MediaProxyError(404, "media not found")
        for allowed, headers in self._sources:
            if normalized == allowed or normalized.endswith(f".{allowed}"):
                return headers
        raise MediaProxyError(404, "media not found")

    async def fetch(self, url: str) -> MediaResult:
        headers = self._headers_for(url)
        try:
            async with (
                self._http.client(transport=self._transport) as client,
                client.stream("GET", url, headers=headers, timeout=_TIMEOUT_SECONDS) as response,
            ):
                if response.status_code == 404:
                    raise MediaProxyError(404, "media not found")
                if response.status_code != 200:
                    raise MediaProxyError(502, "media provider failed")
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
                if not content_type.lower().startswith("image/"):
                    raise MediaProxyError(502, "media provider returned non-image content")
                length = response.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) > _MAX_IMAGE_BYTES):
                    raise MediaProxyError(502, "media exceeds size limit")
                chunks: list[bytes] = []
                seen = 0
                async for chunk in response.aiter_bytes():
                    seen += len(chunk)
                    if seen > _MAX_IMAGE_BYTES:
                        raise MediaProxyError(502, "media exceeds size limit")
                    chunks.append(chunk)
        except MediaProxyError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            raise MediaProxyError(502, "media provider failed") from exc
        return MediaResult(content=b"".join(chunks), content_type=content_type.lower())
