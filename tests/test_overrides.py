from __future__ import annotations

from openbiliclaw.soul.overrides import (
    DomainAdd,
    InterestPolarityEdit,
    ListEdit,
    ProfileOverrides,
    ScalarPin,
    TextPin,
    apply_overrides,
)
from openbiliclaw.soul.profile import (
    CoreLayer,
    InterestDomain,
    InterestLayer,
    InterestSpecific,
    OnionProfile,
    RoleLayer,
    SurfaceLayer,
    ValuesLayer,
)


def _sample_profile() -> OnionProfile:
    return OnionProfile(
        personality_portrait="AI 写的画像",
        core=CoreLayer(core_traits=["完美主义", "好奇"], deep_needs=["认可"]),
        values_layer=ValuesLayer(values=["自由"], motivational_drivers=["成长"]),
        interest=InterestLayer(
            likes=[
                InterestDomain(
                    domain="科技", weight=0.8, specifics=[InterestSpecific(name="AI", weight=0.7)]
                )
            ],
            dislikes=[InterestDomain(domain="八卦", weight=0.9)],
            favorite_up_users=["老高"],
        ),
        role=RoleLayer(life_stage="工作", current_phase="忙碌"),
        surface=SurfaceLayer(cognitive_style=["分析"], exploration_openness=0.6),
    )


def test_profile_overrides_default_is_empty() -> None:
    ov = ProfileOverrides()
    assert ov.is_empty()
    assert ov.to_dict()["text_pins"] == {}
    assert ov.version == 1


def test_profile_overrides_roundtrip() -> None:
    ov = ProfileOverrides(
        updated_at="2026-05-29T10:00:00",
        text_pins={
            "personality_portrait": TextPin(
                value="我改写的画像", ai_value_at_pin="AI 原值", pinned_at="t"
            )
        },
        list_edits={"core.core_traits": ListEdit(add=["务实"], remove=["完美主义"])},
        interest_edits={"dislikes": InterestPolarityEdit(remove_domains=["二次元"])},
    )

    restored = ProfileOverrides.from_dict(ov.to_dict())

    assert not restored.is_empty()
    assert restored.text_pins["personality_portrait"].value == "我改写的画像"
    assert restored.text_pins["personality_portrait"].ai_value_at_pin == "AI 原值"
    assert restored.list_edits["core.core_traits"].add == ["务实"]
    assert restored.list_edits["core.core_traits"].remove == ["完美主义"]
    assert restored.interest_edits["dislikes"].remove_domains == ["二次元"]


def test_profile_overrides_from_dict_handles_garbage() -> None:
    assert ProfileOverrides.from_dict(None).is_empty()
    assert ProfileOverrides.from_dict({"text_pins": "nope", "list_edits": 5}).is_empty()
    # version defaults safely on bad input
    assert ProfileOverrides.from_dict({"version": "bad"}).version == 1


def test_apply_overrides_empty_is_pure_copy() -> None:
    profile = _sample_profile()
    result = apply_overrides(profile, ProfileOverrides())

    assert result is not profile
    assert result.core.core_traits == ["完美主义", "好奇"]
    # mutating the copy must not touch the input
    result.core.core_traits.append("x")
    assert profile.core.core_traits == ["完美主义", "好奇"]


def test_apply_overrides_text_pin() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        text_pins={
            "personality_portrait": TextPin(value="我自己写的"),
            "role.life_stage": TextPin(value="在读研究生"),
        }
    )
    result = apply_overrides(profile, ov)
    assert result.personality_portrait == "我自己写的"
    assert result.role.life_stage == "在读研究生"


def test_apply_overrides_scalar_pin_clamps() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(scalar_pins={"surface.exploration_openness": ScalarPin(value=1.5)})
    result = apply_overrides(profile, ov)
    assert result.surface.exploration_openness == 1.0


def test_apply_overrides_list_add_remove_dedup() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        list_edits={"core.core_traits": ListEdit(add=["务实", "好奇"], remove=["完美主义"])}
    )
    result = apply_overrides(profile, ov)
    # 完美主义 removed; 好奇 kept (dedup vs add); 务实 appended
    assert result.core.core_traits == ["好奇", "务实"]


def test_apply_overrides_interest_add_remove() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        interest_edits={
            "likes": InterestPolarityEdit(
                add_domains=[DomainAdd(domain="户外", specifics=["徒步"])],
                remove_domains=["科技"],
            ),
            "dislikes": InterestPolarityEdit(add_domains=[DomainAdd(domain="标题党测评")]),
        }
    )
    result = apply_overrides(profile, ov)
    like_domains = [d.domain for d in result.interest.likes]
    assert "科技" not in like_domains
    assert "户外" in like_domains
    dislike_domains = [d.domain for d in result.interest.dislikes]
    assert "标题党测评" in dislike_domains


def test_apply_overrides_dislike_propagates_to_disliked_topics() -> None:
    profile = _sample_profile()
    ov = ProfileOverrides(
        interest_edits={
            "dislikes": InterestPolarityEdit(add_domains=[DomainAdd(domain="标题党测评")])
        }
    )
    result = apply_overrides(profile, ov)
    # OnionProfile.preferences synthesizes disliked_topics from interest.dislikes
    assert "标题党测评" in result.preferences.disliked_topics


def test_apply_overrides_survives_regeneration() -> None:
    """The same overrides apply cleanly to a freshly regenerated AI profile."""
    ov = ProfileOverrides(
        text_pins={"personality_portrait": TextPin(value="我自己写的")},
        list_edits={"core.core_traits": ListEdit(add=["务实"])},
    )
    # Simulate a rebuild: a brand-new AI profile object.
    rebuilt = _sample_profile()
    rebuilt.personality_portrait = "AI 重新生成的不同画像"
    result = apply_overrides(rebuilt, ov)
    assert result.personality_portrait == "我自己写的"
    assert "务实" in result.core.core_traits


def test_apply_overrides_remove_suppresses_rederived_trait() -> None:
    """A removed trait stays gone even if the AI re-derives it next cycle."""
    ov = ProfileOverrides(list_edits={"core.core_traits": ListEdit(remove=["完美主义"])})
    # AI profile still contains the disliked trait (re-derived)
    rebuilt = _sample_profile()
    assert "完美主义" in rebuilt.core.core_traits
    result = apply_overrides(rebuilt, ov)
    assert "完美主义" not in result.core.core_traits
