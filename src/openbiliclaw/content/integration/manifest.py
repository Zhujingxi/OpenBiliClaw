"""Immutable provider metadata and advertised contracts."""

from __future__ import annotations

import re
from enum import StrEnum
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

# Pydantic resolves these field types at runtime.
from .identity import (  # noqa: TC001  # Runtime type required by Pydantic model fields.
    ContentKind,
    ProviderId,
)


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


class BiasClass(StrEnum):
    """Declared source bias for a provider feed channel."""

    PLATFORM_POPULARITY = "platform-popularity"
    PLATFORM_PERSONALIZED = "platform-personalized"
    SUBSCRIPTION_GRAPH = "subscription-graph"
    EDITORIAL = "editorial"


class ChannelDescriptor(StrictBaseModel):
    """One concrete provider feed and the bias it introduces."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feed_id: str = Field(min_length=1, max_length=512)
    bias_class: BiasClass
    auth_required: bool


class AccessArtifactKind(StrEnum):
    """Generic browser primitive required by a credential recipe."""

    COOKIE = "cookie"
    LOCAL_STORAGE = "local_storage"
    SESSION_STORAGE = "session_storage"


class AccessArtifact(StrictBaseModel):
    """One declarative browser artifact; never contains a credential value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: AccessArtifactKind
    domain: str = Field(min_length=1, max_length=253)
    name: str = Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")


class AccessRecipe(StrictBaseModel):
    """Provider-owned data recipe consumed by the generic browser extension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domains: tuple[str, ...] = Field(min_length=1)
    artifacts: tuple[AccessArtifact, ...] = Field(min_length=1)
    warmup_url: str | None = Field(default=None, max_length=2048)
    target_method_id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")

    @model_validator(mode="after")
    def _pure_data_contract(self) -> AccessRecipe:
        if len(self.domains) != len(set(self.domains)):
            raise ValueError("duplicate access recipe domain")
        for domain in self.domains:
            parsed = urlsplit(f"https://{domain}")
            labels = domain.split(".")
            if (
                domain != domain.lower()
                or domain.endswith(".")
                or parsed.hostname != domain
                or parsed.netloc != domain
                or len(domain) > 253
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                )
            ):
                raise ValueError("access recipe domains must be normalized DNS hostnames")
            try:
                ip_address(domain)
            except ValueError:
                pass
            else:
                raise ValueError("access recipe domains must not be IP literals")
        identities = tuple((item.kind, item.domain, item.name) for item in self.artifacts)
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate access recipe artifact")
        if any(item.domain not in self.domains for item in self.artifacts):
            raise ValueError("access recipe artifact domain must be declared")
        if self.warmup_url is not None:
            warmup = urlsplit(self.warmup_url)
            if warmup.scheme != "https" or warmup.hostname not in self.domains:
                raise ValueError("access recipe warmup URL must use a declared HTTPS domain")
            if warmup.username or warmup.password or warmup.fragment:
                raise ValueError("access recipe warmup URL contains forbidden components")
        return self


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
    channels: tuple[ChannelDescriptor, ...] = ()
    image_hosts: tuple[str, ...] = ()
    image_headers: dict[str, str] = Field(default_factory=dict)
    access_recipe: AccessRecipe | None = None
    availability: ProviderAvailability

    @field_validator("image_hosts")
    @classmethod
    def _valid_image_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        for host in hosts:
            parsed = urlsplit(f"https://{host}")
            labels = host.split(".")
            if (
                host != host.lower()
                or host.endswith(".")
                or parsed.hostname != host
                or parsed.netloc != host
                or len(host) > 253
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                )
            ):
                raise ValueError("image hosts must be normalized DNS hostnames")
            try:
                ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("image hosts must not be IP literals")
        if len(hosts) != len(set(hosts)):
            raise ValueError("duplicate image host")
        return hosts

    @field_validator("image_headers")
    @classmethod
    def _valid_image_headers(cls, headers: dict[str, str]) -> dict[str, str]:
        for name, value in headers.items():
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", name)
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("invalid static image header")
        return headers

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
        feed_ids = tuple(channel.feed_id for channel in self.channels)
        if len(feed_ids) != len(set(feed_ids)):
            raise ValueError("duplicate channel feed_id")
        if bool(self.channels) != (CapabilityKind.FEED in self.capabilities):
            raise ValueError("channel declarations and advertised feed capability must agree")
        if self.image_headers and not self.image_hosts:
            raise ValueError("image headers require declared image hosts")
        return self

    def channel(self, feed_id: str) -> ChannelDescriptor:
        """Return one declared feed channel or raise for an unsupported feed ID."""

        for channel in self.channels:
            if channel.feed_id == feed_id:
                return channel
        raise KeyError(feed_id)
