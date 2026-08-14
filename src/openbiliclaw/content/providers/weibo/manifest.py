"""Weibo provider manifest."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

WEIBO_ID = ProviderId(value="weibo")
POST_KIND = ContentKind(value="post")
WEIBO_MANIFEST = ProviderManifest(
    provider_id=WEIBO_ID,
    display_name="Weibo",
    capabilities=frozenset({CapabilityKind.SEARCH, CapabilityKind.PROJECTION}),
    native_schemas=(NativeSchemaDescriptor(content_kind=POST_KIND, schema_version=1),),
    availability=ProviderAvailability.AVAILABLE,
)
