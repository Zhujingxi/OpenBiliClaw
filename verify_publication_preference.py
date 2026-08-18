#!/usr/bin/env python3
"""手动验证发布日期偏好功能的脚本"""

from datetime import UTC, date, datetime, timedelta
from openbiliclaw.recommendation.publication_preference import (
    PRESET_LAST_7_DAYS,
    PRESET_CUSTOM,
    PublicationDatePreference,
    evaluate_publication_preference,
    resolve_publication_window,
)

def test_basic_functionality():
    """测试基本功能"""
    print("=" * 60)
    print("测试1: 基本功能验证")
    print("=" * 60)

    # 测试1: 创建偏好配置
    pref = PublicationDatePreference(
        preset=PRESET_LAST_7_DAYS,
        weight=1.0
    )
    print(f"✓ 创建偏好配置: {pref.preset}, weight={pref.weight}")

    # 测试2: 解析窗口
    window = resolve_publication_window(pref)
    print(f"✓ 解析时间窗口: {window.start_utc} 到 {window.end_utc}")

    # 测试3: 评估B站内容（范围内）
    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).isoformat()

    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at=yesterday,
        preference=pref,
        now=now
    )
    print(f"✓ 昨天的B站视频: in_range={decision.in_range}, eligible={decision.eligible}")
    assert decision.in_range is True
    assert decision.eligible is True

    # 测试4: 评估B站内容（范围外）
    old_date = (now - timedelta(days=30)).isoformat()
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at=old_date,
        preference=pref,
        now=now
    )
    print(f"✓ 30天前的B站视频: in_range={decision.in_range}, eligible={decision.eligible}")
    assert decision.in_range is False
    assert decision.eligible is False  # weight=1.0 严格模式

    # 测试5: 非B站内容保持中性
    decision = evaluate_publication_preference(
        source_platform="youtube",
        published_at=old_date,
        preference=pref,
        now=now
    )
    print(f"✓ YouTube内容: in_range={decision.in_range}, eligible={decision.eligible}")
    assert decision.in_range is True
    assert decision.eligible is True

    print("\n✅ 基本功能测试通过！\n")


def test_custom_range():
    """测试自定义日期范围"""
    print("=" * 60)
    print("测试2: 自定义日期范围")
    print("=" * 60)

    pref = PublicationDatePreference(
        preset=PRESET_CUSTOM,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        weight=0.5  # 软模式
    )
    print(f"✓ 自定义范围: {pref.start_date} 到 {pref.end_date}")

    # 测试范围内
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="2024-06-15T12:00:00Z",
        preference=pref,
        now=datetime.now(UTC)
    )
    print(f"✓ 2024年6月视频: in_range={decision.in_range}, multiplier={decision.score_multiplier}")
    assert decision.in_range is True
    assert decision.score_multiplier == 1.0

    # 测试范围外（软模式）
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="2023-06-15T12:00:00Z",
        preference=pref,
        now=datetime.now(UTC)
    )
    print(f"✓ 2023年6月视频: in_range={decision.in_range}, multiplier={decision.score_multiplier}, eligible={decision.eligible}")
    assert decision.in_range is False
    assert decision.score_multiplier == 0.5  # 1.0 - weight
    assert decision.eligible is True  # 软模式仍可服务

    print("\n✅ 自定义范围测试通过！\n")


def test_scoring_impact():
    """测试评分影响"""
    print("=" * 60)
    print("测试3: 评分影响模拟")
    print("=" * 60)

    pref_soft = PublicationDatePreference(preset=PRESET_LAST_7_DAYS, weight=0.5)
    pref_strict = PublicationDatePreference(preset=PRESET_LAST_7_DAYS, weight=1.0)

    now = datetime.now(UTC)
    old_video = (now - timedelta(days=30)).isoformat()

    # 模拟一个原始分数
    original_score = 0.75

    # 软模式
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at=old_video,
        preference=pref_soft,
        now=now
    )
    soft_score = original_score * decision.score_multiplier
    print(f"✓ 软模式 (weight=0.5): {original_score} → {soft_score}")
    assert soft_score == 0.375

    # 严格模式
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at=old_video,
        preference=pref_strict,
        now=now
    )
    strict_score = original_score * decision.score_multiplier if decision.eligible else 0
    print(f"✓ 严格模式 (weight=1.0): {original_score} → {strict_score} (eligible={decision.eligible})")
    assert strict_score == 0
    assert decision.eligible is False

    print("\n✅ 评分影响测试通过！\n")


def test_edge_cases():
    """测试边界情况"""
    print("=" * 60)
    print("测试4: 边界情况")
    print("=" * 60)

    now = datetime.now(UTC)

    # 空字符串发布时间
    pref = PublicationDatePreference(preset=PRESET_LAST_7_DAYS, weight=1.0)
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="",
        preference=pref,
        now=now
    )
    print(f"✓ 空发布时间: in_range={decision.in_range}, eligible={decision.eligible}")
    assert decision.in_range is False
    assert decision.eligible is False

    # 无效发布时间
    decision = evaluate_publication_preference(
        source_platform="bilibili",
        published_at="not-a-date",
        preference=pref,
        now=now
    )
    print(f"✓ 无效发布时间: in_range={decision.in_range}, eligible={decision.eligible}")
    assert decision.in_range is False

    # 空平台（应该不是B站）
    decision = evaluate_publication_preference(
        source_platform="",
        published_at="2020-01-01T00:00:00Z",
        preference=pref,
        now=now
    )
    print(f"✓ 空平台: in_range={decision.in_range}, eligible={decision.eligible}")
    # 空平台不会被识别为bilibili，应该保持中性

    print("\n✅ 边界情况测试通过！\n")


if __name__ == "__main__":
    print("\n" + "🚀 开始验证发布日期偏好功能 " + "\n")

    try:
        test_basic_functionality()
        test_custom_range()
        test_scoring_impact()
        test_edge_cases()

        print("=" * 60)
        print("🎉 所有验证测试通过！")
        print("=" * 60)
        print("\n核心功能已验证：")
        print("  ✅ 时间窗口解析正确")
        print("  ✅ B站内容正确过滤")
        print("  ✅ 非B站内容保持中性")
        print("  ✅ 严格/软模式按预期工作")
        print("  ✅ 评分乘数计算正确")
        print("  ✅ 边界情况处理得当")
        print("\n可以放心提交PR！🚀\n")

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}\n")
        raise
    except Exception as e:
        print(f"\n❌ 发生错误: {e}\n")
        raise
