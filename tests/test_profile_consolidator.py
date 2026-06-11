"""Tests for LLM-judged like/dislike topic consolidation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from openbiliclaw.soul.consolidator import ProfileConsolidator


class _FakeLayer:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.save_count = 0

    def save(self) -> None:
        self.save_count += 1


class _FakeMemory:
    def __init__(
        self,
        preference: dict[str, Any],
        soul: dict[str, Any] | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._layers = {
            "preference": _FakeLayer(preference),
            "soul": _FakeLayer(soul or {}),
        }
        self._data_dir = data_dir
        self.synced_profiles: list[Any] = []

    def get_layer(self, name: str) -> _FakeLayer:
        return self._layers[name]

    def sync_profile_files(self, profile: Any) -> None:
        self.synced_profiles.append(profile)


class _StubEmbedding:
    """Names listed in the same group get identical vectors (cos=1)."""

    def __init__(self, groups: list[list[str]]) -> None:
        self._vectors: dict[str, list[float]] = {}
        dims = len(groups) + 64
        for gi, group in enumerate(groups):
            vec = [0.0] * dims
            vec[gi] = 1.0
            for name in group:
                self._vectors[name] = vec
        self._dims = dims
        self._next_axis = len(groups)

    async def embed(self, text: str) -> list[float]:
        if text not in self._vectors:
            vec = [0.0] * self._dims
            vec[self._next_axis] = 1.0
            self._next_axis += 1
            self._vectors[text] = vec
        return self._vectors[text]


class _StubLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_user_input = ""

    async def complete_structured_task(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_user_input = str(kwargs.get("user_input", ""))
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def _interest(name: str, weight: float, category: str = "科技", **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "category": category,
        "weight": weight,
        "first_seen": extra.get("first_seen", "2026-01-01T00:00:00"),
        "last_seen": extra.get("last_seen", "2026-06-01T00:00:00"),
        "source": "watch history",
    }


async def test_rule_layer_merges_same_name_across_categories(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("人工智能", 0.98, "技术", first_seen="2026-02-01T00:00:00"),
                _interest("人工智能", 0.89, "科技", first_seen="2026-01-01T00:00:00"),
                _interest("篮球", 0.95, "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    consolidator = ProfileConsolidator(memory=memory, llm_service=None, data_dir=tmp_path)

    report = await consolidator.run(dry_run=False)

    interests = memory.get_layer("preference").data["interests"]
    names = [item["name"] for item in interests]
    assert names == ["人工智能", "篮球"]
    ai = interests[0]
    assert ai["weight"] == 0.98
    assert ai["category"] == "技术"  # higher-weight entry wins the metadata
    assert ai["first_seen"] == "2026-01-01T00:00:00"  # earliest survives
    assert len(report.rule_merges) == 1
    assert memory.get_layer("preference").save_count == 1


async def test_llm_merge_applies_weight_and_timestamps(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97, first_seen="2026-03-01T00:00:00"),
                _interest(
                    "智能体开发与实现",
                    0.88,
                    first_seen="2026-01-15T00:00:00",
                    last_seen="2026-06-10T00:00:00",
                ),
                _interest("篮球", 0.95, "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert llm.calls == 1
    interests = memory.get_layer("preference").data["interests"]
    names = [item["name"] for item in interests]
    assert names == ["智能体开发", "篮球"]
    merged = interests[0]
    assert merged["weight"] == 0.97
    assert merged["first_seen"] == "2026-01-15T00:00:00"
    assert merged["last_seen"] == "2026-06-10T00:00:00"
    assert report.merges and report.merges[0]["canonical"] == "智能体开发"
    # Run record written for revert
    runs = list((tmp_path / "consolidation_runs").glob("*.json"))
    assert len(runs) == 1
    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["rename_map"] == {"智能体开发与实现": "智能体开发"}
    assert len(record["before"]["interests"]) == 3
    # Audit entry appended
    assert "画像整理" in (tmp_path / "soul_changelog.md").read_text(encoding="utf-8")


async def test_dislike_merge_keeps_frontmost_position(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [],
            "disliked_topics": ["新雷点", "偶像练习室物料", "中间项", "偶像团体练习室内容"],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [],
            "dislikes": [
                {
                    "cluster_id": "D1",
                    "op": "merge",
                    "members": ["偶像练习室物料", "偶像团体练习室内容"],
                    "canonical": "偶像练习室物料",
                }
            ],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["偶像练习室物料", "偶像团体练习室内容"]]),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    assert memory.get_layer("preference").data["disliked_topics"] == [
        "新雷点",
        "偶像练习室物料",
        "中间项",
    ]


async def test_generalized_dislike_canonical_is_rejected(tmp_path: Path) -> None:
    topics = ["一个案例反复切悬念拖时长", "先抛结论再补一堆模糊概念"]
    memory = _FakeMemory(
        {"interests": [], "disliked_topics": list(topics)},
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [],
            "dislikes": [
                {
                    "cluster_id": "D1",
                    "op": "merge",
                    "members": list(topics),
                    "canonical": "低质内容",
                }
            ],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([list(topics)]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert memory.get_layer("preference").data["disliked_topics"] == topics
    assert report.merges == []
    assert report.rejected_clusters and "banned" in report.rejected_clusters[0]


async def test_hallucinated_member_rejects_cluster(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "不存在的标签"],
                    "canonical": "智能体开发",
                },
                {"cluster_id": "L1", "op": "keep", "name": "智能体开发与实现"},
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    names = [item["name"] for item in memory.get_layer("preference").data["interests"]]
    assert names == ["智能体开发", "智能体开发与实现"]
    assert report.rejected_clusters


async def test_incomplete_coverage_rejects_cluster(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("游戏资讯A", 0.9),
                _interest("游戏资讯B", 0.89),
                _interest("游戏资讯C", 0.88),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["游戏资讯A", "游戏资讯B"],
                    "canonical": "游戏资讯A",
                }
                # 游戏资讯C neither merged nor kept -> coverage violation
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["游戏资讯A", "游戏资讯B", "游戏资讯C"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=False)

    assert len(memory.get_layer("preference").data["interests"]) == 3
    assert report.rejected_clusters and "cover" in report.rejected_clusters[0]


async def test_dry_run_never_writes(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    report = await consolidator.run(dry_run=True)

    assert report.merges  # the proposal is reported...
    assert len(memory.get_layer("preference").data["interests"]) == 2  # ...but nothing changed
    assert memory.get_layer("preference").save_count == 0
    assert not (tmp_path / "consolidation_state.json").exists()
    assert not (tmp_path / "consolidation_runs").exists()


async def test_no_merge_memory_skips_judged_clusters(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [
                _interest("篮球", 0.95, "体育"),
                _interest("NBA", 0.9, "体育"),
            ],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {"cluster_id": "L1", "op": "keep", "name": "篮球"},
                {"cluster_id": "L1", "op": "keep", "name": "NBA"},
            ],
            "dislikes": [],
        }
    )
    embedding = _StubEmbedding([["篮球", "NBA"]])
    consolidator = ProfileConsolidator(
        memory=memory, llm_service=llm, embedding_service=embedding, data_dir=tmp_path
    )

    first = await consolidator.run(dry_run=False)
    assert first.clusters_sent == 1
    assert llm.calls == 1

    second = await consolidator.run(dry_run=False)
    assert second.clusters_sent == 0
    assert llm.calls == 1  # no second LLM call


async def test_run_if_due_throttles_and_skips_clean_input(tmp_path: Path) -> None:
    memory = _FakeMemory(
        {
            "interests": [_interest("篮球", 0.95, "体育")],
            "disliked_topics": [],
        },
        data_dir=tmp_path,
    )
    consolidator = ProfileConsolidator(
        memory=memory, llm_service=None, data_dir=tmp_path, min_interval_seconds=12 * 3600
    )
    t0 = datetime(2026, 6, 12, 3, 0, 0)

    first = await consolidator.run_if_due(now=t0)
    assert first.ran

    throttled = await consolidator.run_if_due(now=t0 + timedelta(hours=1))
    assert throttled.throttled

    clean = await consolidator.run_if_due(now=t0 + timedelta(hours=13))
    assert clean.skipped_clean

    # New input -> due again
    memory.get_layer("preference").data["disliked_topics"].append("新雷点")
    dirty = await consolidator.run_if_due(now=t0 + timedelta(hours=26))
    assert dirty.ran


async def test_apply_rebuilds_onion_tree(tmp_path: Path) -> None:
    from openbiliclaw.soul.profile import OnionProfile

    profile = OnionProfile()
    profile.populate_from_flat_preference(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        }
    )
    memory = _FakeMemory(
        {
            "interests": [
                _interest("智能体开发", 0.97),
                _interest("智能体开发与实现", 0.88),
            ],
            "disliked_topics": [],
        },
        soul=profile.to_dict(),
        data_dir=tmp_path,
    )
    llm = _StubLLM(
        {
            "likes": [
                {
                    "cluster_id": "L1",
                    "op": "merge",
                    "members": ["智能体开发", "智能体开发与实现"],
                    "canonical": "智能体开发",
                }
            ],
            "dislikes": [],
        }
    )
    consolidator = ProfileConsolidator(
        memory=memory,
        llm_service=llm,
        embedding_service=_StubEmbedding([["智能体开发", "智能体开发与实现"]]),
        data_dir=tmp_path,
    )

    await consolidator.run(dry_run=False)

    rebuilt = OnionProfile.from_dict(dict(memory.get_layer("soul").data))
    specific_names = [spec.name for dom in rebuilt.interest.likes for spec in dom.specifics]
    assert "智能体开发与实现" not in specific_names
    assert memory.synced_profiles  # sync_profile_files invoked
