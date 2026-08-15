"""Provider channel registry contract and production declarations."""

from __future__ import annotations

import pytest

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)
from openbiliclaw.content.providers.bangumi import BANGUMI_MANIFEST
from openbiliclaw.content.providers.bilibili import BILIBILI_MANIFEST
from openbiliclaw.content.providers.douyin.manifest import DOUYIN_MANIFEST
from openbiliclaw.content.providers.linuxdo import LINUXDO_MANIFEST
from openbiliclaw.content.providers.reddit import REDDIT_MANIFEST
from openbiliclaw.content.providers.rednote.manifest import REDNOTE_MANIFEST
from openbiliclaw.content.providers.v2ex import V2EX_MANIFEST
from openbiliclaw.content.providers.weibo import WEIBO_MANIFEST
from openbiliclaw.content.providers.x import X_MANIFEST
from openbiliclaw.content.providers.youtube import YOUTUBE_MANIFEST
from openbiliclaw.content.providers.zhihu import ZHIHU_MANIFEST


def _manifest(
    *capabilities: CapabilityKind, channels: tuple[ChannelDescriptor, ...] = ()
) -> ProviderManifest:
    return ProviderManifest(
        provider_id=ProviderId(value="demo"),
        display_name="Demo",
        capabilities=frozenset(capabilities),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="video"), schema_version=1),
        ),
        channels=channels,
        availability=ProviderAvailability.AVAILABLE,
    )


def test_channel_registry_validates_and_looks_up_feed() -> None:
    popular = ChannelDescriptor(
        feed_id="popular", bias_class=BiasClass.PLATFORM_POPULARITY, auth_required=False
    )
    manifest = _manifest(CapabilityKind.FEED, channels=(popular,))

    assert manifest.channel("popular") == popular
    with pytest.raises(KeyError, match="missing"):
        manifest.channel("missing")
    with pytest.raises(ValueError, match="duplicate channel feed_id"):
        _manifest(CapabilityKind.FEED, channels=(popular, popular))
    with pytest.raises(ValueError, match="channel declarations and advertised feed"):
        _manifest(CapabilityKind.FEED)
    with pytest.raises(ValueError, match="channel declarations and advertised feed"):
        _manifest(CapabilityKind.SEARCH, channels=(popular,))


def test_manifest_validates_declarative_image_proxy_configuration() -> None:
    configured = ProviderManifest(
        provider_id=ProviderId(value="images"),
        display_name="Images",
        capabilities=frozenset(),
        native_schemas=(
            NativeSchemaDescriptor(content_kind=ContentKind(value="image"), schema_version=1),
        ),
        image_hosts=("cdn.example.test",),
        image_headers={"referer": "https://example.test"},
        availability=ProviderAvailability.AVAILABLE,
    )
    assert configured.image_hosts == ("cdn.example.test",)
    assert configured.image_headers == {"referer": "https://example.test"}
    for invalid in ("https://cdn.test", "CDN.test", "cdn.test.", "127.0.0.1", "bad host"):
        with pytest.raises(ValueError):
            ProviderManifest(
                provider_id=ProviderId(value="bad"),
                display_name="Bad",
                capabilities=frozenset(),
                native_schemas=(),
                image_hosts=(invalid,),
                availability=ProviderAvailability.AVAILABLE,
            )


def test_all_production_manifests_declare_channels_iff_feed_is_advertised() -> None:
    manifests = (
        BANGUMI_MANIFEST,
        BILIBILI_MANIFEST,
        DOUYIN_MANIFEST,
        LINUXDO_MANIFEST,
        REDDIT_MANIFEST,
        REDNOTE_MANIFEST,
        V2EX_MANIFEST,
        WEIBO_MANIFEST,
        X_MANIFEST,
        YOUTUBE_MANIFEST,
        ZHIHU_MANIFEST,
    )
    for manifest in manifests:
        has_feed = CapabilityKind.FEED in manifest.capabilities
        assert bool(manifest.channels) is has_feed, manifest.provider_id.value

    assert BILIBILI_MANIFEST.channel("popular") == ChannelDescriptor(
        feed_id="popular", bias_class=BiasClass.PLATFORM_POPULARITY, auth_required=False
    )
    assert BANGUMI_MANIFEST.channel("rank").bias_class is BiasClass.PLATFORM_POPULARITY
    assert V2EX_MANIFEST.channel("hot").bias_class is BiasClass.PLATFORM_POPULARITY
    assert {channel.feed_id for manifest in manifests for channel in manifest.channels} == {
        "popular",
        "rank",
        "hot",
    }
    assert BILIBILI_MANIFEST.image_hosts == ("i0.hdslb.com",)
    assert BILIBILI_MANIFEST.image_headers == {"referer": "https://www.bilibili.com"}
    assert BANGUMI_MANIFEST.image_hosts == ("lain.bgm.tv",)
    assert YOUTUBE_MANIFEST.image_hosts == ("i.ytimg.com",)
    assert DOUYIN_MANIFEST.image_hosts == ("douyinpic.com",)
    assert REDNOTE_MANIFEST.image_hosts == ("xhscdn.com",)
