"""Bangumi identity and implemented contracts."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

BANGUMI_ID = ProviderId(value="bangumi")
SUBJECT_KIND = ContentKind(value="subject")
BANGUMI_MANIFEST = ProviderManifest(
    provider_id=BANGUMI_ID,
    display_name="Bangumi",
    capabilities=frozenset(
        {
            CapabilityKind.SEARCH,
            CapabilityKind.FEED,
            CapabilityKind.FETCH,
            CapabilityKind.PROJECTION,
        }
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=SUBJECT_KIND, schema_version=1),),
    channels=(
        ChannelDescriptor(
            feed_id="rank",
            bias_class=BiasClass.PLATFORM_POPULARITY,
            auth_required=False,
        ),
    ),
    image_hosts=("lain.bgm.tv",),
    availability=ProviderAvailability.AVAILABLE,
)
