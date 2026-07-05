"""Tests for classify_event_satisfaction — deterministic rule table.

Part of the event-satisfaction signal work (see
``docs/plans/2026-05-16-event-satisfaction-signal.md``). The classifier
must be auditable and cheap — no LLM calls — so the test rules below
mirror the documented contract one-for-one.
"""

from __future__ import annotations

import pytest

from openbiliclaw.sources.event_format import classify_event_satisfaction

# --- Explicit positive types ---


@pytest.mark.parametrize("event_type", ["like", "coin", "favorite", "comment"])
def test_explicit_positive_engagement_types(event_type: str) -> None:
    category, reason = classify_event_satisfaction({"event_type": event_type, "title": "X"})
    assert category == "positive"
    assert reason == "explicit_engagement"


# --- Feedback events ---


def test_feedback_dislike_is_explicit_negative() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"feedback_type": "dislike"}}
    )
    assert (category, reason) == ("negative", "explicit_negative")


def test_feedback_thumbs_down_reaction_is_explicit_negative() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"reaction": "thumbs_down"}}
    )
    assert (category, reason) == ("negative", "explicit_negative")


def test_feedback_like_is_positive() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"feedback_type": "like"}}
    )
    assert (category, reason) == ("positive", "explicit_engagement")


def test_feedback_retraction_is_neutral() -> None:
    """An unlike/unbookmark is a neutralization, never a negative preference."""
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"feedback_type": "retraction"}}
    )
    assert (category, reason) == ("neutral", "retraction")


def test_feedback_retraction_wins_over_negative_signals() -> None:
    """The retraction rule must be checked BEFORE any feedback-negative rule —
    a retraction carrying an incidental thumbs_down still classifies neutral."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "feedback",
            "metadata": {"feedback_type": "retraction", "reaction": "thumbs_down"},
        }
    )
    assert (category, reason) == ("neutral", "retraction")


def test_feedback_comment_is_neutral_direct_feedback() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"feedback_type": "comment"}}
    )
    assert (category, reason) == ("neutral", "direct_feedback")


def test_feedback_thumbs_up_reaction_is_positive() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "feedback", "metadata": {"reaction": "thumbs_up"}}
    )
    assert (category, reason) == ("positive", "explicit_engagement")


# --- Click events with dwell ---


def test_click_meaningful_dwell_short_video() -> None:
    """18s on a 60s video is well above both thresholds (15s, 30%)."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 18, "video_duration_seconds": 60},
        }
    )
    assert (category, reason) == ("positive", "meaningful_dwell")


def test_click_quick_exit_short_dwell_long_video() -> None:
    """2s on a 10min video is a clear quick-exit signal."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 2, "video_duration_seconds": 600},
        }
    )
    assert (category, reason) == ("negative", "quick_exit")


def test_click_shallow_view_is_neutral() -> None:
    """10s on a 10min video — past quick-exit, short of meaningful dwell."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 10, "video_duration_seconds": 600},
        }
    )
    assert (category, reason) == ("neutral", "shallow_view")


def test_click_with_no_watch_seconds_is_unknown() -> None:
    category, reason = classify_event_satisfaction(
        {"event_type": "click", "metadata": {"video_duration_seconds": 600}}
    )
    assert (category, reason) == ("unknown", "missing_dwell")


def test_click_reads_top_level_dwell_fields() -> None:
    """Producers may put watch_seconds at the top level; classifier reads both."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "watch_seconds": 18,
            "video_duration_seconds": 60,
        }
    )
    assert (category, reason) == ("positive", "meaningful_dwell")


def test_click_falls_back_to_duration_key() -> None:
    """Legacy extension events use `duration` instead of `video_duration_seconds`."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 18, "duration": 60},
        }
    )
    assert (category, reason) == ("positive", "meaningful_dwell")


# --- Content-page (duration-less) dwell ---


def test_content_page_dwell_engaged_reading_is_positive() -> None:
    """A duration-less content_page_exit with >=30s visible reading is positive."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 45, "dwell_source": "content_page_exit"},
        }
    )
    assert (category, reason) == ("positive", "engaged_reading")


def test_content_page_dwell_quick_exit_is_negative() -> None:
    """<5s on a content page reuses the quick-exit negative rule."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 3, "dwell_source": "content_page_exit"},
        }
    )
    assert (category, reason) == ("negative", "quick_exit")


def test_content_page_dwell_between_bands_is_neutral() -> None:
    """5s..30s reading is neutral — read a bit but not engaged."""
    category, reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 12, "dwell_source": "content_page_exit"},
        }
    )
    assert category == "neutral"


def test_video_dwell_classification_unchanged_by_content_rule() -> None:
    """Regression: video_page_exit dwell still uses the ratio-based rule.
    18s on a 60s video is meaningful; 18s content-reading is below the 30s
    reading bar (neutral) — the two rules must not bleed."""
    video_cat, video_reason = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {
                "watch_seconds": 18,
                "video_duration_seconds": 60,
                "dwell_source": "video_page_exit",
            },
        }
    )
    assert (video_cat, video_reason) == ("positive", "meaningful_dwell")

    content_cat, _ = classify_event_satisfaction(
        {
            "event_type": "click",
            "metadata": {"watch_seconds": 18, "dwell_source": "content_page_exit"},
        }
    )
    assert content_cat == "neutral"


# --- Passive browse ---


@pytest.mark.parametrize("event_type", ["snapshot", "scroll", "hover", "search"])
def test_passive_browse_events_are_neutral(event_type: str) -> None:
    category, reason = classify_event_satisfaction({"event_type": event_type})
    assert (category, reason) == ("neutral", "passive_browse")


# --- Unknown / fallback ---


def test_unknown_event_type_returns_fallback_without_raising() -> None:
    category, reason = classify_event_satisfaction({"event_type": "totally_invented_action"})
    assert (category, reason) == ("unknown", "fallback")


def test_malformed_event_does_not_raise() -> None:
    """A garbage payload (TypeError on metadata access) returns fallback."""
    # Non-dict metadata — accessing nested keys would raise on .get() if
    # the classifier didn't guard. Should return unknown/fallback silently.
    category, reason = classify_event_satisfaction(
        {"event_type": "click", "metadata": "not-a-dict"}
    )
    assert category == "unknown"
    assert reason == "fallback"


def test_twitter_engagement_scoring_v1() -> None:
    """X v1 mapping: like / bookmark→favorite / reply→comment score positive
    via the existing explicit set; retweet→share and follow stay context-tier.

    Regression guard — we must NEVER extend the global positive set just for X
    (that would silently change Bilibili/Douyin/YouTube follow/share scoring).
    """
    for event_type in ("like", "favorite", "comment"):
        category, _ = classify_event_satisfaction(
            {"event_type": event_type, "metadata": {"source_platform": "twitter"}}
        )
        assert category == "positive"
    for event_type in ("share", "follow"):
        category, _ = classify_event_satisfaction(
            {"event_type": event_type, "metadata": {"source_platform": "twitter"}}
        )
        assert category != "positive"
