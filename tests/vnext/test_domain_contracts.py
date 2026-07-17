"""Characterization tests for the frozen vNext domain boundary."""

from datetime import UTC, datetime, timedelta
from typing import Any, get_type_hints
from uuid import UUID

import pytest
from pydantic import BaseModel, HttpUrl, JsonValue, ValidationError

from openbiliclaw.features.activity.domain import ActivityEvent, ActivityKind, ProfileSignal
from openbiliclaw.features.chat.domain import ChatRole, ChatTurn
from openbiliclaw.features.feed.domain import (
    CandidateAssessment,
    ContentItem,
    FeedEntry,
    Interaction,
    InteractionKind,
    feed_deficit,
)
from openbiliclaw.features.library.domain import CollectionItem, CollectionKind
from openbiliclaw.features.profile.domain import (
    ProfileDelta,
    ProfileFacet,
    ProfileSnapshot,
    apply_profile_delta,
)
from openbiliclaw.features.sources.domain import (
    SourceCapability,
    SourceConnector,
    SourceManifest,
    SourceOperation,
    SourceOperationSpec,
    SourceResultKind,
    SourceTransportKind,
)

NOW = datetime(2026, 7, 17, 8, 30, tzinfo=UTC)
EVENT_ID = UUID("00000000-0000-0000-0000-000000000001")
CONTENT_ID = UUID("00000000-0000-0000-0000-000000000002")
ASSESSMENT_ID = UUID("00000000-0000-0000-0000-000000000003")
PROFILE_ID = UUID("00000000-0000-0000-0000-000000000004")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000005")
CONVERSATION_ID = UUID("00000000-0000-0000-0000-000000000006")
BILI_URL = HttpUrl("https://www.bilibili.com/video/BV1contract")
METADATA_URL = HttpUrl("https://www.bilibili.com/video/BV1metadata")


def _contracts() -> tuple[BaseModel, ...]:
    facet = ProfileFacet(
        name="interests",
        value="Python",
        weight=0.8,
        confidence=0.9,
        evidence_ids=(EVENT_ID,),
    )
    return (
        ActivityEvent(
            id=EVENT_ID,
            source_id="bilibili",
            account_id=ACCOUNT_ID,
            kind=ActivityKind.VIEW,
            occurred_at=NOW,
            content_external_id="BV1contract",
            url=BILI_URL,
            title="Domain contracts",
            text="watched",
            duration_seconds=42.5,
            metadata={"progress": 0.75},
        ),
        ProfileSignal(
            facet="interests",
            value="Python",
            weight=0.8,
            confidence=0.9,
            evidence_ids=(EVENT_ID,),
        ),
        facet,
        ProfileSnapshot(
            id=PROFILE_ID,
            revision=3,
            narrative="Builds reliable systems.",
            facets=(facet,),
            confidence=0.9,
            created_at=NOW,
        ),
        ProfileDelta(narrative="Updated", upserts=(facet,)),
        ContentItem(
            id=CONTENT_ID,
            source_id="bilibili",
            external_id="BV1contract",
            url=BILI_URL,
            title="Domain contracts",
            summary="A focused introduction.",
            creator="OpenBiliClaw",
            published_at=NOW,
            media_type="video",
            metadata={"duration": 600},
        ),
        CandidateAssessment(
            id=ASSESSMENT_ID,
            content_id=CONTENT_ID,
            profile_revision=3,
            relevance=0.9,
            quality=0.8,
            novelty=0.7,
            risk=0.1,
            topics=("python", "architecture"),
            explanation="Strong match.",
        ),
        FeedEntry(
            id=UUID("00000000-0000-0000-0000-000000000007"),
            content_id=CONTENT_ID,
            assessment_id=ASSESSMENT_ID,
            position=2,
            admitted_at=NOW,
            explanation="Because you value maintainability.",
        ),
        Interaction(
            id=UUID("00000000-0000-0000-0000-000000000008"),
            content_id=CONTENT_ID,
            kind=InteractionKind.POSITIVE,
            occurred_at=NOW,
            metadata={"surface": "web"},
        ),
        CollectionItem(
            id=UUID("00000000-0000-0000-0000-000000000009"),
            collection=CollectionKind.FAVORITES,
            content_id=CONTENT_ID,
            added_at=NOW,
            note="Revisit this.",
        ),
        ChatTurn(
            id=UUID("00000000-0000-0000-0000-000000000010"),
            conversation_id=CONVERSATION_ID,
            role=ChatRole.ASSISTANT,
            content="Here is why this fits.",
            created_at=NOW,
            ai_run_id=UUID("00000000-0000-0000-0000-000000000011"),
        ),
        SourceManifest(
            source_id="bilibili",
            display_name="Bilibili",
            capabilities=frozenset(
                {SourceCapability.AUTHENTICATION, SourceCapability.BOOTSTRAP_IMPORT}
            ),
            operations=(
                SourceOperationSpec(
                    operation=SourceOperation.BOOTSTRAP_IMPORT,
                    capability=SourceCapability.BOOTSTRAP_IMPORT,
                    result_kind=SourceResultKind.ACTIVITY,
                    requires_auth=True,
                    transport_kind=SourceTransportKind.DIRECT,
                ),
            ),
        ),
    )


def _metadata_contracts() -> tuple[ActivityEvent, ContentItem, Interaction]:
    metadata: dict[str, JsonValue] = {"nested": {"enabled": True, "labels": ["stable", "json"]}}
    return (
        ActivityEvent(
            source_id="bilibili",
            kind=ActivityKind.VIEW,
            metadata=metadata,
        ),
        ContentItem(
            source_id="bilibili",
            external_id="BV1metadata",
            url=METADATA_URL,
            title="Immutable metadata",
            metadata=metadata,
        ),
        Interaction(
            content_id=CONTENT_ID,
            kind=InteractionKind.OPEN,
            metadata=metadata,
        ),
    )


@pytest.mark.parametrize("contract", _contracts(), ids=lambda value: type(value).__name__)
def test_frozen_contracts_json_round_trip(contract: BaseModel) -> None:
    restored = type(contract).model_validate_json(contract.model_dump_json())

    assert restored == contract
    with pytest.raises(ValidationError, match="frozen"):
        contract.id = UUID(int=0)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("contract", "field"),
    [
        (
            ProfileSignal(
                facet="interests",
                value="Python",
                weight=0.8,
                confidence=0.9,
                evidence_ids=(EVENT_ID,),
            ),
            "value",
        ),
        (
            ProfileDelta(
                narrative="Updated",
                removals=(("interests", "Python"),),
            ),
            "narrative",
        ),
    ],
)
def test_frozen_contracts_without_ids_reject_assignment(contract: BaseModel, field: str) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        setattr(contract, field, "changed")


@pytest.mark.parametrize("contract", _metadata_contracts(), ids=lambda value: type(value).__name__)
def test_metadata_is_recursively_immutable(
    contract: ActivityEvent | ContentItem | Interaction,
) -> None:
    metadata: Any = contract.metadata

    with pytest.raises(TypeError):
        metadata["added"] = "not allowed"
    with pytest.raises(TypeError):
        metadata["nested"]["enabled"] = False
    with pytest.raises(TypeError):
        metadata["nested"]["labels"][0] = "changed"


@pytest.mark.parametrize(
    "invalid_metadata",
    [
        {"unsupported": object()},
        {"unsupported": b"bytes"},
        {"unsupported": {"not", "an", "array"}},
        {"unsupported": float("nan")},
        {"unsupported": {1: "non-string key"}},
    ],
    ids=["object", "bytes", "set", "non-finite", "non-string-key"],
)
@pytest.mark.parametrize("contract", _metadata_contracts(), ids=lambda value: type(value).__name__)
def test_metadata_rejects_non_json_values(
    contract: ActivityEvent | ContentItem | Interaction,
    invalid_metadata: object,
) -> None:
    payload = contract.model_dump(mode="python")
    payload["metadata"] = invalid_metadata

    with pytest.raises(ValidationError, match="metadata"):
        type(contract).model_validate(payload)


@pytest.mark.parametrize("contract_type", [ProfileSignal, ProfileFacet])
def test_profile_evidence_is_mandatory(contract_type: type[BaseModel]) -> None:
    with pytest.raises(ValidationError, match="evidence_ids"):
        contract_type.model_validate(
            {
                "facet" if contract_type is ProfileSignal else "name": "interests",
                "value": "Python",
                "weight": 0.8,
                "confidence": 0.9,
                "evidence_ids": (),
            }
        )


def test_user_override_has_full_confidence_and_cannot_be_silently_removed() -> None:
    override = ProfileFacet(
        name="avoidances",
        value="Clickbait",
        weight=-1,
        confidence=0.2,
        evidence_ids=(EVENT_ID,),
        overridden=True,
    )
    snapshot = ProfileSnapshot(revision=4, facets=(override,))

    updated = apply_profile_delta(
        snapshot,
        ProfileDelta(removals=(("avoidances", "CLICKBAIT"),)),
    )

    assert override.confidence == 1.0
    assert updated.facets == (override,)
    assert updated.revision == 5


def test_user_override_signal_always_has_full_confidence() -> None:
    signal = ProfileSignal(
        facet="interests",
        value="Testing",
        weight=1,
        confidence=0,
        evidence_ids=(EVENT_ID,),
        override=True,
    )

    assert signal.confidence == 1.0


def test_duplicate_facets_merge_case_insensitively_with_all_evidence() -> None:
    second_event = UUID("00000000-0000-0000-0000-000000000012")
    snapshot = ProfileSnapshot(
        revision=1,
        facets=(
            ProfileFacet(
                name="interests",
                value="Python",
                weight=0.4,
                confidence=0.5,
                evidence_ids=(EVENT_ID,),
            ),
        ),
    )
    delta = ProfileDelta(
        upserts=(
            ProfileFacet(
                name="interests",
                value="python",
                weight=0.8,
                confidence=0.75,
                evidence_ids=(second_event,),
            ),
        )
    )

    updated = apply_profile_delta(snapshot, delta)

    assert len(updated.facets) == 1
    assert updated.facets[0].evidence_ids == (EVENT_ID, second_event)
    assert updated.facets[0].confidence == 0.75
    assert updated.facets[0].weight == pytest.approx(0.64)


def test_current_override_keeps_semantics_and_merges_proposal_evidence() -> None:
    proposal_event = UUID("00000000-0000-0000-0000-000000000013")
    override = ProfileFacet(
        name="interests",
        value="Python",
        weight=1,
        confidence=1,
        evidence_ids=(EVENT_ID,),
        overridden=True,
    )
    proposal = ProfileFacet(
        name="interests",
        value="python",
        weight=-1,
        confidence=0.9,
        evidence_ids=(proposal_event,),
    )

    updated = apply_profile_delta(
        ProfileSnapshot(revision=7, facets=(override,)),
        ProfileDelta(upserts=(proposal,)),
    )

    merged = updated.facets[0]
    assert merged.value == override.value
    assert merged.weight == override.weight
    assert merged.confidence == 1.0
    assert merged.overridden is True
    assert merged.evidence_ids == (EVENT_ID, proposal_event)


def test_proposed_override_keeps_semantics_and_merges_current_evidence() -> None:
    override_event = UUID("00000000-0000-0000-0000-000000000014")
    current = ProfileFacet(
        name="avoidances",
        value="Spoilers",
        weight=-0.4,
        confidence=0.6,
        evidence_ids=(EVENT_ID,),
    )
    override = ProfileFacet(
        name="avoidances",
        value="spoilers",
        weight=-1,
        confidence=0.1,
        evidence_ids=(override_event,),
        overridden=True,
    )

    updated = apply_profile_delta(
        ProfileSnapshot(revision=2, facets=(current,)),
        ProfileDelta(upserts=(override,)),
    )

    merged = updated.facets[0]
    assert merged.value == override.value
    assert merged.weight == override.weight
    assert merged.confidence == 1.0
    assert merged.overridden is True
    assert merged.evidence_ids == (EVENT_ID, override_event)


def test_apply_profile_delta_is_deterministic() -> None:
    snapshot = ProfileSnapshot(
        id=PROFILE_ID,
        revision=2,
        narrative="Original",
        created_at=NOW,
    )
    delta = ProfileDelta(
        upserts=(
            ProfileFacet(
                name="values",
                value="Evidence",
                weight=0.9,
                confidence=0.8,
                evidence_ids=(EVENT_ID,),
            ),
        )
    )

    revision_time = NOW + timedelta(seconds=1)
    first = apply_profile_delta(snapshot, delta, created_at=revision_time)
    second = apply_profile_delta(snapshot, delta, created_at=revision_time)

    assert first == second
    assert first.id == PROFILE_ID
    assert first.created_at == revision_time
    assert first.created_at > snapshot.created_at


@pytest.mark.parametrize("contract_type", [ActivityEvent, ContentItem])
def test_public_urls_strip_sensitive_query_credentials(contract_type: type[BaseModel]) -> None:
    url = (
        "https://www.xiaohongshu.com/explore/note?keep=1&xsec_token=secret"
        "&access_token=secret&Cookie=secret&session_id=secret#section"
    )
    if contract_type is ActivityEvent:
        contract = ActivityEvent(source_id="xiaohongshu", kind="view", url=url)
    else:
        contract = ContentItem(
            source_id="xiaohongshu",
            external_id="note",
            url=url,
            title="Note",
        )

    serialized_url = str(contract.url)
    assert "keep=1" in serialized_url
    assert serialized_url.endswith("#section")
    assert "secret" not in serialized_url
    assert "xsec_token" not in serialized_url
    assert "access_token" not in serialized_url
    assert "Cookie" not in serialized_url
    assert "session_id" not in serialized_url


@pytest.mark.parametrize("contract_type", [ActivityEvent, ContentItem])
def test_public_urls_strip_userinfo_signatures_and_secret_fragments(
    contract_type: type[BaseModel],
) -> None:
    url = (
        " \thttps://alice:password@example.com/item?keep=1"
        "&X-Amz-Signature=query-secret"
        "#section=comments&access_token=fragment-secret"
    )
    if contract_type is ActivityEvent:
        contract = ActivityEvent(source_id="youtube", kind="view", url=url)
    else:
        contract = ContentItem(
            source_id="youtube",
            external_id="item",
            url=url,
            title="Item",
        )

    serialized_url = str(contract.url)
    assert serialized_url == "https://example.com/item?keep=1#section=comments"
    assert "alice" not in serialized_url
    assert "password" not in serialized_url
    assert "query-secret" not in serialized_url
    assert "fragment-secret" not in serialized_url


def test_candidate_assessment_scores_clamp_to_unit_interval() -> None:
    assessment = CandidateAssessment(
        content_id=CONTENT_ID,
        profile_revision=1,
        relevance=1.5,
        quality=-0.25,
        novelty=2,
        risk=-1,
    )

    assert assessment.relevance == 1.0
    assert assessment.quality == 0.0
    assert assessment.novelty == 1.0
    assert assessment.risk == 0.0
    assert assessment.score == 0.75


@pytest.mark.parametrize(
    ("current_unseen", "expected"),
    [(4, 16), (5, 0), (6, 0), (20, 0)],
)
def test_feed_deficit_replenishes_only_below_low_watermark(
    current_unseen: int, expected: int
) -> None:
    assert feed_deficit(current_unseen, low_watermark=5, high_watermark=20) == expected


@pytest.mark.parametrize(
    ("current_unseen", "low_watermark", "high_watermark"),
    [(-1, 5, 20), (1, -1, 20), (1, 5, -1), (1, 21, 20)],
)
def test_feed_deficit_rejects_invalid_watermarks(
    current_unseen: int, low_watermark: int, high_watermark: int
) -> None:
    with pytest.raises(ValueError):
        feed_deficit(current_unseen, low_watermark, high_watermark)


def test_source_connector_is_a_runtime_checkable_capability_contract() -> None:
    class FakeConnector:
        manifest = SourceManifest(
            source_id="bilibili",
            display_name="Test",
            capabilities=frozenset({SourceCapability.SEARCH}),
            operations=(
                SourceOperationSpec(
                    operation=SourceOperation.SEARCH,
                    capability=SourceCapability.SEARCH,
                    result_kind=SourceResultKind.CONTENT,
                    requires_auth=False,
                    transport_kind=SourceTransportKind.DIRECT,
                ),
            ),
        )

        async def execute(
            self, operation: SourceOperation, query: str | None = None, limit: int = 20
        ) -> tuple[ContentItem, ...]:
            del operation, query, limit
            return ()

    connector: Any = FakeConnector()

    assert isinstance(connector, SourceConnector)


def test_source_connector_annotations_resolve_at_runtime() -> None:
    execute_hints = get_type_hints(SourceConnector.execute)

    assert execute_hints["return"] == tuple[ActivityEvent, ...] | tuple[ContentItem, ...]
