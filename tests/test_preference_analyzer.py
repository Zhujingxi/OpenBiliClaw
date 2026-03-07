from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from openbiliclaw.llm.base import LLMProviderError, LLMResponse


class FakeRegistry:
    def __init__(self, response: LLMResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[list[dict[str, str]]] = []
        self.json_modes: list[bool] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        self.calls.append(messages)
        self.json_modes.append(json_mode)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="", provider="openai")


@pytest.mark.asyncio
async def test_analyze_events_parses_structured_preference_output() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    registry = FakeRegistry(
        LLMResponse(
            content="""
            {
              "interests": [
                {"name": "历史", "category": "知识", "weight": 1.2, "source": "history videos"},
                {"name": "纪录片", "category": "影视", "weight": 0.72, "source": "watch history"}
              ],
              "style": {"preferred_duration": "long", "depth_preference": 0.91},
              "context": {"session_type": "deep_dive"},
              "exploration_openness": 0.66,
              "disliked_topics": ["低质标题党"],
              "favorite_up_users": ["小约翰可汗"]
            }
            """,
            provider="openai",
        )
    )
    analyzer = PreferenceAnalyzer(registry)

    preference = await analyzer.analyze_events(
        events=[
            {"event_type": "view", "title": "一战史解说", "metadata": {"bvid": "BV1"}},
            {"event_type": "view", "title": "长篇纪录片", "metadata": {"bvid": "BV2"}},
        ],
        existing_preference={},
    )

    assert registry.json_modes == [True]
    assert "output_schema" in registry.calls[0][0]["content"]
    assert preference["interests"][0]["name"] == "历史"
    assert preference["interests"][0]["weight"] == 1.0
    assert preference["style"]["preferred_duration"] == "long"
    assert preference["favorite_up_users"] == ["小约翰可汗"]


@pytest.mark.asyncio
async def test_invalid_json_response_raises_preference_analysis_error() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    registry = FakeRegistry(LLMResponse(content="not-json", provider="openai"))
    analyzer = PreferenceAnalyzer(registry)

    with pytest.raises(PreferenceAnalysisError):
        await analyzer.analyze_events(
            events=[{"event_type": "view", "title": "x"}],
            existing_preference={},
        )


def test_merge_preferences_applies_decay_and_deduplicates_tags() -> None:
    from openbiliclaw.soul.preference_analyzer import PreferenceAnalyzer

    analyzer = PreferenceAnalyzer(FakeRegistry())
    merged = analyzer.merge_preferences(
        existing_preference={
            "interests": [
                {
                    "name": "历史",
                    "category": "知识",
                    "weight": 0.8,
                    "first_seen": "2026-02-01T00:00:00",
                    "last_seen": (datetime.now() - timedelta(days=14)).isoformat(),
                    "source": "old",
                }
            ],
            "favorite_up_users": ["旧UP"],
        },
        new_preference={
            "interests": [
                {"name": "历史", "category": "知识", "weight": 0.7, "source": "new"},
                {"name": "纪录片", "category": "影视", "weight": 0.6, "source": "new"},
            ],
            "favorite_up_users": ["旧UP", "新UP"],
        },
        now=datetime.now(),
    )

    assert len(merged["interests"]) == 2
    history_tag = next(item for item in merged["interests"] if item["name"] == "历史")
    assert 0.7 <= history_tag["weight"] <= 1.0
    assert history_tag["first_seen"] == "2026-02-01T00:00:00"
    assert set(merged["favorite_up_users"]) == {"旧UP", "新UP"}


@pytest.mark.asyncio
async def test_provider_error_is_wrapped() -> None:
    from openbiliclaw.soul.preference_analyzer import (
        PreferenceAnalysisError,
        PreferenceAnalyzer,
    )

    analyzer = PreferenceAnalyzer(FakeRegistry(error=LLMProviderError("provider down")))

    with pytest.raises(PreferenceAnalysisError):
        await analyzer.analyze_events(
            events=[{"event_type": "view", "title": "x"}],
            existing_preference={},
        )
