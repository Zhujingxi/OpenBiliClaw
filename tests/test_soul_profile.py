"""Tests for the Soul profile models."""

from openbiliclaw.soul.profile import (
    AwarenessNote,
    InsightHypothesis,
    InterestTag,
    OnionProfile,
    PreferenceLayer,
    SoulProfile,
    preference_layer_from_dict,
    preference_layer_to_dict,
)


class TestSoulProfile:
    """Test SoulProfile data model."""

    def test_empty_profile_context(self) -> None:
        profile = SoulProfile()
        context = profile.to_llm_context()
        assert "尚未建立" in context

    def test_profile_with_portrait(self) -> None:
        profile = SoulProfile(
            personality_portrait="一个好奇心很强的技术爱好者",
            core_traits=["好奇", "理性"],
        )
        context = profile.to_llm_context()
        assert "好奇心很强" in context
        assert "好奇" in context

    def test_profile_with_insights(self) -> None:
        profile = SoulProfile(
            active_insights=[
                InsightHypothesis(
                    hypothesis="他看游戏视频是为了放松",
                    confidence=0.8,
                ),
            ]
        )
        context = profile.to_llm_context()
        assert "放松" in context
        assert "80%" in context

    def test_profile_with_awareness(self) -> None:
        profile = SoulProfile(
            recent_awareness=[
                AwarenessNote(
                    date="2026-03-07",
                    observation="今天搜索了三次摄影相关内容",
                ),
            ]
        )
        context = profile.to_llm_context()
        assert "摄影" in context


class TestInterestTag:
    """Test InterestTag model."""

    def test_default_weight(self) -> None:
        tag = InterestTag(name="AI", category="科技")
        assert tag.weight == 1.0

    def test_custom_weight(self) -> None:
        tag = InterestTag(name="游戏", category="娱乐", weight=0.3)
        assert tag.weight == 0.3


class TestSourcePlatformMix:
    """PreferenceLayer / OnionProfile expose the source_platform_mix field."""

    def test_preference_layer_round_trip(self) -> None:
        layer = PreferenceLayer(
            source_platform_mix={"bilibili": 0.7, "xiaohongshu": 0.3},
        )
        data = preference_layer_to_dict(layer)
        assert data["source_platform_mix"] == {"bilibili": 0.7, "xiaohongshu": 0.3}
        restored = preference_layer_from_dict(data)
        assert restored.source_platform_mix == {"bilibili": 0.7, "xiaohongshu": 0.3}

    def test_preference_layer_defaults_to_empty_mix(self) -> None:
        layer = PreferenceLayer()
        assert layer.source_platform_mix == {}
        restored = preference_layer_from_dict({})
        assert restored.source_platform_mix == {}

    def test_legacy_soul_profile_emits_source_mix_in_context(self) -> None:
        profile = SoulProfile(
            personality_portrait="x",
            preferences=PreferenceLayer(
                source_platform_mix={"bilibili": 0.6, "xiaohongshu": 0.4},
            ),
        )
        context = profile.to_llm_context()
        assert "来源分布" in context
        assert "bilibili" in context
        assert "xiaohongshu" in context

    def test_soul_profile_hides_source_mix_when_single_source(self) -> None:
        profile = SoulProfile(
            personality_portrait="x",
            preferences=PreferenceLayer(source_platform_mix={"bilibili": 1.0}),
        )
        assert "来源分布" not in profile.to_llm_context()

    def test_onion_profile_round_trip_preserves_mix(self) -> None:
        profile = OnionProfile(
            personality_portrait="y",
            source_platform_mix={"bilibili": 0.8, "xiaohongshu": 0.2},
        )
        data = profile.to_dict()
        assert data["source_platform_mix"] == {"bilibili": 0.8, "xiaohongshu": 0.2}
        restored = OnionProfile.from_dict(data)
        assert restored.source_platform_mix == {"bilibili": 0.8, "xiaohongshu": 0.2}

    def test_onion_profile_surfaces_multi_source_in_context(self) -> None:
        profile = OnionProfile(
            personality_portrait="z",
            source_platform_mix={"bilibili": 0.55, "xiaohongshu": 0.45},
        )
        context = profile.to_llm_context()
        assert "来源分布" in context
        assert "bilibili 55%" in context
        assert "xiaohongshu 45%" in context

    def test_onion_profile_skips_source_mix_when_only_one_source(self) -> None:
        profile = OnionProfile(
            personality_portrait="z",
            source_platform_mix={"bilibili": 1.0},
        )
        assert "来源分布" not in profile.to_llm_context()

    def test_onion_profile_synthesized_preferences_include_mix(self) -> None:
        profile = OnionProfile(
            source_platform_mix={"bilibili": 0.5, "xiaohongshu": 0.5},
        )
        assert profile.preferences.source_platform_mix == {
            "bilibili": 0.5,
            "xiaohongshu": 0.5,
        }
