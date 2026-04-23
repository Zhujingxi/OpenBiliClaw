"""Tests for speculative interest lifecycle."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from openbiliclaw.soul.speculator import (
    CooldownEntry,
    InterestSpeculator,
    SpeculativeInterest,
    SpeculativeState,
    _event_matches_speculation,
    _tokenize,
    expire_stale,
    load_speculative_state,
    observe_events,
    promote_ready,
    save_speculative_state,
)

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------


def test_tokenize_basic():
    tokens = _tokenize("博弈论 科普 视频")
    assert "博弈论" in tokens
    assert "科普" in tokens
    assert "视频" in tokens


def test_tokenize_filters_short():
    tokens = _tokenize("a 好 hello")
    assert "a" not in tokens
    assert "好" not in tokens
    assert "hello" in tokens


# ---------------------------------------------------------------------------
# Event matching
# ---------------------------------------------------------------------------


def test_event_matches_domain_substring():
    spec = SpeculativeInterest(domain="博弈论", category="知识")
    event = {"title": "纳什均衡与博弈论入门", "tags": ""}
    assert _event_matches_speculation(event, spec) is True


def test_event_matches_category_substring():
    spec = SpeculativeInterest(domain="量子计算", category="前沿科技")
    event = {"title": "前沿科技趋势解读", "tags": ""}
    assert _event_matches_speculation(event, spec) is True


def test_event_no_match():
    spec = SpeculativeInterest(domain="建筑叙事", category="人文")
    event = {"title": "今天吃什么", "tags": "美食"}
    assert _event_matches_speculation(event, spec) is False


def test_event_matches_token_overlap():
    spec = SpeculativeInterest(domain="科技伦理与AI治理", category="科技哲学")
    event = {"title": "AI治理的未来走向", "tags": "科技"}
    assert _event_matches_speculation(event, spec) is True


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def test_observe_events_increments():
    state = SpeculativeState(active=[
        SpeculativeInterest(domain="博弈论", category="知识", status="active"),
    ])
    events = [
        {"title": "博弈论科普：囚徒困境", "event_type": "view"},
        {"title": "今天吃什么", "event_type": "view"},
        {"title": "博弈论在经济学中的应用", "event_type": "view"},
    ]
    updated, count = observe_events(events, state)
    assert count == 2
    assert updated.active[0].confirmation_count == 2
    assert len(updated.active[0].confirming_events) == 2


def test_observe_skips_non_active():
    state = SpeculativeState(active=[
        SpeculativeInterest(domain="博弈论", status="promoted"),
    ])
    events = [{"title": "博弈论科普", "event_type": "view"}]
    _, count = observe_events(events, state)
    assert count == 0


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def test_promote_ready():
    state = SpeculativeState(active=[
        SpeculativeInterest(
            domain="博弈论", status="active",
            confirmation_count=3, confirmation_threshold=3,
        ),
        SpeculativeInterest(
            domain="建筑叙事", status="active",
            confirmation_count=1, confirmation_threshold=3,
        ),
    ])
    promoted, updated = promote_ready(state)
    assert len(promoted) == 1
    assert promoted[0].domain == "博弈论"
    assert promoted[0].status == "promoted"
    assert len(updated.active) == 1
    assert updated.active[0].domain == "建筑叙事"
    assert updated.total_promoted == 1


def test_promote_none_ready():
    state = SpeculativeState(active=[
        SpeculativeInterest(domain="X", confirmation_count=1, confirmation_threshold=3),
    ])
    promoted, updated = promote_ready(state)
    assert promoted == []
    assert len(updated.active) == 1


# ---------------------------------------------------------------------------
# Expiry and cooldown
# ---------------------------------------------------------------------------


def test_expire_stale_creates_cooldown():
    now = datetime.now()
    old = now - timedelta(days=15)
    state = SpeculativeState(active=[
        SpeculativeInterest(
            domain="过期方向", status="active",
            created_at=old.isoformat(), ttl_days=14,
        ),
        SpeculativeInterest(
            domain="还没过期", status="active",
            created_at=now.isoformat(), ttl_days=14,
        ),
    ])
    rejected, updated = expire_stale(state, now, cooldown_days=30)
    assert len(rejected) == 1
    assert rejected[0].domain == "过期方向"
    assert rejected[0].status == "rejected"
    assert len(updated.active) == 1
    assert updated.active[0].domain == "还没过期"
    assert len(updated.cooldown) == 1
    assert updated.cooldown[0].domain == "过期方向"
    assert updated.total_rejected == 1


def test_expire_cleans_old_cooldowns():
    now = datetime.now()
    state = SpeculativeState(
        active=[],
        cooldown=[
            CooldownEntry(
                domain="旧冷却",
                cooldown_until=(now - timedelta(days=1)).isoformat(),
            ),
            CooldownEntry(
                domain="新冷却",
                cooldown_until=(now + timedelta(days=10)).isoformat(),
            ),
        ],
    )
    _, updated = expire_stale(state, now)
    assert len(updated.cooldown) == 1
    assert updated.cooldown[0].domain == "新冷却"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_speculative_state_roundtrip():
    state = SpeculativeState(
        active=[
            SpeculativeInterest(
                domain="博弈论", category="知识", reason="test",
                confidence=0.5, weight=0.4,
                created_at="2026-01-01T00:00:00",
                confirmation_count=2,
                experience_mode="knowledge",
                entry_load="heavy",
            ),
        ],
        cooldown=[
            CooldownEntry(
                domain="过期", rejected_at="2026-01-01",
                cooldown_until="2026-02-01",
            ),
        ],
        last_generation_at="2026-01-01T00:00:00",
        total_promoted=3,
        total_rejected=5,
    )
    data = state.to_dict()
    restored = SpeculativeState.from_dict(data)
    assert len(restored.active) == 1
    assert restored.active[0].domain == "博弈论"
    assert restored.active[0].confirmation_count == 2
    assert restored.active[0].experience_mode == "knowledge"
    assert restored.active[0].entry_load == "heavy"
    assert len(restored.cooldown) == 1
    assert restored.total_promoted == 3
    assert restored.total_rejected == 5


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_and_load_speculative_state():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(
            active=[SpeculativeInterest(domain="test", status="active")],
            total_promoted=1,
        )
        save_speculative_state(data_dir, state)

        loaded = load_speculative_state(data_dir)
        assert len(loaded.active) == 1
        assert loaded.active[0].domain == "test"
        assert loaded.total_promoted == 1


def test_load_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        state = load_speculative_state(Path(tmpdir))
        assert state.active == []
        assert state.cooldown == []


# ---------------------------------------------------------------------------
# InterestSpeculator
# ---------------------------------------------------------------------------


def test_speculator_observe():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)

        # Pre-seed a speculation
        state = SpeculativeState(active=[
            SpeculativeInterest(
                domain="博弈论", category="知识", status="active",
                created_at=datetime.now().isoformat(),
            ),
        ])
        save_speculative_state(data_dir, state)

        matches = speculator.observe([
            {"title": "博弈论入门", "event_type": "view"},
        ])
        assert matches == 1

        # Verify persisted
        reloaded = load_speculative_state(data_dir)
        assert reloaded.active[0].confirmation_count == 1


def test_speculator_ingest_seeds():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)

        added = speculator.ingest_seeds([
            {"name": "博弈论", "category": "知识", "reason": "test", "weight": 0.4},
            {"name": "科技伦理", "category": "哲学", "reason": "test2", "weight": 0.5},
        ])
        assert added == 2

        state = load_speculative_state(data_dir)
        assert len(state.active) == 2
        assert state.active[0].domain == "博弈论"


def test_speculator_ingest_seeds_dedup():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)

        speculator.ingest_seeds([
            {"name": "博弈论", "category": "知识"},
        ])
        added = speculator.ingest_seeds([
            {"name": "博弈论", "category": "知识"},  # duplicate
            {"name": "新方向", "category": "其他"},
        ])
        assert added == 1  # Only new one


def test_speculator_ingest_seeds_respects_cooldown():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(cooldown=[
            CooldownEntry(
                domain="博弈论",
                cooldown_until=(datetime.now() + timedelta(days=10)).isoformat(),
            ),
        ])
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)
        added = speculator.ingest_seeds([
            {"name": "博弈论", "category": "知识"},
        ])
        assert added == 0


def test_speculator_get_active():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(active=[
            SpeculativeInterest(domain="A", status="active"),
            SpeculativeInterest(domain="B", status="promoted"),
            SpeculativeInterest(domain="C", status="active"),
        ])
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(llm_service=None, data_dir=data_dir)
        active = speculator.get_active_speculations()
        assert len(active) == 2
        assert {s.domain for s in active} == {"A", "C"}


async def test_speculator_tick_promotes():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        state = SpeculativeState(active=[
            SpeculativeInterest(
                domain="已确认", status="active",
                confirmation_count=3, confirmation_threshold=3,
                created_at=datetime.now().isoformat(),
            ),
        ])
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir,
            generation_interval_minutes=999999,  # don't generate
        )

        from openbiliclaw.soul.profile import OnionProfile

        result = await speculator.tick(OnionProfile())
        assert len(result.promoted) == 1
        assert result.promoted[0].domain == "已确认"


async def test_speculator_tick_expires():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        old = datetime.now() - timedelta(days=20)
        state = SpeculativeState(active=[
            SpeculativeInterest(
                domain="过期的", status="active",
                created_at=old.isoformat(), ttl_days=14,
            ),
        ])
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir,
            generation_interval_minutes=999999,
        )

        from openbiliclaw.soul.profile import OnionProfile

        result = await speculator.tick(OnionProfile())
        assert len(result.rejected) == 1
        assert result.rejected[0].domain == "过期的"

        # Verify cooldown was created
        reloaded = load_speculative_state(data_dir)
        assert len(reloaded.cooldown) == 1
        assert reloaded.cooldown[0].domain == "过期的"


def test_speculator_max_active_limit():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir, max_active=3,
        )
        added = speculator.ingest_seeds([
            {"name": f"兴趣{i}", "category": "测试"}
            for i in range(10)
        ])
        assert added == 3


def test_should_generate_respects_primary_cap():
    """Skip generation when confirmed domains + active speculations >= cap."""
    from openbiliclaw.soul.profile import InterestDomain, InterestLayer, OnionProfile

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        # 12 confirmed domains + 2 active = 14, cap is 15 → should generate
        profile = OnionProfile(
            interest=InterestLayer(
                likes=[InterestDomain(domain=f"域{i}") for i in range(12)]
            )
        )
        state = SpeculativeState(active=[
            SpeculativeInterest(domain="猜A", status="active"),
            SpeculativeInterest(domain="猜B", status="active"),
        ])
        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir,
            max_primary_interests=15,
        )
        assert speculator._should_generate(state, datetime.now(), profile) is True

        # 14 confirmed + 2 active = 16 >= 15 → should NOT generate
        profile2 = OnionProfile(
            interest=InterestLayer(
                likes=[InterestDomain(domain=f"域{i}") for i in range(14)]
            )
        )
        assert speculator._should_generate(state, datetime.now(), profile2) is False


def test_should_generate_respects_secondary_cap():
    """Skip generation when confirmed specifics + active speculations >= cap."""
    from openbiliclaw.soul.profile import (
        InterestDomain,
        InterestLayer,
        InterestSpecific,
        OnionProfile,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        # 3 domains with 20 specifics each = 60, cap is 60 → should NOT generate
        profile = OnionProfile(
            interest=InterestLayer(
                likes=[
                    InterestDomain(
                        domain=f"域{i}",
                        specifics=[InterestSpecific(name=f"项{j}") for j in range(20)],
                    )
                    for i in range(3)
                ]
            )
        )
        state = SpeculativeState(active=[
            SpeculativeInterest(domain="猜A", status="active"),
        ])
        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir,
            max_secondary_interests=60,
        )
        assert speculator._should_generate(state, datetime.now(), profile) is False


async def test_force_tick_ignores_interval():
    """force_tick generates even if interval hasn't elapsed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        # Set last_generation_at to just now — tick would skip, force_tick should not
        state = SpeculativeState(
            last_generation_at=datetime.now().isoformat(),
        )
        save_speculative_state(data_dir, state)

        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir,
            generation_interval_minutes=9999,
        )

        from openbiliclaw.soul.profile import OnionProfile

        # force_tick with no LLM service won't generate, but it should run
        result = await speculator.force_tick(OnionProfile())
        # No error, returns result (empty since no LLM)
        assert result.generated == []
        assert result.promoted == []


async def test_speculator_generate_keeps_visible_experience_mix():
    class _FakeLLMService:
        async def complete_structured_task(self, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "speculations": [
                            {
                                "domain": "博弈论科普",
                                "category": "知识解释",
                                "reason": "系统性思维延伸。",
                                "bridge_type": "near",
                                "confidence": 0.59,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["纳什均衡"],
                            },
                            {
                                "domain": "AI治理",
                                "category": "社会文化",
                                "reason": "延续因果链兴趣。",
                                "bridge_type": "far",
                                "confidence": 0.57,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["监管辩论"],
                            },
                            {
                                "domain": "建筑叙事",
                                "category": "审美体验",
                                "reason": "偏好结构和空间逻辑。",
                                "bridge_type": "novel",
                                "confidence": 0.55,
                                "experience_mode": "knowledge",
                                "entry_load": "heavy",
                                "specifics": ["城市更新"],
                            },
                            {
                                "domain": "城市漫游",
                                "category": "现实观察",
                                "reason": "想从具体空间感受城市。",
                                "bridge_type": "near",
                                "confidence": 0.49,
                                "experience_mode": "wander_observe",
                                "entry_load": "light",
                                "specifics": ["街区vlog"],
                            },
                            {
                                "domain": "器物修复",
                                "category": "实操动手",
                                "reason": "对结构拆解有兴趣。",
                                "bridge_type": "near",
                                "confidence": 0.48,
                                "experience_mode": "hands_on",
                                "entry_load": "light",
                                "specifics": ["旧物翻新"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    with tempfile.TemporaryDirectory() as tmpdir:
        from openbiliclaw.soul.profile import OnionProfile

        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(
            llm_service=_FakeLLMService(),
            data_dir=data_dir,
            max_active=3,
        )

        result = await speculator.force_tick(OnionProfile())

        assert len(result.generated) == 3
        assert any(item.entry_load == "light" for item in result.generated)
        assert any(item.experience_mode != "knowledge" for item in result.generated)


def test_interval_uses_minutes():
    """Verify _should_generate uses minutes, not hours."""
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        speculator = InterestSpeculator(
            llm_service=None, data_dir=data_dir,
            generation_interval_minutes=10,
        )
        now = datetime.now()
        # 5 minutes ago → should NOT generate
        state5 = SpeculativeState(
            last_generation_at=(now - timedelta(minutes=5)).isoformat()
        )
        assert speculator._should_generate(state5, now) is False

        # 15 minutes ago → should generate
        state15 = SpeculativeState(
            last_generation_at=(now - timedelta(minutes=15)).isoformat()
        )
        assert speculator._should_generate(state15, now) is True
