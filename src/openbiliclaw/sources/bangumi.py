"""Bangumi subject and public-collection normalization."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.sources.event_format import sanitize_comment_text

VALID_SUBJECT_TYPE_IDS = frozenset({1, 2, 3, 4, 6})
# Bangumi's v0 API caps a page at 50 rows. Normal bootstrap targets request the
# full cap and buffer surplus rows; smaller global targets avoid over-fetching.
BANGUMI_MAX_PAGE_SIZE = 50
COLLECTION_TYPES = {
    1: "wish",
    2: "done",
    3: "doing",
    4: "on_hold",
    5: "dropped",
}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(cast("Any", value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _rating(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(cast("Any", value or 0.0))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(10.0, max(0.0, number))


def _cover_url(subject: Mapping[str, Any]) -> str:
    images = _mapping(subject.get("images"))
    for key in ("common", "medium", "large", "grid", "small"):
        value = str(images.get(key) or "").strip()
        if value:
            return value
    return ""


def _subject_tags(subject: Mapping[str, Any]) -> list[str]:
    candidates: list[tuple[int, str]] = []
    # ``meta_tags`` must be a JSON array. A schema drift that hands back a bare
    # string (e.g. "TV") would otherwise be walked character-by-character, so
    # only iterate genuine lists — mirroring the ``tags`` guard below.
    meta_tags = subject.get("meta_tags")
    if isinstance(meta_tags, list):
        for raw in meta_tags:
            text = str(raw or "").strip()
            if text:
                candidates.append((2**31 - 1, text))
    raw_tags = subject.get("tags") or []
    if isinstance(raw_tags, list):
        for raw in raw_tags:
            if not isinstance(raw, Mapping):
                continue
            text = str(raw.get("name") or "").strip()
            if text:
                candidates.append((_non_negative_int(raw.get("count")), text))
    candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
    seen: set[str] = set()
    result: list[str] = []
    for _, text in candidates:
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= 20:
            break
    return result


def _collection_total(subject: Mapping[str, Any]) -> int:
    explicit = _non_negative_int(subject.get("collection_total"))
    if explicit:
        return explicit
    collection = _mapping(subject.get("collection"))
    return sum(
        _non_negative_int(collection.get(key))
        for key in ("wish", "collect", "doing", "on_hold", "dropped")
    )


def bangumi_subject_to_content(
    row: Mapping[str, Any],
    *,
    strategy: str,
    source_keyword_id: int | None = None,
) -> DiscoveredContent | None:
    """Normalize one official Subject/SlimSubject into discovery content."""

    if row.get("nsfw") is True:
        return None
    subject_id = _non_negative_int(row.get("id"))
    subject_type = _non_negative_int(row.get("type"))
    if subject_id <= 0 or subject_type not in VALID_SUBJECT_TYPE_IDS:
        return None
    title = str(row.get("name_cn") or "").strip() or str(row.get("name") or "").strip()
    if not title:
        return None
    rating = _mapping(row.get("rating"))
    rating_score = _rating(rating.get("score", row.get("score")))
    rating_count = _non_negative_int(rating.get("total"))
    source_rank = _non_negative_int(rating.get("rank", row.get("rank")))
    content_id = str(subject_id)
    return DiscoveredContent(
        bvid=content_id,
        content_id=content_id,
        content_url=f"https://bgm.tv/subject/{content_id}",
        source_platform="bangumi",
        source_strategy=strategy,
        content_type="subject",
        title=title,
        body_text=str(row.get("summary") or row.get("short_summary") or "").strip(),
        description="",
        cover_url=_cover_url(row),
        published_at=str(row.get("date") or "").strip(),
        tags=_subject_tags(row),
        favorite_count=_collection_total(row),
        rating_score=rating_score,
        rating_count=rating_count,
        source_rank=source_rank,
        score_threshold=0.0,
        source_keyword_id=source_keyword_id,
    )


def bangumi_collection_to_event(
    row: Mapping[str, Any],
    *,
    username: str,
) -> dict[str, Any] | None:
    """Map one public user collection row into a canonical profile event."""

    if row.get("private") is True:
        return None
    subject = _mapping(row.get("subject"))
    subject_id = _non_negative_int(row.get("subject_id") or subject.get("id"))
    collection_type = _non_negative_int(row.get("type"))
    collection_name = COLLECTION_TYPES.get(collection_type)
    if subject_id <= 0 or collection_name is None:
        return None
    subject_type = _non_negative_int(subject.get("type") or row.get("subject_type"))
    if subject_type not in VALID_SUBJECT_TYPE_IDS:
        return None
    title = str(subject.get("name_cn") or "").strip() or str(subject.get("name") or "").strip()
    rate = min(10, _non_negative_int(row.get("rate")))
    metadata: dict[str, Any] = {
        "source_platform": "bangumi",
        "subject_id": str(subject_id),
        "subject_type": subject_type,
        "collection_type": collection_type,
        "collection_status": collection_name,
        "user_rate": rate,
        "collection_tags": [
            str(value).strip() for value in (row.get("tags") or []) if str(value or "").strip()
        ][:20],
        "ep_status": _non_negative_int(row.get("ep_status")),
        "vol_status": _non_negative_int(row.get("vol_status")),
        "rating_score": _rating(subject.get("score")),
        "rating_count": 0,
        "source_rank": _non_negative_int(subject.get("rank")),
        # The official schema explicitly says updated_at is not a reliable
        # collection timestamp. Preserve it only as diagnostic provenance.
        "source_updated_at": str(row.get("updated_at") or ""),
        "import_source": "bangumi_public_collection",
        "bangumi_username": str(username or "").strip(),
    }
    comment = sanitize_comment_text(row.get("comment"))
    if comment:
        metadata["collection_comment"] = comment
    if rate >= 8:
        event_type = "like"
        strength = 0.85
    elif 1 <= rate <= 4:
        event_type = "feedback"
        strength = 1.0
        metadata["feedback_type"] = "dislike"
    elif collection_type == 1:
        event_type = "favorite"
        strength = 1.0
    elif collection_type == 3:
        event_type = "favorite"
        strength = 0.85
    elif collection_type == 2:
        event_type = "view"
        strength = 0.35
    elif collection_type == 4:
        event_type = "view"
        strength = 0.25
    else:
        event_type = "feedback"
        strength = 0.60
        metadata["feedback_type"] = "dislike"
    metadata["signal_strength"] = strength
    return {
        "event_type": event_type,
        "url": f"https://bgm.tv/subject/{subject_id}",
        "title": title,
        "context": "",
        "metadata": metadata,
    }


@dataclass
class _CollectionScope:
    """One (collection status × subject type) pagination lane."""

    collection_type: int
    subject_type: str
    offset: int = 0
    exhausted: bool = False
    buffer: deque[dict[str, Any]] = field(default_factory=deque)


async def fetch_bangumi_public_collection_events(
    client: Any,
    *,
    username: str,
    subject_types: tuple[str, ...],
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch a fair, bounded sample from one user's public collection.

    Collection status and subject type are separate API filters. Walking their
    Cartesian product avoids a large completed-anime list starving wishlist,
    reading, book, or game signals during bootstrap.

    Each lane is sampled up to ``per_pair`` rows per round-robin visit (its fair
    share), while network calls request up to ``BANGUMI_MAX_PAGE_SIZE`` rows
    (bounded by the global target) and buffer surplus rows for later visits.
    That preserves per-scope fairness and the global ``target`` cap while
    collapsing many tiny pages into fewer paced calls (default ``per_pair`` is
    20, well under the normal 50-row request size).
    """

    target = max(1, int(limit))
    scopes: deque[_CollectionScope] = deque(
        _CollectionScope(collection_type, subject_type)
        for collection_type in COLLECTION_TYPES
        for subject_type in dict.fromkeys(subject_types)
    )
    if not scopes:
        return []
    per_pair = max(1, math.ceil(target / len(scopes)))
    page_size = min(BANGUMI_MAX_PAGE_SIZE, target)
    events: list[dict[str, Any]] = []
    seen_subjects: set[str] = set()
    while scopes and len(events) < target:
        scope = scopes.popleft()
        # One paced request per drained-but-unexhausted lane; the buffered page
        # can feed several fair-share visits before another call is needed.
        if not scope.buffer and not scope.exhausted:
            page = await client.get_user_collections(
                username,
                collection_type=scope.collection_type,
                subject_type=scope.subject_type,
                limit=page_size,
                offset=scope.offset,
            )
            scope.buffer.extend(page.data)
            scope.offset += len(page.data)
            if len(page.data) < page.limit or scope.offset >= page.total:
                scope.exhausted = True
        # Fair share: examine at most ``per_pair`` buffered rows this visit so a
        # dominant lane cannot starve the others or overshoot the global target.
        examined = 0
        while scope.buffer and examined < per_pair and len(events) < target:
            row = scope.buffer.popleft()
            examined += 1
            event = bangumi_collection_to_event(row, username=username)
            if event is None:
                continue
            subject_id = str((event.get("metadata") or {}).get("subject_id") or "")
            if not subject_id or subject_id in seen_subjects:
                continue
            seen_subjects.add(subject_id)
            events.append(event)
        if scope.buffer or not scope.exhausted:
            scopes.append(scope)
    return events
