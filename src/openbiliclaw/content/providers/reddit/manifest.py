"""Reddit provider manifest."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

REDDIT_ID = ProviderId(value="reddit")
POST_KIND = ContentKind(value="post")
REDDIT_MANIFEST = ProviderManifest(
    provider_id=REDDIT_ID,
    display_name="Reddit",
    capabilities=frozenset(
        {CapabilityKind.SEARCH, CapabilityKind.FETCH, CapabilityKind.PROJECTION}
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=POST_KIND, schema_version=1),),
    availability=ProviderAvailability.AVAILABLE,
)
