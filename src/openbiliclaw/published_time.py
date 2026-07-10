from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from email.utils import parsedate_to_datetime

_PLACEHOLDERS = {"", "unknown", "none", "null", "n/a", "na", "未知", "暂无"}
_SPACE_RE = re.compile(r"\s+")
_MAX_LABEL_LENGTH = 64


@dataclass(frozen=True, slots=True)
class PublishedTime:
    published_at: str = ""
    published_label: str = ""


def normalize_published_label(value: object) -> str:
    if not isinstance(value, str):
        return ""
    label = _SPACE_RE.sub(" ", value).strip()
    if label.lower() in _PLACEHOLDERS:
        return ""
    return label[:_MAX_LABEL_LENGTH]


def _as_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        seconds = float(value) / 1000 if abs(float(value)) >= 1_000_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text or text.lower() in _PLACEHOLDERS:
        return None
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return _as_datetime(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def normalize_published_time(
    value: object = None,
    *,
    label: object = None,
    now: datetime | None = None,
) -> PublishedTime:
    parsed = _as_datetime(value)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if parsed is not None:
        lower = datetime(1970, 1, 1, tzinfo=UTC)
        upper = current + timedelta(days=366)
        if lower <= parsed <= upper:
            return PublishedTime(
                parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                normalize_published_label(label),
            )
    return PublishedTime("", normalize_published_label(label))


def format_published_time(
    published_at: object,
    published_label: object = "",
    *,
    now: datetime | None = None,
    local_tz: tzinfo | None = None,
) -> str:
    normalized = normalize_published_time(published_at, label=published_label, now=now)
    if not normalized.published_at:
        return normalized.published_label
    published = datetime.fromisoformat(normalized.published_at.replace("Z", "+00:00"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    diff = current - published
    if -timedelta(minutes=5) <= diff < timedelta(minutes=1):
        return "刚刚"
    if timedelta(0) <= diff < timedelta(hours=24):
        return f"{max(1, int(diff.total_seconds() // 3600))} 小时前"
    if timedelta(0) <= diff < timedelta(days=7):
        return f"{int(diff.total_seconds() // 86400)} 天前"
    local = published.astimezone(local_tz)
    local_now = current.astimezone(local_tz)
    if local.year == local_now.year:
        return f"{local.month}月{local.day}日"
    return local.strftime("%Y-%m-%d")
