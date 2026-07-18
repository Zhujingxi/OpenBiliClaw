"""Canonical source-platform families shared across discovery and storage."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

PLATFORM_BILIBILI = "bilibili"
PLATFORM_XIAOHONGSHU = "xiaohongshu"
PLATFORM_DOUYIN = "douyin"
PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITTER = "twitter"
PLATFORM_ZHIHU = "zhihu"
PLATFORM_REDDIT = "reddit"


@dataclass(frozen=True)
class SourceFamilyRule:
    """Aliases and discovery signals that identify one canonical platform."""

    family: str
    platform_aliases: frozenset[str]
    source_keys: frozenset[str] = frozenset()
    source_prefixes: tuple[str, ...] = ()
    url_hosts: tuple[str, ...] = ()


SOURCE_FAMILY_RULES = (
    SourceFamilyRule(
        family=PLATFORM_BILIBILI,
        platform_aliases=frozenset({"bilibili", "bili"}),
        source_keys=frozenset({"search", "related_chain", "trending", "explore"}),
        url_hosts=("bilibili.com", "b23.tv"),
    ),
    SourceFamilyRule(
        family=PLATFORM_XIAOHONGSHU,
        platform_aliases=frozenset({"xiaohongshu", "xhs", "rednote"}),
        source_prefixes=("xhs-", "xhs_", "xiaohongshu"),
        url_hosts=("xiaohongshu.com", "xhslink.com"),
    ),
    SourceFamilyRule(
        family=PLATFORM_DOUYIN,
        platform_aliases=frozenset({"douyin", "dy", "tiktok"}),
        source_prefixes=("dy-", "dy_", "douyin"),
        url_hosts=("douyin.com",),
    ),
    SourceFamilyRule(
        family=PLATFORM_YOUTUBE,
        platform_aliases=frozenset({"youtube", "yt"}),
        source_prefixes=("yt-", "yt_", "youtube"),
        url_hosts=("youtube.com", "youtu.be"),
    ),
    SourceFamilyRule(
        family=PLATFORM_TWITTER,
        platform_aliases=frozenset({"twitter", "x"}),
        source_prefixes=("x-", "x_", "twitter"),
        url_hosts=("x.com", "twitter.com"),
    ),
    SourceFamilyRule(
        family=PLATFORM_ZHIHU,
        platform_aliases=frozenset({"zhihu", "zh", "知乎"}),
        source_prefixes=("zhihu-", "zhihu_"),
        url_hosts=("zhihu.com",),
    ),
    SourceFamilyRule(
        family=PLATFORM_REDDIT,
        platform_aliases=frozenset({"reddit", "rd"}),
        source_prefixes=("reddit-", "reddit_"),
        url_hosts=("reddit.com", "redd.it"),
    ),
)

CANONICAL_SOURCE_FAMILIES = tuple(rule.family for rule in SOURCE_FAMILY_RULES)


def normalize_source_platform(value: object, *, default: str = "") -> str:
    """Return the canonical family for a known alias, preserving unknown keys."""
    key = str(value or "").strip().lower()
    if not key:
        return default
    for rule in SOURCE_FAMILY_RULES:
        if key in rule.platform_aliases:
            return rule.family
    return key


def source_family(source: object, source_platform: object = "") -> str:
    """Resolve pool source accounting from platform, exact strategy, or prefix."""
    platform = str(source_platform or "").strip().lower()
    raw_source = str(source or "").strip()
    source_key = raw_source.lower()
    normalized = normalize_source_platform(platform)
    if platform and normalized in CANONICAL_SOURCE_FAMILIES and normalized != PLATFORM_BILIBILI:
        return normalized
    for rule in SOURCE_FAMILY_RULES:
        if source_key in rule.source_keys:
            return rule.family
    for rule in SOURCE_FAMILY_RULES:
        if source_key.startswith(rule.source_prefixes):
            return rule.family
    if normalized == PLATFORM_BILIBILI:
        return normalized
    return raw_source or "unknown"


def infer_source_platform_from_url(url: object) -> str:
    """Infer a canonical family from an exact hostname or its subdomain."""
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower().rstrip(".")
    for rule in SOURCE_FAMILY_RULES:
        if any(host == base or host.endswith(f".{base}") for base in rule.url_hosts):
            return rule.family
    return ""
