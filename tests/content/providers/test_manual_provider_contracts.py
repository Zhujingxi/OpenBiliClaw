from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import pytest
from pydantic import ValidationError

from openbiliclaw.access.models import (
    AccessHandle,
    AnonymousAccessHandle,
    CredentialAccessHandle,
    Permission,
)
from openbiliclaw.content.integration.capabilities import ContentPage, PageRequest, SearchQuery
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.manifest import ProviderManifest
from openbiliclaw.content.integration.registry import provider_contract_violations
from openbiliclaw.content.providers.linuxdo import LINUXDO_MANIFEST, LinuxDoClient, LinuxDoProvider
from openbiliclaw.content.providers.reddit import REDDIT_MANIFEST, RedditClient, RedditProvider
from openbiliclaw.content.providers.weibo import WEIBO_MANIFEST, WeiboClient, WeiboProvider
from openbiliclaw.content.providers.x import X_MANIFEST, XClient, XProvider
from openbiliclaw.content.providers.zhihu import ZHIHU_MANIFEST, ZhihuClient, ZhihuProvider

if TYPE_CHECKING:
    from openbiliclaw.content.integration.native import NativeContent
    from openbiliclaw.content.integration.projections import (
        CardData,
        ContentPreview,
        RecommendationCandidate,
        SearchDocument,
    )

NOW = 1_700_000_000


class Resolver:
    def __init__(self, secret: str = "CANARY-secret") -> None:
        self.secret = secret
        self.calls = 0

    async def __call__(self, handle: CredentialAccessHandle) -> str:
        self.calls += 1
        return self.secret


class Transport:
    def __init__(self, provider: str, kind: str) -> None:
        self.provider = provider
        self.kind = kind
        self.secrets: list[str | None] = []
        self.search_payload: bytes | None = None
        self.fetch_payload: bytes | None = None
        self.failure: ContentIntegrationError | None = None

    def item(self, ident: str = "1") -> dict[str, object]:
        return {
            "id": ident,
            "title": f"{self.provider} title",
            "body": "safe body",
            "author": "alice",
            "url": f"https://example.com/{ident}",
            "published_at": NOW,
            "deleted": False,
        }

    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        self.secrets.append(credential)
        if self.failure is not None:
            raise self.failure
        if self.search_payload is not None:
            return self.search_payload
        import json

        return json.dumps({"items": [self.item()], "next_cursor": "next"}).encode()

    async def fetch(self, content_id: str, credential: str | None) -> bytes:
        self.secrets.append(credential)
        if self.fetch_payload is not None:
            return self.fetch_payload
        import json

        return json.dumps(self.item(content_id)).encode()


class ManualProvider(Protocol):
    async def search(
        self, query: SearchQuery, access: AccessHandle
    ) -> ContentPage[ContentPreview]: ...
    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent: ...
    def native_from_bytes(self, raw: bytes) -> NativeContent: ...
    def preview(self, content: NativeContent) -> ContentPreview: ...
    def card_data(self, content: NativeContent) -> CardData: ...
    def recommendation_candidate(self, content: NativeContent) -> RecommendationCandidate: ...
    def search_document(self, content: NativeContent) -> SearchDocument: ...


def _reddit(t: Transport, r: Resolver) -> ManualProvider:
    return RedditProvider(RedditClient(t, r))


def _x(t: Transport, r: Resolver) -> ManualProvider:
    return XProvider(XClient(t, r))


def _zhihu(t: Transport, r: Resolver) -> ManualProvider:
    return ZhihuProvider(ZhihuClient(t, r))


def _linuxdo(t: Transport, r: Resolver) -> ManualProvider:
    return LinuxDoProvider(LinuxDoClient(t, r))


def _weibo(t: Transport, r: Resolver) -> ManualProvider:
    return WeiboProvider(WeiboClient(t))


Case = tuple[str, str, ProviderManifest, Callable[[Transport, Resolver], ManualProvider], bool]
PROVIDERS: tuple[Case, ...] = (
    ("reddit", "post", REDDIT_MANIFEST, _reddit, True),
    ("x", "post", X_MANIFEST, _x, True),
    ("zhihu", "answer", ZHIHU_MANIFEST, _zhihu, True),
    ("linuxdo", "topic", LINUXDO_MANIFEST, _linuxdo, True),
    ("weibo", "post", WEIBO_MANIFEST, _weibo, False),
)


def access(provider: str, private: bool) -> CredentialAccessHandle | AnonymousAccessHandle:
    if private:
        return CredentialAccessHandle(
            provider_id=provider,
            account_id="acct",
            permissions=frozenset({Permission.READ_PRIVATE}),
            credential_ref="cred_" + "1" * 32,
            revision=1,
        )
    return AnonymousAccessHandle(
        provider_id=provider, account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )


@pytest.mark.parametrize("provider,kind,manifest,factory,private", PROVIDERS)
async def test_contract_search_fetch_projection(
    provider: str,
    kind: str,
    manifest: ProviderManifest,
    factory: Callable[[Transport, Resolver], ManualProvider],
    private: bool,
) -> None:
    transport = Transport(provider, kind)
    resolver = Resolver()
    implementation = factory(transport, resolver)
    assert provider_contract_violations(manifest, implementation) == ()
    page = await implementation.search(
        SearchQuery(text="typed", page=PageRequest(limit=1)), access(provider, private)
    )
    assert len(page.items) == 1 and page.next_cursor is not None
    native = await implementation.fetch(
        ContentRef(
            provider_id=ProviderId(value=provider),
            content_kind=ContentKind(value=kind),
            provider_content_id="42",
            canonical_url="https://example.com/42",
        ),
        access(provider, private),
    )
    assert implementation.preview(native).provenance.ref == native.ref
    assert implementation.card_data(native).source_timestamp == datetime.fromtimestamp(NOW, tz=UTC)
    assert "CANARY" not in native.model_dump_json()
    assert resolver.calls == (2 if private else 0)


@pytest.mark.parametrize("provider,kind,manifest,factory,private", PROVIDERS)
async def test_empty_tombstone_schema_drift_and_scope(
    provider: str,
    kind: str,
    manifest: ProviderManifest,
    factory: Callable[[Transport, Resolver], ManualProvider],
    private: bool,
) -> None:
    transport = Transport(provider, kind)
    resolver = Resolver()
    implementation = factory(transport, resolver)
    import json

    async def empty(text: str, cursor: str | None, limit: int, credential: str | None) -> bytes:
        return b'{"items":[],"next_cursor":null}'

    transport.search_payload = await empty("", None, 1, None)
    page = await implementation.search(SearchQuery(text="none"), access(provider, private))
    assert page.items == ()
    with pytest.raises(ContentIntegrationError) as denied:
        await implementation.search(SearchQuery(text="x"), access("other", private))
    assert denied.value.code is IntegrationErrorCode.ACCESS_DENIED
    item = transport.item()
    item["deleted"] = True
    assert (
        '"deleted":true'
        in implementation.native_from_bytes(json.dumps(item).encode()).payload.model_dump_json()
    )
    item["unknown"] = 1
    with pytest.raises(ValidationError):
        implementation.native_from_bytes(json.dumps(item).encode())


@pytest.mark.parametrize("provider,kind,manifest,factory,private", PROVIDERS)
async def test_projection_edges_invalid_cursor_ref_and_transport_drift(
    provider: str,
    kind: str,
    manifest: ProviderManifest,
    factory: Callable[[Transport, Resolver], ManualProvider],
    private: bool,
) -> None:
    transport = Transport(provider, kind)
    resolver = Resolver()
    implementation = factory(transport, resolver)
    native = implementation.native_from_bytes(__import__("json").dumps(transport.item()).encode())
    assert implementation.recommendation_candidate(native).discovery_reason == f"{provider}:search"
    assert implementation.search_document(native).title.endswith("title")
    from openbiliclaw.content.integration.capabilities import ProviderCursor

    with pytest.raises(ContentIntegrationError) as cursor_error:
        await implementation.search(
            SearchQuery(
                text="x",
                page=PageRequest(
                    cursor=ProviderCursor(provider_id=ProviderId(value="other"), value="1")
                ),
            ),
            access(provider, private),
        )
    assert cursor_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF
    with pytest.raises(ContentIntegrationError) as ref_error:
        await implementation.fetch(
            ContentRef(
                provider_id=ProviderId(value="other"),
                content_kind=ContentKind(value=kind),
                provider_content_id="1",
                canonical_url="https://example.com/1",
            ),
            access(provider, private),
        )
    assert ref_error.value.code is IntegrationErrorCode.INVALID_CONTENT_REF

    async def invalid_search(
        text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        return b'{"items":"bad","next_cursor":null}'

    transport.search_payload = await invalid_search("", None, 1, None)
    with pytest.raises(ContentIntegrationError) as drift:
        await implementation.search(SearchQuery(text="x"), access(provider, private))
    assert drift.value.code is IntegrationErrorCode.PROVIDER_UNAVAILABLE

    async def invalid_fetch(content_id: str, credential: str | None) -> bytes:
        return b'{"id":1}'

    transport.fetch_payload = await invalid_fetch("", None)
    with pytest.raises(ContentIntegrationError):
        await implementation.fetch(
            ContentRef(
                provider_id=ProviderId(value=provider),
                content_kind=ContentKind(value=kind),
                provider_content_id="1",
                canonical_url="https://example.com/1",
            ),
            access(provider, private),
        )


@pytest.mark.parametrize("provider,kind,manifest,factory,private", PROVIDERS)
@pytest.mark.parametrize(
    "code", [IntegrationErrorCode.ACCESS_DENIED, IntegrationErrorCode.RATE_LIMITED]
)
async def test_auth_and_rate_limit_failures_are_normalized_without_secret(
    provider: str,
    kind: str,
    manifest: ProviderManifest,
    factory: Callable[[Transport, Resolver], ManualProvider],
    private: bool,
    code: IntegrationErrorCode,
) -> None:
    transport = Transport(provider, kind)
    transport.failure = ContentIntegrationError(code, "safe provider failure")
    resolver = Resolver()
    implementation = factory(transport, resolver)
    with pytest.raises(ContentIntegrationError) as raised:
        await implementation.search(SearchQuery(text="x"), access(provider, private))
    assert raised.value.code is code
    assert "CANARY" not in str(raised.value)
