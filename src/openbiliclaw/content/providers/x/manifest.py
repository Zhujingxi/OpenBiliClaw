"""X provider manifest."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

X_ID = ProviderId(value="x")
POST_KIND = ContentKind(value="post")
X_MANIFEST = ProviderManifest(
    provider_id=X_ID,
    display_name="X",
    capabilities=frozenset(
        {CapabilityKind.SEARCH, CapabilityKind.FETCH, CapabilityKind.PROJECTION}
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=POST_KIND, schema_version=1),),
    availability=ProviderAvailability.AVAILABLE,
)
