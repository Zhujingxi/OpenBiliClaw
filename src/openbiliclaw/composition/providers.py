"""Explicit first-party provider construction and validated registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openbiliclaw.content.integration.manifest import ProviderManifest
from openbiliclaw.content.integration.registry import ContentProviderRegistry
from openbiliclaw.content.providers.bangumi import (
    BANGUMI_MANIFEST,
    BangumiClient,
    BangumiProvider,
    HttpxBangumiTransport,
)
from openbiliclaw.content.providers.bilibili import (
    BILIBILI_MANIFEST,
    BilibiliClient,
    BilibiliProvider,
    HttpxBilibiliTransport,
)
from openbiliclaw.content.providers.bilibili.client import CredentialResolver
from openbiliclaw.content.providers.douyin.capabilities import DouyinProvider
from openbiliclaw.content.providers.douyin.manifest import DOUYIN_MANIFEST
from openbiliclaw.content.providers.rednote.capabilities import RednoteProvider
from openbiliclaw.content.providers.rednote.manifest import REDNOTE_MANIFEST
from openbiliclaw.content.providers.v2ex import (
    V2EX_MANIFEST,
    HttpxV2EXTransport,
    V2EXClient,
    V2EXProvider,
)
from openbiliclaw.content.providers.youtube import (
    YOUTUBE_MANIFEST,
    HttpxYouTubeTransport,
    YouTubeClient,
    YouTubeProvider,
)

if TYPE_CHECKING:
    from openbiliclaw.access.models import CredentialAccessHandle

ProviderBuilder = Callable[[], tuple[ProviderManifest, object]]


class _UnavailableCredentialResolver(CredentialResolver):
    async def __call__(self, handle: CredentialAccessHandle) -> str:
        del handle
        raise RuntimeError("credential resolver is unavailable")


@dataclass(frozen=True, slots=True)
class ProviderGraph:
    registry: ContentProviderRegistry
    enabled: tuple[str, ...]
    degraded: tuple[str, ...]


def _bilibili() -> tuple[ProviderManifest, object]:
    resolver: CredentialResolver = _UnavailableCredentialResolver()
    client = BilibiliClient(HttpxBilibiliTransport(), resolver)
    return BILIBILI_MANIFEST, BilibiliProvider(client)


def _bangumi() -> tuple[ProviderManifest, object]:
    return BANGUMI_MANIFEST, BangumiProvider(BangumiClient(HttpxBangumiTransport()))


def _douyin() -> tuple[ProviderManifest, object]:
    return DOUYIN_MANIFEST, DouyinProvider()


def _rednote() -> tuple[ProviderManifest, object]:
    return REDNOTE_MANIFEST, RednoteProvider()


def _v2ex() -> tuple[ProviderManifest, object]:
    return V2EX_MANIFEST, V2EXProvider(V2EXClient(HttpxV2EXTransport()))


def _youtube() -> tuple[ProviderManifest, object]:
    return YOUTUBE_MANIFEST, YouTubeProvider(YouTubeClient(HttpxYouTubeTransport()))


_BUILDERS: dict[str, ProviderBuilder] = {
    "bangumi": _bangumi,
    "bilibili": _bilibili,
    "douyin": _douyin,
    "rednote": _rednote,
    "v2ex": _v2ex,
    "youtube": _youtube,
}


def build_providers(enabled: tuple[str, ...]) -> ProviderGraph:
    """Build named first-party providers; unknown optional names degrade independently."""
    registry = ContentProviderRegistry()
    active: list[str] = []
    degraded: list[str] = []
    for provider_id in dict.fromkeys(enabled):
        builder = _BUILDERS.get(provider_id)
        if builder is None:
            degraded.append(provider_id)
            continue
        manifest, implementation = builder()
        registry.register(manifest, implementation)
        active.append(provider_id)
    return ProviderGraph(registry, tuple(active), tuple(degraded))
