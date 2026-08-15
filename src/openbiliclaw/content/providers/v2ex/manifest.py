"""V2EX identity and implemented contracts."""

from openbiliclaw.content.integration.identity import ContentKind, ProviderId
from openbiliclaw.content.integration.manifest import (
    BiasClass,
    CapabilityKind,
    ChannelDescriptor,
    NativeSchemaDescriptor,
    ProviderAvailability,
    ProviderManifest,
)

V2EX_ID = ProviderId(value="v2ex")
TOPIC_KIND = ContentKind(value="topic")
V2EX_MANIFEST = ProviderManifest(
    provider_id=V2EX_ID,
    display_name="V2EX",
    capabilities=frozenset(
        {
            CapabilityKind.FEED,
            CapabilityKind.FETCH,
            CapabilityKind.CREATOR,
            CapabilityKind.PROJECTION,
        }
    ),
    native_schemas=(NativeSchemaDescriptor(content_kind=TOPIC_KIND, schema_version=1),),
    channels=(
        ChannelDescriptor(
            feed_id="hot",
            bias_class=BiasClass.PLATFORM_POPULARITY,
            auth_required=False,
        ),
    ),
    availability=ProviderAvailability.AVAILABLE,
)
