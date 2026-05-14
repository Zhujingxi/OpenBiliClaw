from openbiliclaw.discovery.pool_snapshot import (
    PoolDistributionSnapshot,
    build_pool_distribution_snapshot,
)
from openbiliclaw.storage.database import Database


def test_build_pool_snapshot_marks_saturated_topics_and_styles(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    for index in range(12):
        db.cache_content(
            f"BVai{index}",
            title=f"AI item {index}",
            topic_group="AI 编程",
            style_key="deep_dive",
            franchise_key="",
            source="search",
            relevance_score=0.8,
            pool_expression="x",
            pool_topic_label="x",
        )
    for index in range(3):
        db.cache_content(
            f"BVdoc{index}",
            title=f"doc item {index}",
            topic_group="人物纪录",
            style_key="story_doc",
            source="search",
            relevance_score=0.75,
            pool_expression="x",
            pool_topic_label="x",
        )

    snapshot = build_pool_distribution_snapshot(
        db,
        pool_target_count=60,
        source_targets={"bilibili": 48, "xiaohongshu": 6, "douyin": 6},
    )

    assert snapshot.pool_available_count == 15
    assert "AI 编程" in snapshot.saturated_topics
    assert "deep_dive" in snapshot.saturated_styles
    assert snapshot.source_deficits["bilibili"] == 33


def test_prompt_hints_caps_positive_source_deficits_by_priority():
    snapshot = PoolDistributionSnapshot(
        pool_target_count=100,
        pool_available_count=20,
        source_targets={},
        source_counts={},
        source_deficits={
            "source-01": 2,
            "source-02": 9,
            "source-03": 0,
            "source-04": -1,
            "source-05": 5,
            "source-06": 11,
            "source-07": 8,
            "source-08": 6,
            "source-09": 3,
            "source-10": 7,
            "source-11": 4,
            "source-12": 10,
        },
    )

    hints = snapshot.to_prompt_hints()

    assert hints["source_deficits"] == {
        "source-06": 11,
        "source-12": 10,
        "source-02": 9,
        "source-07": 8,
        "source-10": 7,
        "source-08": 6,
        "source-05": 5,
        "source-11": 4,
    }
