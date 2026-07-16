"""Deterministic multilingual-enough grounding primitives for short AI text."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_LATIN_STOP_WORDS = frozenset(
    {
        "about",
        "also",
        "content",
        "fits",
        "from",
        "interest",
        "interests",
        "matches",
        "recommend",
        "recommendation",
        "that",
        "this",
        "video",
        "with",
        "your",
    }
)
_CJK_STOP_PHRASES = frozenset(
    {
        "一个",
        "一个视频",
        "不错",
        "以及",
        "你的",
        "你的兴趣",
        "内容",
        "关于",
        "兴趣",
        "可以",
        "喜欢",
        "实用",
        "实用教程",
        "很有",
        "很符合",
        "推荐",
        "推荐内容",
        "教程",
        "相关",
        "相关内容",
        "符合",
        "讲解",
        "这个",
        "这个内容",
        "这个视频",
        "适合",
        "非常适合",
        "非常",
        "值得",
        "值得推荐",
        "视频",
        "视频教程",
    }
)
_CJK_STOP_NGRAMS = frozenset(
    phrase[index : index + size]
    for phrase in _CJK_STOP_PHRASES
    for size in (2, 3)
    for index in range(len(phrase) - size + 1)
)


def normalized_grounding_tokens(text: str) -> frozenset[str]:
    """Return meaningful Latin words and overlapping CJK bi/trigrams."""

    normalized = unicodedata.normalize("NFKC", text).casefold()
    latin = {
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if len(token) >= 3 and token not in _LATIN_STOP_WORDS
    }
    cjk: set[str] = set()
    for chunk in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized):
        for size in (2, 3):
            cjk.update(chunk[index : index + size] for index in range(len(chunk) - size + 1))
    return frozenset(latin | (cjk - _CJK_STOP_NGRAMS))


def grounding_overlap(facts: Iterable[str], candidate: str) -> frozenset[str]:
    """Return non-generic tokens shared by supplied facts and candidate text."""

    fact_tokens = frozenset().union(*(normalized_grounding_tokens(fact) for fact in facts))
    return fact_tokens & normalized_grounding_tokens(candidate)


def is_grounded_in(facts: Iterable[str], candidate: str) -> bool:
    """Return whether candidate shares at least one meaningful token with facts."""

    return bool(grounding_overlap(facts, candidate))
