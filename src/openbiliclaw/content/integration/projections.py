"""Independent purpose-specific cross-provider projections."""

from __future__ import annotations

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from openbiliclaw.core._pydantic import StrictBaseModel

from .identity import (
    ContentRef,  # noqa: TC001  # Pydantic resolves field types at runtime.  # Runtime type required by Pydantic model fields.
)


class ProjectionProvenance(StrictBaseModel):
    """Native source identity and projection time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    native_schema_version: int = Field(ge=1)
    projected_at: AwareDatetime


class ContentPreview(StrictBaseModel):
    """Small read/tool preview; not a recommendation or card schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(max_length=4000)

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, value: object) -> object:
        # Native payloads may carry longer descriptions; summaries are
        # display projections, so clamp instead of rejecting valid content.
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000]
        return value

    creator_label: str | None = Field(default=None, max_length=300)
    image_url: str | None = Field(default=None, pattern=r"^https?://[^\s]+$", max_length=2048)
    source_timestamp: AwareDatetime | None = None
    provenance: ProjectionProvenance

    @model_validator(mode="after")
    def _matching_provenance(self) -> ContentPreview:
        if self.provenance.ref != self.ref:
            raise ValueError("projection provenance does not match content reference")
        return self


class RecommendationCandidate(StrictBaseModel):
    """Recommendation inventory projection with discovery provenance only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(max_length=4000)

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, value: object) -> object:
        # Native payloads may carry longer descriptions; summaries are
        # display projections, so clamp instead of rejecting valid content.
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000]
        return value

    discovery_reason: str = Field(min_length=1, max_length=500)
    source_timestamp: AwareDatetime | None = None
    provenance: ProjectionProvenance

    @model_validator(mode="after")
    def _matching_provenance(self) -> RecommendationCandidate:
        if self.provenance.ref != self.ref:
            raise ValueError("projection provenance does not match content reference")
        return self


class SearchDocument(StrictBaseModel):
    """Search-index projection without presentation or ranking fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100_000)
    source_timestamp: AwareDatetime | None = None
    provenance: ProjectionProvenance

    @model_validator(mode="after")
    def _matching_provenance(self) -> SearchDocument:
        if self.provenance.ref != self.ref:
            raise ValueError("projection provenance does not match content reference")
        return self


class CardData(StrictBaseModel):
    """Presentation-ready content data without recommendation policy fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ContentRef
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(max_length=4000)

    @field_validator("summary", mode="before")
    @classmethod
    def _truncate_summary(cls, value: object) -> object:
        # Native payloads may carry longer descriptions; summaries are
        # display projections, so clamp instead of rejecting valid content.
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000]
        return value

    badge: str | None = Field(default=None, max_length=100)
    image_url: str | None = Field(default=None, pattern=r"^https?://[^\s]+$", max_length=2048)
    source_timestamp: AwareDatetime | None = None
    provenance: ProjectionProvenance

    @model_validator(mode="after")
    def _matching_provenance(self) -> CardData:
        if self.provenance.ref != self.ref:
            raise ValueError("projection provenance does not match content reference")
        return self
