"""Versioned, purpose-specific bounded profile projections."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

from .profile import (
    AvoidanceClaim,
    CanonicalProfile,
    InsightClaim,
    PreferenceClaim,
    StableInterestClaim,
)


class DiscoveryProfile(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = 1
    interests: tuple[str, ...] = Field(max_length=30)
    avoidances: tuple[str, ...] = Field(max_length=20)
    provider_preferences: tuple[str, ...] = Field(max_length=10)


class RecommendationProfile(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = 1
    positive_topics: tuple[str, ...] = Field(max_length=30)
    negative_topics: tuple[str, ...] = Field(max_length=20)
    style_preferences: tuple[str, ...] = Field(max_length=10)
    language_preferences: tuple[str, ...] = Field(max_length=10)


class DialogueProfile(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = 1
    preference_summary: tuple[str, ...] = Field(max_length=30)
    insights: tuple[str, ...] = Field(max_length=10)


def _bounded(items: tuple[str, ...], max_chars: int) -> tuple[str, ...]:
    if max_chars < 1:
        raise ValueError("projection character budget must be positive")
    result: list[str] = []
    used = 0
    for item in items:
        remaining = max_chars - used
        if remaining <= 0:
            break
        value = item[:remaining]
        if value:
            result.append(value)
            used += len(value)
    return tuple(result)


def discovery_projection(profile: CanonicalProfile, *, max_chars: int = 2_000) -> DiscoveryProfile:
    interests = tuple(
        item.value for item in profile.claims if isinstance(item, StableInterestClaim)
    )
    avoidances = tuple(item.value for item in profile.claims if isinstance(item, AvoidanceClaim))
    providers = tuple(
        item.value
        for item in profile.claims
        if isinstance(item, PreferenceClaim) and item.dimension.value == "provider"
    )
    return DiscoveryProfile(
        interests=_bounded(interests, max_chars),
        avoidances=_bounded(avoidances, max_chars),
        provider_preferences=_bounded(providers, max_chars),
    )


def recommendation_projection(
    profile: CanonicalProfile, *, max_chars: int = 3_000
) -> RecommendationProfile:
    positive = tuple(item.value for item in profile.claims if isinstance(item, StableInterestClaim))
    negative = tuple(item.value for item in profile.claims if isinstance(item, AvoidanceClaim))
    styles = tuple(
        item.value
        for item in profile.claims
        if isinstance(item, PreferenceClaim) and item.dimension.value == "style"
    )
    languages = tuple(
        item.value
        for item in profile.claims
        if isinstance(item, PreferenceClaim) and item.dimension.value == "language"
    )
    return RecommendationProfile(
        positive_topics=_bounded(positive, max_chars),
        negative_topics=_bounded(negative, max_chars),
        style_preferences=_bounded(styles, max_chars),
        language_preferences=_bounded(languages, max_chars),
    )


def dialogue_projection(profile: CanonicalProfile, *, max_chars: int = 4_000) -> DialogueProfile:
    preferences = tuple(
        f"{item.dimension.value}: {item.value}"
        for item in profile.claims
        if isinstance(item, PreferenceClaim)
    )
    insights = tuple(item.value for item in profile.claims if isinstance(item, InsightClaim))
    return DialogueProfile(
        preference_summary=_bounded(preferences, max_chars),
        insights=_bounded(insights, max_chars),
    )
