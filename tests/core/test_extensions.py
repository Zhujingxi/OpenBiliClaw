from __future__ import annotations

import pytest

from openbiliclaw.core.extensions import (
    AccessMethodRegistration,
    AssistantSkillRegistration,
    ContentProviderRegistration,
    DiscoveryStrategyRegistration,
    EmbeddingProviderRegistration,
    ExtensionKind,
    ExtensionRegistry,
    ModelProviderRegistration,
    ObservationProviderRegistration,
    PresentationRegistration,
    UnderstandingAnalyzerRegistration,
)


def test_registry_accepts_each_approved_typed_extension_category() -> None:
    registrations = (
        ModelProviderRegistration("model.openai", capability_version=1),
        EmbeddingProviderRegistration("embedding.local", capability_version=1),
        ContentProviderRegistration("content.bilibili", capability_version=1),
        AccessMethodRegistration("access.anonymous", capability_version=1),
        ObservationProviderRegistration("observation.extension", capability_version=1),
        UnderstandingAnalyzerRegistration("understanding.preference", capability_version=1),
        DiscoveryStrategyRegistration("discovery.search", capability_version=1),
        AssistantSkillRegistration("assistant.recommend", capability_version=1),
        PresentationRegistration("presentation.cards", capability_version=1),
    )
    registry = ExtensionRegistry(supported_capability_version=1)

    for registration in registrations:
        registry.register(registration)

    assert registry.registrations == registrations
    assert registry.by_kind(ExtensionKind.MODEL_PROVIDER) == (registrations[0],)


def test_registry_rejects_duplicates_and_incompatible_versions() -> None:
    registry = ExtensionRegistry(supported_capability_version=1)
    registration = ModelProviderRegistration("model.openai", capability_version=1)
    registry.register(registration)

    with pytest.raises(ValueError, match="duplicate"):
        registry.register(registration)
    with pytest.raises(ValueError, match="version"):
        registry.register(ModelProviderRegistration("model.other", capability_version=2))


def test_extension_identifiers_are_validated() -> None:
    with pytest.raises(ValueError, match="supported_capability_version"):
        ExtensionRegistry(supported_capability_version=0)
    with pytest.raises(ValueError):
        ModelProviderRegistration("Not Valid", capability_version=1)
    with pytest.raises(ValueError):
        ModelProviderRegistration("model.valid", capability_version=0)
