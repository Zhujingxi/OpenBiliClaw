"""Bounded, provider-owned Google Takeout import for YouTube observations."""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, ConfigDict, Field

from openbiliclaw.core._pydantic import StrictBaseModel

_VIDEO_ID = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")


class TakeoutEventKind(StrEnum):
    VIEW = "view"
    FOLLOW = "follow"
    LIKE = "like"


class TakeoutEvent(StrictBaseModel):
    """Typed observation proposal; no legacy event dictionaries cross the boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TakeoutEventKind
    title: str = Field(min_length=1, max_length=500)
    canonical_url: str = Field(pattern=r"^https?://[^\s]+$", max_length=2048)
    provider_content_id: str = Field(min_length=1, max_length=128)
    creator_label: str | None = Field(default=None, max_length=300)
    occurred_at: AwareDatetime | None = None
    signal_strength: float = Field(ge=0, le=1)


class TakeoutStats(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    watch_history: int = Field(default=0, ge=0)
    subscriptions: int = Field(default=0, ge=0)
    liked_videos: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.watch_history + self.subscriptions + self.liked_videos


class TakeoutParseResult(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    events: tuple[TakeoutEvent, ...]
    stats: TakeoutStats
    warnings: tuple[str, ...]


class _Accumulator:
    def __init__(self) -> None:
        self.events: list[TakeoutEvent] = []
        self.watch = 0
        self.subscriptions = 0
        self.likes = 0
        self.warnings: list[str] = []

    def result(self) -> TakeoutParseResult:
        return TakeoutParseResult(
            events=tuple(self.events),
            stats=TakeoutStats(
                watch_history=self.watch,
                subscriptions=self.subscriptions,
                liked_videos=self.likes,
            ),
            warnings=tuple(self.warnings),
        )


def parse_takeout(path: str | Path) -> TakeoutParseResult:
    """Parse an extracted Takeout directory or its raw zip archive."""

    location = Path(path)
    accumulator = _Accumulator()
    if location.suffix.lower() == ".zip":
        _parse_zip(location, accumulator)
    elif location.is_dir():
        _parse_dir(location, accumulator)
    else:
        raise ValueError("Takeout path must be a directory or .zip file")
    return accumulator.result()


def _parse_zip(path: Path, accumulator: _Accumulator) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = {name.lower(): name for name in archive.namelist()}
            watch = _find(names, "history/watch-history.json")
            watch_html = _find(names, "history/watch-history.html")
            subscriptions = _find(names, "subscriptions/subscriptions.csv")
            likes = _find_likes(names)
            if watch is not None:
                _watch(archive.read(watch).decode(errors="replace"), accumulator)
            elif watch_html is not None:
                _watch_html(archive.read(watch_html).decode(errors="replace"), accumulator)
            else:
                accumulator.warnings.append("watch history not found")
            if subscriptions is not None:
                _subscriptions(archive.read(subscriptions).decode(errors="replace"), accumulator)
            if likes is not None:
                _likes(archive.read(likes).decode(errors="replace"), accumulator)
    except zipfile.BadZipFile:
        accumulator.warnings.append("invalid Takeout zip")


def _parse_dir(root: Path, accumulator: _Accumulator) -> None:
    youtube = (
        root
        if (root / "history").is_dir()
        else next(
            (entry for entry in root.rglob("YouTube and YouTube Music") if entry.is_dir()), root
        )
    )
    watch = youtube / "history" / "watch-history.json"
    watch_html = youtube / "history" / "watch-history.html"
    subscriptions = youtube / "subscriptions" / "subscriptions.csv"
    likes = youtube / "playlists" / "Liked videos.csv"
    if watch.is_file():
        _watch(watch.read_text(errors="replace"), accumulator)
    elif watch_html.is_file():
        _watch_html(watch_html.read_text(errors="replace"), accumulator)
    else:
        accumulator.warnings.append("watch history not found")
    if subscriptions.is_file():
        _subscriptions(subscriptions.read_text(errors="replace"), accumulator)
    if likes.is_file():
        _likes(likes.read_text(errors="replace"), accumulator)


def _watch(text: str, accumulator: _Accumulator) -> None:
    try:
        records = json.loads(text)
    except json.JSONDecodeError:
        accumulator.warnings.append("invalid watch history JSON")
        return
    if not isinstance(records, list):
        accumulator.warnings.append("invalid watch history JSON root")
        return
    for record in records:
        if not isinstance(record, dict) or str(record.get("header", "")) not in {"", "YouTube"}:
            continue
        raw_title = str(record.get("title", "")).strip()
        url = str(record.get("titleUrl", "")).strip()
        match = _VIDEO_ID.search(url)
        if not raw_title or match is None or "removed" in raw_title.casefold():
            continue
        creator: str | None = None
        subtitles = record.get("subtitles")
        if isinstance(subtitles, list) and subtitles and isinstance(subtitles[0], dict):
            creator = str(subtitles[0].get("name", "")).strip() or None
        occurred = _time(record.get("time"))
        accumulator.events.append(
            TakeoutEvent(
                kind=TakeoutEventKind.VIEW,
                title=re.sub(r"^Watched\s+", "", raw_title),
                canonical_url=f"https://www.youtube.com/watch?v={match.group(1)}",
                provider_content_id=match.group(1),
                creator_label=creator,
                occurred_at=occurred,
                signal_strength=0.35,
            )
        )
        accumulator.watch += 1


_CONTENT_CELL = re.compile(
    r'<div[^>]+class="[^"]*content-cell[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_ANCHOR = re.compile(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _watch_html(text: str, accumulator: _Accumulator) -> None:
    """Parse Takeout's default HTML history without an HTML dependency."""

    for cell_match in _CONTENT_CELL.finditer(text):
        anchors = _ANCHOR.findall(cell_match.group(1))
        if not anchors:
            continue
        video_url, raw_title = anchors[0]
        match = _VIDEO_ID.search(video_url)
        title = html.unescape(_TAG.sub("", raw_title)).strip()
        if match is None or not title:
            continue
        creator: str | None = None
        if len(anchors) > 1:
            creator = html.unescape(_TAG.sub("", anchors[1][1])).strip() or None
        accumulator.events.append(
            TakeoutEvent(
                kind=TakeoutEventKind.VIEW,
                title=title,
                canonical_url=f"https://www.youtube.com/watch?v={match.group(1)}",
                provider_content_id=match.group(1),
                creator_label=creator,
                signal_strength=0.35,
            )
        )
        accumulator.watch += 1


def _subscriptions(text: str, accumulator: _Accumulator) -> None:
    for index, row in enumerate(csv.reader(io.StringIO(text))):
        if not row or (index == 0 and row[0].strip().casefold() in {"channel id", "channel_id"}):
            continue
        channel_id = row[0].strip()
        url = row[1].strip() if len(row) > 1 else ""
        title = row[2].strip() if len(row) > 2 else ""
        if not channel_id or not title or not url.startswith(("http://", "https://")):
            continue
        accumulator.events.append(
            TakeoutEvent(
                kind=TakeoutEventKind.FOLLOW,
                title=title,
                canonical_url=url,
                provider_content_id=channel_id,
                creator_label=title,
                signal_strength=1.0,
            )
        )
        accumulator.subscriptions += 1


def _likes(text: str, accumulator: _Accumulator) -> None:
    rows = csv.reader(
        io.StringIO("\n".join(line for line in text.splitlines() if not line.startswith("#")))
    )
    for index, row in enumerate(rows):
        if not row or (index == 0 and row[0].strip().casefold() in {"video id", "videoid"}):
            continue
        video_id = row[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            continue
        title = row[2].strip() if len(row) > 2 else video_id
        accumulator.events.append(
            TakeoutEvent(
                kind=TakeoutEventKind.LIKE,
                title=title or video_id,
                canonical_url=f"https://www.youtube.com/watch?v={video_id}",
                provider_content_id=video_id,
                signal_strength=0.85,
            )
        )
        accumulator.likes += 1


def _time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _find(names: dict[str, str], suffix: str) -> str | None:
    return next((original for name, original in names.items() if name.endswith(suffix)), None)


def _find_likes(names: dict[str, str]) -> str | None:
    return next(
        (
            original
            for name, original in names.items()
            if name.rsplit("/", 1)[-1] in {"liked videos.csv", "likes.csv"}
        ),
        None,
    )
