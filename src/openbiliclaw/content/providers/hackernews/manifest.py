"""Hacker News identity and implemented contracts."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

HACKER_NEWS_ID = ProviderId(value="hackernews")
ITEM_KIND = ContentKind(value="item")
HACKER_NEWS_MANIFEST = ProviderManifest(
    provider_id=HACKER_NEWS_ID,
    display_name="Hacker News",
    capabilities=frozenset({CapabilityKind.FEED, CapabilityKind.FETCH, CapabilityKind.PROJECTION}),
    native_schemas=(NativeSchemaDescriptor(content_kind=ITEM_KIND, schema_version=1),),
    channels=(
        ChannelDescriptor(
            feed_id="top",
            bias_class=BiasClass.PLATFORM_POPULARITY,
            auth_required=False,
        ),
    ),
    availability=ProviderAvailability.AVAILABLE,
)
