"""Stable provider and content identity value objects."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel


class ProviderId(StrictBaseModel):
    """Stable lower-case provider identity; never a display name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    def __str__(self) -> str:
        return self.value


class ContentKind(StrictBaseModel):
    """Provider-declared stable content-kind identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")

    def __str__(self) -> str:
        return self.value


class ContentRef(StrictBaseModel):
    """Canonical cross-process identity for one provider-native record.

    URL normalization remains provider-owned. This layer validates and stores
    the provider's canonical HTTP(S) result without rewriting it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: ProviderId
    content_kind: ContentKind
    provider_content_id: str = Field(min_length=1, max_length=512)
    canonical_url: str = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
