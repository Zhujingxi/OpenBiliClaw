"""Tests for shared cross-source identity-key normalization."""

from __future__ import annotations

from openbiliclaw.sources.identity_keys import (
    bvid_from_url,
    dedup_key,
    mid_from_url,
    note_id_from_url,
    tweet_id_from_url,
)


def test_tweet_id_from_url_both_forms() -> None:
    assert tweet_id_from_url("https://x.com/i/status/123") == "123"
    assert tweet_id_from_url("https://x.com/someuser/status/456?s=20") == "456"
    assert tweet_id_from_url("https://x.com/someuser") == ""


def test_bvid_and_mid_from_url() -> None:
    assert bvid_from_url("https://www.bilibili.com/video/BV1xx411c7mD?p=1") == "BV1xx411c7mD"
    assert mid_from_url("https://space.bilibili.com/12345/dynamic") == "12345"


def test_note_id_from_url_three_forms() -> None:
    note = "0123456789abcdef01234567"  # 24-hex
    assert note_id_from_url(f"https://www.xiaohongshu.com/explore/{note}") == note
    assert note_id_from_url(f"https://www.xiaohongshu.com/discovery/item/{note}") == note
    assert (
        note_id_from_url(f"https://www.xiaohongshu.com/search_result/{note}?xsec_token=x") == note
    )


def test_note_id_uppercase_hex_normalized_lowercase() -> None:
    upper = "ABCDEF0123456789ABCDEF01"
    assert note_id_from_url(f"https://www.xiaohongshu.com/explore/{upper}") == upper.lower()


def test_note_id_rejects_non_24_hex() -> None:
    assert note_id_from_url("https://www.xiaohongshu.com/explore/short") == ""
    assert note_id_from_url("https://www.xiaohongshu.com/explore/zzzzzzzzzzzzzzzzzzzzzzzz") == ""


def test_dedup_key_prefixes_all_four_key_types() -> None:
    note = "0123456789abcdef01234567"
    assert dedup_key("https://x.com/i/status/123") == "x:123"
    assert dedup_key("https://www.bilibili.com/video/BV1xx411c7mD") == "bv:BV1xx411c7mD"
    assert dedup_key("https://space.bilibili.com/12345") == "mid:12345"
    assert dedup_key(f"https://www.xiaohongshu.com/explore/{note}") == f"xhs:{note}"
    assert dedup_key("https://example.com/nothing") == ""


def test_xhs_action_tap_url_shape_hits_the_note_key() -> None:
    """The extension's xhs-action-tap builds a note URL as
    ``https://www.xiaohongshu.com/explore/<note_id>`` (see
    ``extension/src/content/xhs/action-event.ts:xhsNoteUrl``). That exact
    shape must extract back to the same note key so a like and its later
    retraction discount the same events."""
    note = "69dea966000000001a0280ad"
    tap_built_url = f"https://www.xiaohongshu.com/explore/{note}"
    assert note_id_from_url(tap_built_url) == note
    assert dedup_key(tap_built_url) == f"xhs:{note}"
