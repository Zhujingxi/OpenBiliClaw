"""RedNote identity; session-bound reads are deliberately not advertised."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

REDNOTE_ID = ProviderId(value="rednote")
NOTE_KIND = ContentKind(value="note")

REDNOTE_MANIFEST = ProviderManifest(
    provider_id=REDNOTE_ID,
    display_name="RedNote",
    capabilities=frozenset({CapabilityKind.PROJECTION}),
    native_schemas=(NativeSchemaDescriptor(content_kind=NOTE_KIND, schema_version=1),),
    image_hosts=("xhscdn.com",),
    availability=ProviderAvailability.DEGRADED,
)
