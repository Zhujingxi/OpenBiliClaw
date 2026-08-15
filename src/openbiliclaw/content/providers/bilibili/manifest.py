"""Bilibili provider identity and advertised implemented contracts."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    ActionDescriptor,
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

BILIBILI_ID = ProviderId(value="bilibili")
VIDEO_KIND = ContentKind(value="video")
ARTICLE_KIND = ContentKind(value="article")

BILIBILI_MANIFEST = ProviderManifest(
    provider_id=BILIBILI_ID,
    display_name="Bilibili",
    capabilities=frozenset(
        {
            CapabilityKind.SEARCH,
            CapabilityKind.FEED,
            CapabilityKind.FETCH,
            CapabilityKind.RELATED,
            CapabilityKind.CREATOR,
            CapabilityKind.HISTORY,
            CapabilityKind.SAVED,
            CapabilityKind.ACTION,
            CapabilityKind.PROJECTION,
            CapabilityKind.OBSERVATION,
        }
    ),
    native_schemas=(
        NativeSchemaDescriptor(content_kind=VIDEO_KIND, schema_version=1),
        NativeSchemaDescriptor(content_kind=ARTICLE_KIND, schema_version=1),
    ),
    actions=(ActionDescriptor(action_id="save", label="Save", content_kind=VIDEO_KIND),),
    channels=(
        ChannelDescriptor(
            feed_id="popular",
            bias_class=BiasClass.PLATFORM_POPULARITY,
            auth_required=False,
        ),
    ),
    availability=ProviderAvailability.AVAILABLE,
)
