"""Zhihu provider manifest."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    CapabilityKind,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

ZHIHU_ID = ProviderId(value="zhihu")
ANSWER_KIND = ContentKind(value="answer")
ZHIHU_MANIFEST = ProviderManifest(
    provider_id=ZHIHU_ID,
    display_name="Zhihu",
    capabilities=frozenset(
        {CapabilityKind.SEARCH, CapabilityKind.FETCH, CapabilityKind.PROJECTION}
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=ANSWER_KIND, schema_version=1),),
    availability=ProviderAvailability.AVAILABLE,
)
