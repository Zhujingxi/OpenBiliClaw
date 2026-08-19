from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

import pytest
from yt_dlp.utils import DownloadError

from openbiliclaw.content.integration.errors import ContentIntegrationError, IntegrationErrorCode
from openbiliclaw.content.integration.manifest import CapabilityKind
from openbiliclaw.content.providers.youtube.client import YtDlpYouTubeTransport
from openbiliclaw.content.providers.youtube.manifest import YOUTUBE_MANIFEST
from openbiliclaw.content.providers.youtube.models import YouTubePage


class FakeYoutubeDL:
    def __init__(
        self,
        options: Mapping[str, object],
        responses: dict[str, object | Exception],
        calls: list[tuple[dict[str, object], str]],
    ) -> None:
        self._options = dict(options)
        self._responses = responses
        self._calls = calls

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def extract_info(self, target: str, *, download: bool) -> object:
        assert download is False
        self._calls.append((self._options, target))
        response = self._responses[target]
        if isinstance(response, Exception):
            raise response
        return response


class FakeFactory:
    def __init__(self, responses: dict[str, object | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[dict[str, object], str]] = []

    def __call__(self, options: Mapping[str, object]) -> FakeYoutubeDL:
        return FakeYoutubeDL(options, self.responses, self.calls)


def test_youtube_manifest_does_not_claim_removed_trending_feed() -> None:
    assert CapabilityKind.FEED not in YOUTUBE_MANIFEST.capabilities


_ENTRY = {
    "id": "abcdefghijk",
    "title": "Typed video",
    "description": "summary",
    "channel": "Typed channel",
    "channel_id": "UC123",
    "duration": 123,
    "view_count": 456,
    "upload_date": "20250102",
    "thumbnails": [{"url": "https://img.example/video.jpg"}],
    # Real yt-dlp detail dictionaries include internal tuple-valued keys;
    # the adapter must select normalized fields instead of JSON-validating all output.
    "_format_sort_fields": ("quality", "res"),
}


async def test_ytdlp_search_maps_flat_entries_and_ends_pagination() -> None:
    factory = FakeFactory({"ytsearch5:typed query": {"entries": [_ENTRY, _ENTRY, {"id": "bad"}]}})
    transport = YtDlpYouTubeTransport(factory)

    page = YouTubePage.model_validate_json(await transport("search", "typed query", "0", 5))

    assert [item.id for item in page.items] == ["abcdefghijk"]
    assert page.items[0].channel is not None
    assert page.items[0].channel.id == "UC123"
    assert page.items[0].published_at is not None
    assert page.items[0].published_at.isoformat() == "2025-01-02T00:00:00+00:00"
    assert page.next_cursor is None
    options, target = factory.calls[0]
    assert target == "ytsearch5:typed query"
    assert options["extract_flat"] is True
    assert options["quiet"] is True
    assert options["skip_download"] is True

    exhausted = YouTubePage.model_validate_json(await transport("search", "typed query", "1", 5))
    assert exhausted.items == ()
    assert len(factory.calls) == 1


async def test_ytdlp_missing_publish_time_is_null_not_epoch() -> None:
    entry = {key: value for key, value in _ENTRY.items() if key != "upload_date"}
    factory = FakeFactory({"ytsearch1:undated": {"entries": [entry]}})

    page = YouTubePage.model_validate_json(
        await YtDlpYouTubeTransport(factory)("search", "undated", "0", 1)
    )

    assert page.items[0].published_at is None
    assert '"published_at":null' in page.model_dump_json()


async def test_ytdlp_fetch_and_creator_targets_and_mapping() -> None:
    watch = "https://www.youtube.com/watch?v=abcdefghijk"
    channel = "https://www.youtube.com/channel/UC123/videos"
    factory = FakeFactory({watch: _ENTRY, channel: {"entries": [_ENTRY]}})
    transport = YtDlpYouTubeTransport(factory)

    detail = YouTubePage.model_validate_json(await transport("fetch", "abcdefghijk", "0", 1))
    creator = YouTubePage.model_validate_json(await transport("creator", "UC123", "0", 3))

    assert detail.items[0].id == creator.items[0].id == "abcdefghijk"
    assert [call[1] for call in factory.calls] == [watch, channel]
    assert factory.calls[0][0]["extract_flat"] is False
    assert factory.calls[1][0]["extract_flat"] is True
    assert detail.items[0].duration_seconds == 123
    assert detail.items[0].view_count == 456


async def test_ytdlp_caps_one_shot_results_and_deduplicates() -> None:
    second = {**_ENTRY, "id": "abcdefghijl", "title": "Second"}
    factory = FakeFactory({"ytsearch1:x": {"entries": [_ENTRY, second]}})

    page = YouTubePage.model_validate_json(
        await YtDlpYouTubeTransport(factory)("search", "x", "0", 1)
    )

    assert [item.id for item in page.items] == ["abcdefghijk"]
    assert factory.calls[0][1] == "ytsearch1:x"


async def test_ytdlp_rejects_invalid_video_id_without_extraction() -> None:
    factory = FakeFactory({})

    with pytest.raises(ContentIntegrationError) as raised:
        await YtDlpYouTubeTransport(factory)("fetch", "bad", "0", 1)

    assert raised.value.code is IntegrationErrorCode.INVALID_CONTENT_REF
    assert factory.calls == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP Error 429: Too Many Requests", IntegrationErrorCode.RATE_LIMITED),
        ("HTTP Error 403: Forbidden", IntegrationErrorCode.ACCESS_DENIED),
        ("Unable to download webpage: timed out", IntegrationErrorCode.NETWORK_UNAVAILABLE),
        ("Video unavailable", IntegrationErrorCode.INVALID_CONTENT_REF),
        ("Extractor changed", IntegrationErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_ytdlp_download_errors_are_typed(
    message: str, expected: IntegrationErrorCode
) -> None:
    target = "https://www.youtube.com/watch?v=abcdefghijk"
    factory = FakeFactory({target: DownloadError(message)})

    with pytest.raises(ContentIntegrationError) as raised:
        await YtDlpYouTubeTransport(factory)("fetch", "abcdefghijk", "0", 1)

    assert raised.value.code is expected
    assert message not in str(raised.value)
