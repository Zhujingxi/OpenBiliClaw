"""Immutable provider metadata and advertised contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

# Pydantic resolves these field types at runtime.
from .identity import ContentKind, ProviderId  # noqa: TC001


class CapabilityKind(StrEnum):
    SEARCH = "search"
    FEED = "feed"
    FETCH = "fetch"
    RELATED = "related"
    CREATOR = "creator"
    HISTORY = "history"
    SAVED = "saved"
    ACTION = "action"
    PROJECTION = "projection"
    OBSERVATION = "observation"


class ProviderAvailability(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class NativeSchemaDescriptor(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content_kind: ContentKind
    schema_version: int = Field(ge=1)


class ActionDescriptor(StrictBaseModel):
    """Safe provider action metadata; never contains executable instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=80)
    content_kind: ContentKind


class ProviderManifest(StrictBaseModel):
    """Frozen provider metadata validated before registry publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    display_name: str = Field(min_length=1, max_length=100)
    capabilities: frozenset[CapabilityKind]
    native_schemas: tuple[NativeSchemaDescriptor, ...]
    actions: tuple[ActionDescriptor, ...] = ()
    availability: ProviderAvailability

    @model_validator(mode="after")
    def _contract_consistency(self) -> ProviderManifest:
        schema_ids = tuple(
            (schema.content_kind, schema.schema_version) for schema in self.native_schemas
        )
        if len(schema_ids) != len(set(schema_ids)):
            raise ValueError("duplicate native schema identity")
        action_ids = tuple(action.action_id for action in self.actions)
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("duplicate action ID")
        if bool(self.actions) != (CapabilityKind.ACTION in self.capabilities):
            raise ValueError("action descriptors and advertised action capability must agree")
        return self
