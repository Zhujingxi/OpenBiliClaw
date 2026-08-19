"""Explicit first-party content and access-provider construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from openbiliclaw.access.manual import ManualProviderSpec
from openbiliclaw.access.models import Permission
from openbiliclaw.content.integration.registry import ContentProviderRegistry
from openbiliclaw.content.providers.bangumi import (
    BANGUMI_CONNECTION_FORM,
    BANGUMI_MANIFEST,
    BangumiClient,
    BangumiCredentialVerifier,
    BangumiProvider,
    HttpxBangumiTransport,
)
from openbiliclaw.content.providers.bilibili import (
    BILIBILI_CONNECTION_FORM,
    BILIBILI_MANIFEST,
    BilibiliClient,
    BilibiliCredentialVerifier,
    BilibiliProvider,
    HttpxBilibiliTransport,
)
from openbiliclaw.content.providers.douyin.capabilities import DouyinProvider
from openbiliclaw.content.providers.douyin.manifest import DOUYIN_MANIFEST
from openbiliclaw.content.providers.hackernews import (
    HACKER_NEWS_MANIFEST,
    HackerNewsClient,
    HackerNewsProvider,
    HttpxHackerNewsTransport,
)
from openbiliclaw.content.providers.linuxdo import LINUXDO_MANIFEST, LinuxDoProvider
from openbiliclaw.content.providers.linuxdo.auth import (
    LINUXDO_CONNECTION_FORM,
    LinuxDoCredentialVerifier,
)
from openbiliclaw.content.providers.reddit import REDDIT_MANIFEST, RedditProvider
from openbiliclaw.content.providers.reddit.auth import (
    REDDIT_CONNECTION_FORM,
    RedditCredentialVerifier,
)
from openbiliclaw.content.providers.rednote.capabilities import RednoteProvider
from openbiliclaw.content.providers.rednote.manifest import REDNOTE_MANIFEST
from openbiliclaw.content.providers.v2ex import (
    V2EX_CONNECTION_FORM,
    V2EX_MANIFEST,
    HttpxV2EXTransport,
    V2EXClient,
    V2EXCredentialVerifier,
    V2EXProvider,
)
from openbiliclaw.content.providers.weibo import WEIBO_MANIFEST, WeiboProvider
from openbiliclaw.content.providers.x import X_MANIFEST, XProvider
from openbiliclaw.content.providers.x.auth import X_CONNECTION_FORM, XCredentialVerifier
from openbiliclaw.content.providers.youtube import (
    YOUTUBE_MANIFEST,
    YouTubeClient,
    YouTubeProvider,
    YtDlpYouTubeTransport,
)
from openbiliclaw.content.providers.zhihu import ZHIHU_MANIFEST, ZhihuProvider
from openbiliclaw.content.providers.zhihu.auth import (
    ZHIHU_CONNECTION_FORM,
    ZhihuCredentialVerifier,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.access.forms import ConnectionForm
    from openbiliclaw.access.manual import CredentialVerifier
    from openbiliclaw.access.models import CredentialAccessHandle
    from openbiliclaw.content.integration.manifest import ProviderManifest
    from openbiliclaw.infrastructure.credentials.vault import CredentialVault


class CredentialTransport(Protocol):
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes: ...

    async def fetch(self, content_id: str, credential: str | None) -> bytes: ...


class _UnavailableCredentialTransport:
    async def search(
        self, text: str, cursor: str | None, limit: int, credential: str | None
    ) -> bytes:
        del text, cursor, limit, credential
        raise RuntimeError("provider transport is not configured")

    async def fetch(self, content_id: str, credential: str | None) -> bytes:
        del content_id, credential
        raise RuntimeError("provider transport is not configured")


class _UnavailableProbe:
    async def __call__(self, credential: str) -> str | None:
        del credential
        raise RuntimeError("provider credential probe is not configured")


class _VaultCredentialResolver:
    def __init__(self, vault: CredentialVault) -> None:
        self._vault = vault

    async def __call__(self, handle: CredentialAccessHandle) -> str:
        def decode(secret: memoryview) -> str:
            values = json.loads(secret.tobytes())
            if not isinstance(values, dict) or len(values) != 1:
                raise ValueError("invalid provider credential record")
            value = next(iter(values.values()))
            if not isinstance(value, str):
                raise ValueError("invalid provider credential value")
            return value

        return self._vault.resolve(handle.credential_ref, decode)


@dataclass(frozen=True, slots=True)
class _BuiltProvider:
    manifest: ProviderManifest
    implementation: object
    manual: ManualProviderSpec | None = None


@dataclass(frozen=True, slots=True)
class ProviderGraph:
    registry: ContentProviderRegistry
    enabled: tuple[str, ...]
    degraded: tuple[str, ...]
    manual_specs: tuple[ManualProviderSpec, ...] = ()


def _manual(
    form: ConnectionForm,
    verifier: CredentialVerifier,
    capabilities: frozenset[Permission],
) -> ManualProviderSpec:
    return ManualProviderSpec(form=form, capabilities=capabilities, verifier=verifier)


def _builders(vault: CredentialVault) -> dict[str, Callable[[], _BuiltProvider]]:
    resolver = _VaultCredentialResolver(vault)
    unavailable: CredentialTransport = _UnavailableCredentialTransport()

    def bilibili() -> _BuiltProvider:
        client = BilibiliClient(HttpxBilibiliTransport(), resolver)
        return _BuiltProvider(
            BILIBILI_MANIFEST,
            BilibiliProvider(client),
            _manual(
                BILIBILI_CONNECTION_FORM,
                BilibiliCredentialVerifier(client),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE, Permission.WRITE}),
            ),
        )

    def bangumi() -> _BuiltProvider:
        client = BangumiClient(HttpxBangumiTransport())
        return _BuiltProvider(
            BANGUMI_MANIFEST,
            BangumiProvider(client),
            _manual(
                BANGUMI_CONNECTION_FORM,
                BangumiCredentialVerifier(_UnavailableIdentityClient()),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            ),
        )

    def v2ex() -> _BuiltProvider:
        client = V2EXClient(HttpxV2EXTransport())
        return _BuiltProvider(
            V2EX_MANIFEST,
            V2EXProvider(client),
            _manual(
                V2EX_CONNECTION_FORM,
                V2EXCredentialVerifier(_UnavailableIdentityClient()),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            ),
        )

    from openbiliclaw.content.providers.linuxdo.client import HttpxLinuxDoTransport, LinuxDoClient
    from openbiliclaw.content.providers.reddit.client import RedditClient
    from openbiliclaw.content.providers.weibo.client import HttpxWeiboTransport, WeiboClient
    from openbiliclaw.content.providers.x.client import XClient
    from openbiliclaw.content.providers.zhihu.client import ZhihuClient

    return {
        "bangumi": bangumi,
        "bilibili": bilibili,
        "douyin": lambda: _BuiltProvider(DOUYIN_MANIFEST, DouyinProvider()),
        "hackernews": lambda: _BuiltProvider(
            HACKER_NEWS_MANIFEST,
            HackerNewsProvider(HackerNewsClient(HttpxHackerNewsTransport())),
        ),
        "linuxdo": lambda: _BuiltProvider(
            LINUXDO_MANIFEST,
            LinuxDoProvider(LinuxDoClient(HttpxLinuxDoTransport(), resolver)),
            _manual(
                LINUXDO_CONNECTION_FORM,
                LinuxDoCredentialVerifier(_UnavailableProbe()),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            ),
        ),
        "reddit": lambda: _BuiltProvider(
            REDDIT_MANIFEST,
            RedditProvider(RedditClient(unavailable, resolver)),
            _manual(
                REDDIT_CONNECTION_FORM,
                RedditCredentialVerifier(_UnavailableProbe()),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            ),
        ),
        "rednote": lambda: _BuiltProvider(REDNOTE_MANIFEST, RednoteProvider()),
        "v2ex": v2ex,
        "weibo": lambda: _BuiltProvider(
            WEIBO_MANIFEST, WeiboProvider(WeiboClient(HttpxWeiboTransport()))
        ),
        "x": lambda: _BuiltProvider(
            X_MANIFEST,
            XProvider(XClient(unavailable, resolver)),
            _manual(
                X_CONNECTION_FORM,
                XCredentialVerifier(_UnavailableProbe()),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            ),
        ),
        "youtube": lambda: _BuiltProvider(
            YOUTUBE_MANIFEST, YouTubeProvider(YouTubeClient(YtDlpYouTubeTransport()))
        ),
        "zhihu": lambda: _BuiltProvider(
            ZHIHU_MANIFEST,
            ZhihuProvider(ZhihuClient(unavailable, resolver)),
            _manual(
                ZHIHU_CONNECTION_FORM,
                ZhihuCredentialVerifier(_UnavailableProbe()),
                frozenset({Permission.READ_PUBLIC, Permission.READ_PRIVATE}),
            ),
        ),
    }


class _UnavailableIdentityClient:
    async def identity(self, token: str) -> str:
        del token
        raise RuntimeError("provider identity endpoint is unavailable")


def build_providers(enabled: tuple[str, ...], vault: CredentialVault) -> ProviderGraph:
    """Build every enabled landed provider; unknown names degrade independently."""
    registry = ContentProviderRegistry()
    builders = _builders(vault)
    active: list[str] = []
    degraded: list[str] = []
    manual_specs: list[ManualProviderSpec] = []
    for provider_id in dict.fromkeys(enabled):
        builder = builders.get(provider_id)
        if builder is None:
            degraded.append(provider_id)
            continue
        built = builder()
        recipe = built.manifest.access_recipe
        if recipe is not None and (
            built.manual is None or built.manual.form.method_id != recipe.target_method_id
        ):
            raise ValueError("access recipe target method is not implemented")
        registry.register(built.manifest, built.implementation)
        active.append(provider_id)
        if built.manual is not None:
            manual_specs.append(built.manual)
    return ProviderGraph(registry, tuple(active), tuple(degraded), tuple(manual_specs))
