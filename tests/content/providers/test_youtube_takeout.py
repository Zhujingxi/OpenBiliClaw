from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.content.providers.youtube.takeout import TakeoutEventKind, parse_takeout


def test_takeout_directory_imports_watch_likes_and_subscriptions(tmp_path: Path) -> None:
    root = tmp_path / "YouTube and YouTube Music"
    (root / "history").mkdir(parents=True)
    (root / "subscriptions").mkdir()
    (root / "playlists").mkdir()
    (root / "history" / "watch-history.json").write_text(
        json.dumps(
            [
                {
                    "header": "YouTube",
                    "title": "Watched Typed Video",
                    "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk",
                    "subtitles": [{"name": "Typed Channel", "url": "https://youtube.com/@typed"}],
                    "time": "2025-01-02T03:04:00Z",
                },
                {"header": "YouTube Music", "title": "ignored"},
            ]
        )
    )
    (root / "subscriptions" / "subscriptions.csv").write_text(
        "Channel ID,Channel URL,Channel Title\nUC123,https://youtube.com/@typed,Typed Channel\n"
    )
    (root / "playlists" / "Liked videos.csv").write_text(
        "Video ID,Video URL,Video Title\n"
        "abcdefghijk,https://youtube.com/watch?v=abcdefghijk,Typed Video\n"
    )
    result = parse_takeout(root)
    assert {event.kind for event in result.events} == {
        TakeoutEventKind.VIEW,
        TakeoutEventKind.FOLLOW,
        TakeoutEventKind.LIKE,
    }
    assert result.stats.total == 3
    assert result.events[0].occurred_at is not None


def test_takeout_zip_and_partial_missing_files(tmp_path: Path) -> None:
    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "Takeout/YouTube and YouTube Music/subscriptions/subscriptions.csv",
            (
                "Channel ID,Channel URL,Channel Title\n"
                "UC123,https://youtube.com/@typed,Typed Channel\n"
            ),
        )
    result = parse_takeout(archive)
    assert result.stats.subscriptions == 1
    assert result.stats.watch_history == 0
    assert result.warnings == ("watch history not found",)


def test_takeout_directory_imports_default_html_watch_history(tmp_path: Path) -> None:
    root = tmp_path / "YouTube and YouTube Music" / "history"
    root.mkdir(parents=True)
    (root / "watch-history.html").write_text(
        '<div class="content-cell mdl-cell mdl-cell--6-col">'
        '<a href="https://www.youtube.com/watch?v=abcdefghijk">Typed &amp; Video</a><br>'
        '<a href="https://www.youtube.com/@typed">Typed Channel</a><br>'
        "Jan 2, 2025, 3:04:00 AM UTC</div>"
    )
    result = parse_takeout(tmp_path)
    assert result.stats.watch_history == 1
    assert result.events[0].title == "Typed & Video"
    assert result.events[0].creator_label == "Typed Channel"
    assert result.events[0].provider_content_id == "abcdefghijk"


def test_takeout_zip_imports_default_html_watch_history(tmp_path: Path) -> None:
    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "Takeout/YouTube and YouTube Music/history/watch-history.html",
            '<div class="content-cell"><a href="https://www.youtube.com/watch?v=abcdefghijk">'
            "Typed Video</a></div>",
        )
    result = parse_takeout(archive)
    assert result.stats.watch_history == 1


def test_takeout_non_dict_watch_record_and_missing_likes_are_ignored(tmp_path: Path) -> None:
    root = tmp_path / "history"
    root.mkdir()
    (root / "watch-history.json").write_text(json.dumps(["not-a-record"]))
    result = parse_takeout(tmp_path)
    assert result.events == ()
    assert result.stats.liked_videos == 0


def test_takeout_malformed_json_warns_without_raising(tmp_path: Path) -> None:
    root = tmp_path / "history"
    root.mkdir()
    (root / "watch-history.json").write_text("not json")
    result = parse_takeout(tmp_path)
    assert result.events == ()
    assert "invalid watch history JSON" in result.warnings


def test_takeout_invalid_zip_and_json_root_warn(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    archive.write_text("not zip")
    assert parse_takeout(archive).warnings == ("invalid Takeout zip",)

    root = tmp_path / "history"
    root.mkdir()
    (root / "watch-history.json").write_text("{}")
    assert "invalid watch history JSON root" in parse_takeout(tmp_path).warnings


def test_takeout_ignores_removed_bad_time_bad_rows_and_comments(tmp_path: Path) -> None:
    root = tmp_path / "history"
    root.mkdir()
    (root / "watch-history.json").write_text(
        json.dumps(
            [
                {
                    "title": "Removed video",
                    "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk",
                },
                {
                    "title": "Watched Valid",
                    "titleUrl": "https://www.youtube.com/watch?v=abcdefghijk",
                    "time": "bad",
                },
            ]
        )
    )
    playlists = tmp_path / "playlists"
    playlists.mkdir()
    (playlists / "Liked videos.csv").write_text(
        "# comment\nVideo ID,Video URL,Video Title\nbad,url,bad\nabcdefghijk,url,\n"
    )
    subscriptions = tmp_path / "subscriptions"
    subscriptions.mkdir()
    (subscriptions / "subscriptions.csv").write_text("Channel ID,Channel URL,Channel Title\n,,\n")
    result = parse_takeout(tmp_path)
    assert result.stats.watch_history == 1
    assert result.events[0].occurred_at is None
    assert result.stats.liked_videos == 1


def test_takeout_html_ignores_cells_without_valid_video(tmp_path: Path) -> None:
    root = tmp_path / "history"
    root.mkdir()
    (root / "watch-history.html").write_text(
        '<div class="content-cell">no links</div>'
        '<div class="content-cell"><a href="https://example.com">not video</a></div>'
    )
    assert parse_takeout(tmp_path).events == ()


def test_takeout_rejects_path_that_is_neither_directory_nor_zip(tmp_path: Path) -> None:
    path = tmp_path / "x.txt"
    path.write_text("x")
    try:
        parse_takeout(path)
    except ValueError as exc:
        assert "directory or .zip" in str(exc)
    else:
        raise AssertionError("expected ValueError")
