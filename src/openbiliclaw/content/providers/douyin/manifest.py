"""Douyin identity and only currently replayable capabilities."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

DOUYIN_ID = ProviderId(value="douyin")
SHORT_VIDEO_KIND = ContentKind(value="short_video")

DOUYIN_MANIFEST = ProviderManifest(
    provider_id=DOUYIN_ID,
    display_name="Douyin",
    capabilities=frozenset({CapabilityKind.PROJECTION}),
    native_schemas=(NativeSchemaDescriptor(content_kind=SHORT_VIDEO_KIND, schema_version=1),),
    image_hosts=("douyinpic.com",),
    availability=ProviderAvailability.DEGRADED,
)
