from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openbiliclaw.content.integration.identity import ContentKind, ContentRef, ProviderId
from openbiliclaw.content.integration.native import NativeContent
from openbiliclaw.content.integration.projections import (
    CardData,
    ContentPreview,
    ProjectionProvenance,
    RecommendationCandidate,
    SearchDocument,
)
from openbiliclaw.core._pydantic import StrictBaseModel


class Payload(StrictBaseModel):
    title: str
    views: int


def _ref() -> ContentRef:
    return ContentRef(
        provider_id=ProviderId(value="bilibili"),
        content_kind=ContentKind(value="video"),
        provider_content_id="BV1abc",
        canonical_url="https://www.bilibili.com/video/BV1abc",
    )


def _provenance() -> ProjectionProvenance:
    return ProjectionProvenance(ref=_ref(), native_schema_version=2, projected_at=datetime.now(UTC))


def test_identity_is_validated_hashable_and_json_stable() -> None:
    ref = _ref()
    restored = ContentRef.model_validate_json(ref.model_dump_json())
    assert restored == ref
    assert hash(restored) == hash(ref)
    assert len({ref, restored}) == 1
    assert (
        ProviderId.model_validate_json(ProviderId(value="github").model_dump_json()).value
        == "github"
    )
    script = (
        "import sys; "
        "from openbiliclaw.content.integration.identity import ContentRef; "
        "print(ContentRef.model_validate_json(sys.stdin.read()).model_dump_json())"
    )
    restarted = subprocess.run(
        [sys.executable, "-c", script],
        input=ref.model_dump_json(),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(restarted.stdout) == json.loads(ref.model_dump_json())
    with pytest.raises(ValidationError):
        ProviderId(value="Not valid")
    with pytest.raises(ValidationError):
        ContentKind(value="")
    with pytest.raises(ValidationError):
        ContentRef(
            provider_id=ProviderId(value="x"),
            content_kind=ContentKind(value="post"),
            provider_content_id="1",
            canonical_url="javascript:alert(1)",
        )


def test_native_content_requires_positive_schema_and_typed_payload() -> None:
    content = NativeContent(
        ref=_ref(),
        schema_version=2,
        payload=Payload(title="typed", views=3),
    )
    assert isinstance(content.payload, StrictBaseModel)
    assert "typed" in content.payload.model_dump_json()
    assert '"payload":{"title":"typed","views":3}' in content.model_dump_json()
    with pytest.raises(ValidationError):
        NativeContent(ref=_ref(), schema_version=0, payload=Payload(title="x", views=1))
    with pytest.raises(ValidationError):
        NativeContent.model_validate({"ref": _ref(), "schema_version": 1, "payload": {"raw": True}})


def test_projection_models_are_independent_and_require_provenance_and_source_time() -> None:
    now = datetime.now(UTC)
    provenance = _provenance()
    preview = ContentPreview(
        ref=_ref(),
        title="A",
        summary="B",
        creator_label="C",
        source_timestamp=now,
        provenance=provenance,
    )
    candidate = RecommendationCandidate(
        ref=_ref(),
        title="A",
        summary="B",
        discovery_reason="related",
        source_timestamp=now,
        provenance=provenance,
    )
    document = SearchDocument(
        ref=_ref(), title="A", body="searchable", source_timestamp=now, provenance=provenance
    )
    card = CardData(
        ref=_ref(),
        title="A",
        summary="B",
        badge="video",
        source_timestamp=now,
        provenance=provenance,
    )
    assert preview.title == candidate.title == document.title == card.title
    schemas = {
        "ContentPreview": json.dumps(ContentPreview.model_json_schema(), sort_keys=True),
        "RecommendationCandidate": json.dumps(
            RecommendationCandidate.model_json_schema(), sort_keys=True
        ),
        "SearchDocument": json.dumps(SearchDocument.model_json_schema(), sort_keys=True),
        "CardData": json.dumps(CardData.model_json_schema(), sort_keys=True),
    }
    assert '"discovery_reason"' in schemas["RecommendationCandidate"]
    assert '"discovery_reason"' not in schemas["CardData"]
    assert '"badge"' not in schemas["SearchDocument"]
    assert len(set(schemas.values())) == 4
    with pytest.raises(ValidationError):
        ContentPreview.model_validate({"ref": _ref(), "title": "missing provenance"})


def test_api_projection_schema_snapshot() -> None:
    def digest(schema: dict[str, object]) -> str:
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()

    assert {
        "ContentPreview": digest(ContentPreview.model_json_schema()),
        "RecommendationCandidate": digest(RecommendationCandidate.model_json_schema()),
        "SearchDocument": digest(SearchDocument.model_json_schema()),
        "CardData": digest(CardData.model_json_schema()),
    } == {
        "ContentPreview": "66a05b53601a9cdae3d5ebe24d841841a60c4cbec6b96211ee03f4155bc4cb7c",
        "RecommendationCandidate": (
            "9a6f264cfaecbbc90632e573c1a38c906b672cbdf8fecebf74d4511f8013eb32"
        ),
        "SearchDocument": "a290339775b5b53934849fd997e8996d247846dc3998f2d8f886d9ace7242a0c",
        "CardData": "d164eb97634b536e412b4cef35a4dfacdf41ab5862e1b36712bf2569958e2b15",
    }


def test_projection_summary_truncates_oversized_native_description() -> None:
    preview = ContentPreview(
        ref=_ref(),
        title="t",
        summary="x" * 20_000,
        creator_label=None,
        source_timestamp=datetime.now(UTC),
        provenance=_provenance(),
    )
    assert len(preview.summary) == 4000
