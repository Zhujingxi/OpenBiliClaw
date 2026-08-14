"""YouTube identity and implemented contracts."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

YOUTUBE_ID = ProviderId(value="youtube")
VIDEO_KIND = ContentKind(value="video")
YOUTUBE_MANIFEST = ProviderManifest(
    provider_id=YOUTUBE_ID,
    display_name="YouTube",
    capabilities=frozenset(
        {
            CapabilityKind.SEARCH,
            CapabilityKind.FETCH,
            CapabilityKind.CREATOR,
            CapabilityKind.PROJECTION,
        }
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=VIDEO_KIND, schema_version=1),),
    availability=ProviderAvailability.AVAILABLE,
)
