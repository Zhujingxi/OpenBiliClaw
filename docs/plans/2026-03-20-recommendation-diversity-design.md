# Recommendation Diversity Design

**Problem:** 当前 `generate` 推荐链路在候选充足时，仍会被高分 `related_chain` 和单一 `related:*` topic 吞掉大部分名额，导致“推荐内容丰富度”明显不足。

**Observed Evidence:**

- `Recommendation candidate summary (generate)` 多次显示候选池有 34 条，来源分布通常是 `trending: 18, related_chain: 14, search: 2`
- `Recommendation picked summary (generate)` 多次稳定收敛为 `related_chain: 6, trending: 2, search: 2`
- 同一 `related:*` topic 在单批推荐中能占到 4 到 5 条

**Root Cause:**

1. `generate` 路径从缓存读候选时没有像 `reshuffle` 一样先做 source-level balance。
2. `_select_diversified_batch()` 在最后的“补满”阶段会直接把之前因 topic/source/style 超限而 deferred 的候选重新塞回去，导致前面的多样性约束被打穿。

**Design Decision:**

- `generate` 路径与 `reshuffle` / `append` 对齐，先对缓存候选做 source balance，再交给统一的 diversified selection。
- diversified selection 改成分阶段放宽：
  1. 严格遵守 topic/style/source cap
  2. 允许重复 source，但仍遵守 cap
  3. 放宽 style cap，但保留 topic cap
  4. 放宽 source cap，但保留 topic cap
  5. 只有在候选确实不够时，才最终放宽 topic cap 补满

**Expected Outcome:**

- `generate` 批次里不再出现单一 `related:*` topic 连续占 4 到 5 个名额
- 高分 `related_chain` 仍然保留优势，但不会持续压制其它来源和主题
- `reshuffle` / `append` 与 `generate` 的多样性口径保持一致
