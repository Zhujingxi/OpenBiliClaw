from __future__ import annotations

import pytest

from openbiliclaw.sources.bangumi import (
    bangumi_collection_to_event,
    bangumi_subject_to_content,
    fetch_bangumi_public_collection_events,
)
from openbiliclaw.sources.bangumi_client import BangumiPage


def _subject(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": 326,
        "type": 2,
        "name": "Koukaku Kidoutai",
        "name_cn": "攻壳机动队",
        "summary": "未来社会的故事",
        "date": "2004-01-01",
        "nsfw": False,
        "images": {"common": "https://lain.bgm.tv/cover.jpg"},
        "meta_tags": ["TV"],
        "tags": [{"name": "科幻", "count": 99}, {"name": "tv", "count": 1}],
        "rating": {"score": 9.2, "total": 9959, "rank": 1},
        "collection": {"wish": 1, "collect": 2, "doing": 3, "on_hold": 4, "dropped": 5},
    }
    row.update(overrides)
    return row


def test_subject_normalization_maps_catalog_fields_without_fake_engagement() -> None:
    item = bangumi_subject_to_content(_subject(), strategy="bangumi-ranked", source_keyword_id=12)
    assert item is not None
    assert item.item_key == "bangumi:326"
    assert item.content_url == "https://bgm.tv/subject/326"
    assert item.content_type == "subject"
    assert item.title == "攻壳机动队"
    assert item.author_name == ""
    assert item.body_text == "未来社会的故事"
    assert item.cover_url == "https://lain.bgm.tv/cover.jpg"
    assert item.favorite_count == 15
    assert item.view_count == item.like_count == item.comment_count == 0
    assert item.rating_score == 9.2
    assert item.rating_count == 9959
    assert item.source_rank == 1
    assert item.tags == ["TV", "科幻"]
    assert item.source_keyword_id == 12


def test_slim_subject_fallbacks_and_numeric_guards() -> None:
    item = bangumi_subject_to_content(
        _subject(
            name_cn="",
            summary="",
            short_summary="short",
            images={"medium": "https://lain.bgm.tv/m.jpg"},
            rating=None,
            score="99",
            rank="-3",
            collection=None,
            collection_total="8",
        ),
        strategy="bangumi-latest",
    )
    assert item is not None
    assert item.title == "Koukaku Kidoutai"
    assert item.body_text == "short"
    assert item.cover_url.endswith("/m.jpg")
    assert item.favorite_count == 8
    assert item.rating_score == 10.0
    assert item.source_rank == 0


@pytest.mark.parametrize(
    "row",
    [
        _subject(nsfw=True),
        _subject(id=0),
        _subject(type=5),
        _subject(name="", name_cn=""),
    ],
)
def test_subject_normalization_drops_unsafe_or_malformed_rows(row: dict[str, object]) -> None:
    assert bangumi_subject_to_content(row, strategy="bangumi-search") is None


@pytest.mark.parametrize(
    ("rate", "collection_type", "event_type", "strength", "feedback_type"),
    [
        (8, 5, "like", 0.85, None),
        (4, 1, "feedback", 1.0, "dislike"),
        (0, 1, "favorite", 1.0, None),
        (0, 3, "favorite", 0.85, None),
        (0, 2, "view", 0.35, None),
        (0, 4, "view", 0.25, None),
        (0, 5, "feedback", 0.60, "dislike"),
    ],
)
def test_public_collection_signal_matrix(
    rate: int,
    collection_type: int,
    event_type: str,
    strength: float,
    feedback_type: str | None,
) -> None:
    event = bangumi_collection_to_event(
        {
            "subject_id": 326,
            "type": collection_type,
            "rate": rate,
            "comment": "good\u0000" * 100,
            "updated_at": "2026-01-01T00:00:00Z",
            "private": False,
            "subject": {
                "id": 326,
                "type": 2,
                "name": "Title",
                "score": 9.2,
                "rank": 1,
            },
        },
        username="sai",
    )
    assert event is not None
    assert event["event_type"] == event_type
    assert event["metadata"]["signal_strength"] == strength
    assert event["metadata"].get("feedback_type") == feedback_type
    assert event["metadata"]["source_updated_at"] == "2026-01-01T00:00:00Z"
    assert "timestamp" not in event["metadata"]
    assert "\u0000" not in event["metadata"]["collection_comment"]
    assert len(event["metadata"]["collection_comment"]) <= 200


def test_private_collection_is_never_imported() -> None:
    assert (
        bangumi_collection_to_event(
            {
                "subject_id": 1,
                "type": 1,
                "private": True,
                "subject": {"id": 1, "type": 2, "name": "Private"},
            },
            username="sai",
        )
        is None
    )


@pytest.mark.asyncio
async def test_public_collection_fetch_balances_status_and_subject_type() -> None:
    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str]] = []

        async def get_user_collections(
            self,
            username: str,
            *,
            collection_type: int,
            subject_type: str,
            limit: int,
            offset: int,
        ) -> BangumiPage:
            self.calls.append((collection_type, subject_type))
            type_id = {"anime": 2, "book": 1}[subject_type]
            subject_id = collection_type * 10 + type_id
            return BangumiPage(
                [
                    {
                        "subject_id": subject_id,
                        "type": collection_type,
                        "private": False,
                        "subject": {
                            "id": subject_id,
                            "type": type_id,
                            "name": f"subject-{subject_id}",
                        },
                    }
                ],
                total=1,
                limit=limit,
                offset=offset,
            )

    client = _Client()
    events = await fetch_bangumi_public_collection_events(
        client,
        username="sai",
        subject_types=("anime", "book"),
        limit=10,
    )

    assert len(events) == 10
    assert set(client.calls) == {
        (collection_type, subject_type)
        for collection_type in range(1, 6)
        for subject_type in ("anime", "book")
    }
