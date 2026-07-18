"""Bangumi subject and public-collection normalization."""

from __future__ import annotations

import math
import unicodedata
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
# Human-readable SubjectType labels so the profile LLM sees "动画/书籍/游戏"
# instead of the bare integer id. Mirrors ``SUBJECT_TYPE_IDS`` in
# ``bangumi_client`` (book=1, anime=2, music=3, game=4, real=6).
SUBJECT_TYPE_LABELS = {
    1: "书籍",
    2: "动画",
    3: "音乐",
    4: "游戏",
    6: "三次元",
}

# Ordered ``infobox`` keys naming a subject's principal creator, per SubjectType.
# Bangumi has no dedicated author field: the credit lives in the free-form
# ``infobox`` array, whose key names differ per type (a book has 作者, an anime
# has 导演, a game has 开发).
#
# Calibration provenance: a live survey of 250 rows (50 per subject type via
# ``GET /v0/subjects``, 2026-07-18) found ``infobox`` present on 250/250 rows
# and gave the per-type population rates below. Each ladder leads with the most
# populated creator credit for that type and falls back to the next-best when a
# row omits it (e.g. a manga credited only to 原作 + 作画, or an anime whose
# credit sits on the production company instead of a named director).
#   书籍   作者 44/50 · 原作 3/50 · 作画 3/50 · 出版社 47/50
#   动画   导演 47/50 · 原作 45/50 · 动画制作 30/50 · 製作 43/50
#   音乐   艺术家 41/50 · 作曲 43/50 · 厂牌 31/50
#   游戏   开发 35/50 · 发行 33/50 · 游戏开发商 5/50
#   三次元 导演 26/50 · 编剧 24/50 · 主演 29/50
# Re-run the survey before trusting these keys after any Bangumi schema change.
AUTHOR_INFOBOX_KEYS: dict[int, tuple[str, ...]] = {
    1: ("作者", "原作", "作画", "出版社"),
    2: ("导演", "原作", "动画制作", "製作"),
    3: ("艺术家", "作曲", "厂牌"),
    4: ("开发", "发行", "游戏开发商"),
    6: ("导演", "编剧", "主演"),
}
# A creator credit is a display field on the recommendation card. Keep only the
# leading names so a card reads "沖浦啓之、黄瀬和哉" instead of a 30-name roster,
# and hard-cap the rendered length.
MAX_AUTHOR_PARTS = 3
MAX_AUTHOR_LENGTH = 80
# Word-shaped credits that carry no name, only the fact that a name is absent.
# Scope is deliberately narrow: a literal only qualifies if THIS stack can
# actually emit it — Python ``str(None)`` → "None", JSON/JS ``null`` and
# ``undefined``, ``float('nan')`` → "NaN" — plus unambiguous "not applicable"
# spellings. A literal "None" reaching author_name is the dirty-row class
# COALESCE cannot repair once persisted.
#
# Deliberately NOT filtered, because each could be a genuine short credit and
# over-filtering silently deletes real data:
#   - "nil" — a real Japanese rock band, and no Python/JS/JSON path produces
#     it (that spelling is Ruby/Lisp), so it was never an artifact we emit
#   - bare "na" (romanised 나 / 娜 surname) — only the unambiguous "n/a" form
#   - 无 / 暂无 / 未知 / 不明 — ordinary characters that can occur in a real
#     name; these are editor prose, not a serialization artifact, so dropping
#     them would be semantic cleanup rather than dirty-value defence
#   - single letters and digits — plausible stage names
#
# "none"/"null"/"nan" carry a small false-positive risk (a band could stylise
# itself that way), accepted knowingly: they are the exact output of the
# stringification bug this guard exists to stop.
_PLACEHOLDER_CREDITS = frozenset(
    {"none", "null", "nan", "undefined", "n/a", "n.a.", "na.", "(none)", "<none>"}
)


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


def _infobox_value_text(value: object) -> str:
    """Flatten one ``infobox`` entry's ``value`` into display text.

    Bangumi ships three shapes for ``value`` (observed 4117 / 226 / 197 times
    in the 250-row survey): a bare string (``"押井守"``), a list of ``{"v": …}``
    entries (``别名``), and a list of ``{"k": …, "v": …}`` entries
    (``版本:东立版``). Both list shapes are read through ``v``; ``k`` is a
    sub-label, not a name.

    Every other input — ``None``, a bool, a number, a dict, an empty list, a
    drifted schema — yields ``""``. Returning the empty string rather than a
    stringified value is what keeps literal ``"None"`` / ``"[]"`` out of
    ``author_name``, the dirty-row class that ``COALESCE`` cannot repair.

    That guarantee needs two things beyond shape checks, both learned the hard
    way: ``v`` is only read when it is genuinely a ``str`` (``str()`` on a
    drifted value manufactures ``"['押井守']"`` out of a nested list), and a
    value that merely *spells* absence is normalised to ``""`` — a bare
    ``"None"`` in the source data is just as unusable as one we produced
    ourselves. See :data:`_PLACEHOLDER_CREDITS` for where that line is drawn.
    """

    if isinstance(value, str):
        text = value.strip()
        return "" if _is_placeholder_credit(text) else text
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        raw = entry.get("v")
        # Only real strings: no str() fallback, which would turn a drifted
        # ``{"v": ["押井守"]}`` into the literal text "['押井守']".
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if _is_placeholder_credit(text) or text in parts:
            continue
        parts.append(text)
        if len(parts) >= MAX_AUTHOR_PARTS:
            break
    return "、".join(parts)


def _is_placeholder_credit(text: str) -> bool:
    """True when ``text`` spells absence rather than naming anyone.

    Two rules. The word-shaped ones are enumerated in
    :data:`_PLACEHOLDER_CREDITS`. The symbol-shaped ones are decided by Unicode
    category rather than a hand-written character list: a real name contains at
    least one letter or digit, so a value made up entirely of punctuation,
    symbols, separators or format controls (``"-"``, ``"—"``, ``"…"``,
    ``"()"``, ``"?"``, ``"★"``, a stray no-break space) names nobody. Category
    coverage is what keeps this from missing the next dash or ellipsis variant
    an editor happens to type — an enumerated list had already missed U+2026.

    Anything else — including single characters and CJK words that merely
    *mean* "none" — is treated as a real credit, because guessing wrong there
    deletes genuine data.
    """

    stripped = text.strip()
    if not stripped:
        return True
    if stripped.casefold() in _PLACEHOLDER_CREDITS:
        return True
    return not any(unicodedata.category(char)[0] in {"L", "N"} for char in stripped)


def _author_name(subject: Mapping[str, Any], subject_type: int) -> str:
    """Resolve a subject's creator credit from its ``infobox``.

    Walks :data:`AUTHOR_INFOBOX_KEYS` for ``subject_type`` in priority order and
    returns the first credit that flattens to non-empty text. A subject whose
    ``infobox`` is missing, is not a list, or carries no ladder key resolves to
    ``""`` — an absent credit is an empty string, never a placeholder.
    """

    ladder = AUTHOR_INFOBOX_KEYS.get(subject_type, ())
    if not ladder:
        return ""
    raw_infobox = subject.get("infobox")
    if not isinstance(raw_infobox, list):
        return ""
    wanted = set(ladder)
    credits: dict[str, str] = {}
    for entry in raw_infobox:
        if not isinstance(entry, Mapping):
            continue
        key = str(entry.get("key") or "").strip()
        # First *non-empty* occurrence wins: a key whose value flattens to ""
        # stays unrecorded so a later duplicate can still supply the credit.
        if key not in wanted or key in credits:
            continue
        text = _infobox_value_text(entry.get("value"))
        if text:
            credits[key] = text
    for key in ladder:
        credit = credits.get(key, "")
        if credit:
            return _truncate_credit(credit)
    return ""


def _truncate_credit(credit: str) -> str:
    """Bound a credit's rendered length, cutting on a name separator.

    Some credits are a single string holding a whole roster (a 猫和老鼠 entry
    runs past 200 characters). A blind slice can end mid-name or inside an
    unclosed bracket, so prefer the last separator that still keeps at least
    half the budget and fall back to a hard cut only when there is none — then
    drop any bracket the cut left open.
    """

    if len(credit) <= MAX_AUTHOR_LENGTH:
        return credit
    head = credit[:MAX_AUTHOR_LENGTH]
    cut = max(head.rfind(separator) for separator in ("、", "，", ",", "/"))
    if cut >= MAX_AUTHOR_LENGTH // 2:
        head = head[:cut]
    return _drop_unclosed_bracket(head).rstrip(" 、,，/|·")


# Bracket pairs seen in real credits: 押井守（総監督）, [監修], 《攻殻機動隊》.
_CREDIT_BRACKETS = {
    "（": "）",
    "(": ")",
    "【": "】",
    "[": "]",
    "《": "》",
    "〈": "〉",
    "「": "」",
    "『": "』",
    "〔": "〕",
    "｢": "｣",
}
_CREDIT_BRACKET_CLOSERS = {closer: opener for opener, closer in _CREDIT_BRACKETS.items()}


def _drop_unclosed_bracket(head: str) -> str:
    """Cut ``head`` back to before the first bracket the truncation left open.

    ``押井守、神山健治（総監督`` reads as if the credit were mangled, so trim to
    ``押井守、神山健治``.

    A closer only settles an opener of the SAME family: in ``(credit]`` the
    ``]`` does not close the ``(``, so the ``(`` is still open and the cut
    happens before it. Popping on any closer would have declared that balanced.

    Known limitation: when the unmatched opener sits at index 0 the whole
    credit is one long parenthetical, and trimming would erase it entirely.
    Losing a real credit is worse than an unbalanced tail, so the hard cut is
    kept there — deliberate, and covered by a test so the choice stays visible.
    """

    unclosed: list[int] = []
    for index, char in enumerate(head):
        if char in _CREDIT_BRACKETS:
            unclosed.append(index)
        # A closer only settles an opener of its own family. One that matches
        # nothing is stray punctuation inside the credit and leaves the stack
        # untouched, so ``(credit]`` still counts the ``(`` as open.
        elif (
            char in _CREDIT_BRACKET_CLOSERS
            and unclosed
            and head[unclosed[-1]] == _CREDIT_BRACKET_CLOSERS[char]
        ):
            unclosed.pop()
    if not unclosed:
        return head
    return head[: unclosed[0]].rstrip() or head


def _meta_tags(subject: Mapping[str, Any]) -> list[str]:
    """Extract a subject's ``meta_tags`` (e.g. TV / 剧场版) defensively.

    ``meta_tags`` must be a JSON array. A schema drift that hands back a bare
    string (e.g. "TV") would otherwise be walked character-by-character, so only
    iterate genuine lists and de-duplicate while preserving order.
    """

    raw_meta_tags = subject.get("meta_tags")
    if not isinstance(raw_meta_tags, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_meta_tags:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _subject_tags(subject: Mapping[str, Any]) -> list[str]:
    candidates: list[tuple[int, str]] = []
    # ``meta_tags`` must be a JSON array. A schema drift that hands back a bare
    # string (e.g. "TV") would otherwise be walked character-by-character, so
    # only iterate genuine lists — mirroring the ``tags`` guard below.
    for text in _meta_tags(subject):
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
    """Normalize one official Subject/SlimSubject into discovery content.

    ``author_name`` is resolved from the row's own ``infobox`` (see
    :func:`_author_name`), which both discovery endpoints — ``POST
    /v0/search/subjects`` and ``GET /v0/subjects`` — return inline, so no extra
    per-subject request is needed. SlimSubject rows (embedded in user
    collections) carry no ``infobox`` and resolve to an empty credit.
    """

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
        author_name=_author_name(row, subject_type),
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
    include_private: bool = False,
) -> dict[str, Any] | None:
    """Map one user collection row into a canonical profile event.

    Private rows are skipped unless ``include_private`` is set, which the
    authenticated (personal-access-token) path passes because a Bearer request
    returns the token owner's own private collections — legitimate signal for
    their profile.
    """

    if row.get("private") is True and not include_private:
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
        # Readable label so the profile LLM knows 动画/书籍/游戏 directly instead
        # of decoding the bare integer id.
        "subject_type_label": SUBJECT_TYPE_LABELS.get(subject_type, ""),
        "collection_type": collection_type,
        "collection_status": collection_name,
        "user_rate": rate,
        "collection_tags": [
            str(value).strip() for value in (row.get("tags") or []) if str(value or "").strip()
        ][:20],
        # Subject-level meta tags (TV / 剧场版 / …) when the collection row
        # carries embedded subject data; mirrors the discovery path's parse and
        # stays empty when the row omits subject or ships a drifted schema.
        "meta_tags": _meta_tags(subject),
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
    include_private: bool = False,
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
            event = bangumi_collection_to_event(
                row, username=username, include_private=include_private
            )
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
