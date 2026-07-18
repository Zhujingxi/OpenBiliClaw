# Runtime Pool Target 300 Design

## Problem

当前系统对 discovery pool 的两个目标不一致：

- 运行时默认 `scheduler.pool_target_count = 150`
- 首次 `init` 只尽量补到 `50`

这会导致用户第一次开始使用时，池子偏薄；进入稳定运行后，后台目标又和首轮体验不一致。

## Goal

把候选池策略改成两层目标：

1. **首次初始化保底 100 条**
2. **运行时持续以 300 条为目标补货**

这样既能控制首次初始化耗时，也能让日常使用时“换一批”和自动续页更稳定。

## Options

### Option 1: 只把运行时目标改到 300

优点：

- 改动最小

缺点：

- 首次 `init` 仍然可能只有 50 条，首轮体验和日常运行脱节

### Option 2: 运行时 300，首次 100

优点：

- 首次启动成本可控
- 运行中有更高的池子余量
- 明确区分“冷启动保底”和“稳定运行目标”

缺点：

- 首次初始化结束后，后台还需要继续追到 300

### Option 3: 首次和运行时都改到 300

优点：

- 语义最一致

缺点：

- 首次 `init` 耗时和 discover 成本明显提高

**Recommendation:** 采用 Option 2。

## Design

### A. 运行时默认目标改为 300

- `SchedulerConfig.pool_target_count` 默认值从 `150` 改到 `300`
- `RuntimeRefreshController.pool_target_count` 默认值同步改到 `300`
- `config.example.toml` 与配置文档同步更新

### B. 首次初始化保底改为 100

- `_INIT_POOL_TARGET_COUNT` 从 `50` 改到 `100`
- `_run_init_discovery_backfill(..., target_pool_count=100)` 的默认值同步改掉
- CLI 文档里的示例输出、说明文本一并更新

### C. 维持现有护栏不变

- `scheduler.pool_target_count` 的配置范围仍为 `1..300`
- 单轮 refresh discover 回填上限仍为 `60`

这样不会因为默认目标提高而让单轮补货突然失控。

## Testing Strategy

- `tests/test_config.py`
  - 锁定 `Config().scheduler.pool_target_count == 300`
- `tests/test_cli.py`
  - 锁定首次 `init` 的阶段补货目标是 `100`
  - 锁定首阶段达到目标后会提前停止
- 如有需要，补充文档更新以避免默认值与实现脱节
