"""LinuxDo provider manifest."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

LINUXDO_ID = ProviderId(value="linuxdo")
TOPIC_KIND = ContentKind(value="topic")
LINUXDO_MANIFEST = ProviderManifest(
    provider_id=LINUXDO_ID,
    display_name="LinuxDo",
    capabilities=frozenset(
        {CapabilityKind.SEARCH, CapabilityKind.FETCH, CapabilityKind.PROJECTION}
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=TOPIC_KIND, schema_version=1),),
    availability=ProviderAvailability.AVAILABLE,
)
