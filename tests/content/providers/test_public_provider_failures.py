from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest
from pydantic import ValidationError

from openbiliclaw.access.models import AccessHandle, AnonymousAccessHandle, Permission
from openbiliclaw.content.integration.capabilities import ContentPage, FeedQuery
from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.providers.bangumi import BangumiClient, BangumiProvider
from openbiliclaw.content.providers.bangumi.models import BangumiPage, BangumiSubject
from openbiliclaw.content.providers.v2ex import V2EXClient, V2EXProvider
from openbiliclaw.content.providers.v2ex.models import V2EXPage, V2EXTopic
from openbiliclaw.content.providers.youtube import YouTubeClient, YouTubeProvider
from openbiliclaw.content.providers.youtube.models import YouTubePage, YouTubeVideo

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.content.integration.native import NativeContent
    from openbiliclaw.content.integration.projections import ContentPreview
    from openbiliclaw.core._pydantic import StrictBaseModel

NOW = datetime(2025, 1, 1, tzinfo=UTC)


class PublicProvider(Protocol):
    async def fetch(self, ref: ContentRef, access: AccessHandle) -> NativeContent: ...


class FeedProvider(PublicProvider, Protocol):
    async def feed(self, query: FeedQuery, access: AccessHandle) -> ContentPage[ContentPreview]: ...


class PageDump(Protocol):
    def model_dump_json(self) -> str: ...


def _youtube(transport: FailureTransport | BytesTransport) -> PublicProvider:
    return YouTubeProvider(YouTubeClient(transport))


def _bangumi(transport: FailureTransport | BytesTransport) -> FeedProvider:
    return BangumiProvider(BangumiClient(transport))


def _v2ex(transport: FailureTransport | BytesTransport) -> FeedProvider:
    return V2EXProvider(V2EXClient(transport))


class FailureTransport:
    def __init__(self, error: ContentIntegrationError) -> None:
        self.error = error

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        raise self.error


class BytesTransport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    async def __call__(self, operation: str, argument: str, cursor: str, limit: int) -> bytes:
        return self.payload


def access(provider: str) -> AnonymousAccessHandle:
    return AnonymousAccessHandle(
        provider_id=provider, account_id=None, permissions=frozenset({Permission.READ_PUBLIC})
    )


@pytest.mark.parametrize(
    ("factory", "provider_id"),
    [
        (_bangumi, "bangumi"),
        (_v2ex, "v2ex"),
    ],
)
@pytest.mark.parametrize(
    "code",
    [
        IntegrationErrorCode.ACCESS_DENIED,
        IntegrationErrorCode.RATE_LIMITED,
        IntegrationErrorCode.PROVIDER_UNAVAILABLE,
    ],
)
async def test_provider_failures_are_safe_and_preserve_classification(
    factory: Callable[[FailureTransport | BytesTransport], FeedProvider],
    provider_id: str,
    code: IntegrationErrorCode,
) -> None:
    canary = "SECRET-CANARY"
    transport = FailureTransport(ContentIntegrationError(code, "safe provider failure"))
    instance = factory(transport)
    with pytest.raises(ContentIntegrationError) as exc:
        await instance.feed(FeedQuery(), access(provider_id))
    assert exc.value.code is code
    assert canary not in str(exc.value)


@pytest.mark.parametrize(
    ("page", "factory", "provider_id"),
    [
        (BangumiPage(items=(), next_cursor=None), _bangumi, "bangumi"),
        (V2EXPage(items=(), next_cursor=None), _v2ex, "v2ex"),
    ],
)
async def test_empty_pages_remain_empty(
    page: PageDump,
    factory: Callable[[FailureTransport | BytesTransport], FeedProvider],
    provider_id: str,
) -> None:
    instance = factory(BytesTransport(page.model_dump_json().encode()))
    result = await instance.feed(FeedQuery(), access(provider_id))
    assert result.items == ()
    assert result.next_cursor is None


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (YouTubeVideo, {"id": "bad", "title": "x"}),
        (BangumiSubject, {"id": -1, "title": "x"}),
        (V2EXTopic, {"id": 0, "title": "x"}),
    ],
)
def test_malformed_native_payload_is_rejected(
    model: type[StrictBaseModel], payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    ("provider", "access_id", "ref"),
    [
        (
            YouTubeProvider(YouTubeClient(BytesTransport(b"{}"))),
            "youtube",
            ContentRef(
                provider_id=ProviderId(value="youtube"),
                content_kind=ContentKind(value="video"),
                provider_content_id="abcdefghijk",
                canonical_url="https://youtube.com/watch?v=abcdefghijk",
            ),
        ),
        (
            BangumiProvider(BangumiClient(BytesTransport(b"{}"))),
            "bangumi",
            ContentRef(
                provider_id=ProviderId(value="bangumi"),
                content_kind=ContentKind(value="subject"),
                provider_content_id="42",
                canonical_url="https://bgm.tv/subject/42",
            ),
        ),
        (
            V2EXProvider(V2EXClient(BytesTransport(b"{}"))),
            "v2ex",
            ContentRef(
                provider_id=ProviderId(value="v2ex"),
                content_kind=ContentKind(value="topic"),
                provider_content_id="99",
                canonical_url="https://www.v2ex.com/t/99",
            ),
        ),
    ],
)
async def test_fetch_empty_page_is_invalid_ref(
    provider: PublicProvider, access_id: str, ref: ContentRef
) -> None:
    empty = {
        "youtube": YouTubePage(items=(), next_cursor=None),
        "bangumi": BangumiPage(items=(), next_cursor=None),
        "v2ex": V2EXPage(items=(), next_cursor=None),
    }[access_id]
    instance = {"youtube": _youtube, "bangumi": _bangumi, "v2ex": _v2ex}[access_id](
        BytesTransport(empty.model_dump_json().encode())
    )
    with pytest.raises(ContentIntegrationError) as exc:
        await instance.fetch(ref, access(access_id))
    assert exc.value.code is IntegrationErrorCode.INVALID_CONTENT_REF


def _imports(node: ast.AST, package: str) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        if node.level == 0:
            return (node.module or "",)
        parts = package.split(".")
        base = parts[: len(parts) - node.level + 1]
        if node.module:
            base.extend(node.module.split("."))
        return (".".join(base),)
    return ()


def test_public_provider_packages_import_only_approved_boundaries() -> None:
    root = Path(__file__).parents[3] / "src" / "openbiliclaw" / "content" / "providers"
    violations: list[str] = []
    for name in ("youtube", "bangumi", "v2ex"):
        package_root = root / name
        for path in package_root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                for module in _imports(node, f"openbiliclaw.content.providers.{name}"):
                    root_name = module.split(".", 1)[0]
                    allowed = (
                        root_name in sys.stdlib_module_names
                        or root_name in {"pydantic", "httpx", "anyio", "yt_dlp"}
                        or module.startswith("openbiliclaw.content.integration")
                        or module.startswith(f"openbiliclaw.content.providers.{name}")
                        or module.startswith("openbiliclaw.access")
                        or module.startswith("openbiliclaw.core")
                        or module == "openbiliclaw.infrastructure.http.clients"
                    )
                    if not allowed:
                        violations.append(f"{path.name}:{getattr(node, 'lineno', 0)}:{module}")
    assert violations == []
