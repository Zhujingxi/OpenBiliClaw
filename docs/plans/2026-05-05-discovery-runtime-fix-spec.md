# 2026-05-05 — Discovery / Runtime 稳定性修复 Spec

> 起源：2026-05-05 一份 ~43 分钟的 daemon 会话日志（`openbiliclaw.log`
> + `agent-bootstrap.log`）暴露 14 个独立问题。其中 5 个已在
> v0.3.46-v0.3.50 修完并 push 到 main，剩余 **9 个**形成本 spec 范围。
>
> 本 spec 为多波修复给出完整契约——每个问题都标注：现象、根因、修法、
> 改动点、验收方式、风险、版本计划。最后给出执行波次。

## 0. 已修复（仅作上下文，不在本 spec 范围）

| 版本 | 问题 | 修法摘要 |
|------|------|---------|
| v0.3.46 | init 期 7 min profile-not-ready 假 ERROR/WARNING 轰炸 | `is_profile_ready()` 前置 gate；profile-ready 转换钩子立即 classify |
| v0.3.47 | expression 精排排在 discovery 末尾，95% popup 看模板 | per-strategy fire + asyncio.gather 内部并发 + batch_size 8→30 |
| v0.3.48 | XHS 自己发的笔记被推回（屎屎/三花/165㎡） | 扩展抓 self_info → 后端 `_cache_xhs_notes` + event propagation 过滤 |
| v0.3.49 | 惊喜推荐 35 条/43 min，60% 是"相对常规" | DEFAULT_DELIGHT_THRESHOLD 0.57→0.70 / CONSERVATIVE 0.67→0.80 |
| v0.3.50 | 单批 13 条同 UP（张雪机车），franchise_key 填了不用 | eval_batch ≤4 / related_chain ≤3/UP / pool ≤10/franchise |

---

## 1. 待修问题清单（9 项）

### 🔴 高严重度

- **U1**：discovery `evaluate_batch` 8-16 min/批（`reasoning_effort` 滥用）
- **U2**：discovery 单 round 丢弃 ~90% 候选（300+→30 截断）
- **U3**：B站 search 实质失效（141 v_voucher / 9 次 0 results）
- **U4**：Ollama 启动期 502 风暴 + 误用为 chat fallback

### 🟠 中等严重度

- **U5**：eval_batch 无 style cap（跟 v0.3.50 franchise 同病）
- **U6**：MMR embedding cache 启动后 31 min 0% 命中
- **U7**：speculator quality gate 全 drop（confidence 0.35 vs 阈值 0.4）
- **U8**：xhs_producer 整 43 min 只跑 1 轮

### 🟡 较轻

- **U9**：topic_group supergroup 合并只在 serve 时跑

---

## 2. 逐项 Spec

### U1 — 关闭 discovery `reasoning_effort`，eval 提速 30×

**现象**：27 次 `discovery.evaluate_batch` LLM 调用累计 ~3 小时 LLM 思考时间，最长单批 991s（16.5 min）。output tokens 8000-18000 / 30 items（reasoning trace 主导）。

**根因**：deepseek-v4-flash 默认开启 `reasoning_effort`。但 evaluate_batch 任务是结构化打分（score / reason / topic_group / style_key / franchise_key），**不需要思维链**——LLM 写一段思考链的成本远大于直接给 JSON。

**修法**：
1. 在 `discovery.engine._evaluate_batch` 调 `complete_structured_task` 时显式传 `reasoning_effort=""`（关闭）。
2. 同时给 `recommendation.evaluate_batch`（XHS classify_pool_backlog）施加同样设置。
3. `recommendation.write_expression` 也是结构化输出，同样关。
4. **保留 reasoning** 给真正需要的：`soul.speculate` / `soul.awareness` / `recommendation.delight_score`（这些需要 LLM 推理）。

**改动点**：
- `src/openbiliclaw/discovery/engine.py:_evaluate_batch`（1 处加 `reasoning_effort=""` kwarg）
- `src/openbiliclaw/recommendation/engine.py:_classify_batch`（同上）
- `src/openbiliclaw/recommendation/engine.py:_precompute_batch`（同上）
- 验证 `LLMService.complete_structured_task` 支持 `reasoning_effort` 透传。如不支持，需要先在 LLM service 层加参数。

**验收**：
- 跑一次 daemon 30 分钟，`discovery.evaluate_batch` 单次 elapsed < 60s
- output tokens 单批从 8000-18000 降到 1500-3000
- `topics: K uniq, top=X×N` 数据质量不退化（LLM 评估精度可观察）

**风险**：
- 关 reasoning 可能导致评分质量轻度下降——但日志看 LLM 实际产出（topic_group 是字面截断，franchise 经常空）已经不咋样，关掉影响很小
- 长输出 reasoning 内容贡献的"think and self-correct" 没了，**JSON 解析错误率** 可能小幅上升 → tolerant parser 已经能兜（多 shape 解析）

**复杂度**：1-2 行 + 测试，~15 min。

**版本**：**v0.3.51**。

---

### U2 — discovery 单 round 候选并发评估

**现象**：日志反复出现 `evaluate_content_batch: truncating 300+ -> 30 items`，最高 480→30。**discovery 拉到的 90% 候选被丢弃**——里面可能有不少好内容。

**根因**：`evaluate_content_batch` 永远只跑一个 batch（30 items）。truncation 是因为单批 LLM 调用太慢（U1 同根：~16 min），不敢并发跑多批。

**修法**：
1. **依赖 U1 先做**——reasoning 关掉后单批 30s 完成。
2. 改 `evaluate_content_batch` 单 round 跑 N 个 batch（默认 3）×30 items = 90 items 并发评估。
3. `asyncio.gather` 跑这 N 个 batch；`run_llm` semaphore 控制实际并发。
4. 把 truncation cap 从 30 提到 90（或参数化）。

**改动点**：
- `src/openbiliclaw/discovery/engine.py:evaluate_content_batch`（重写循环为 gather）
- `_BATCH_EVAL_PARALLEL_BATCHES = 3` 新常量
- 单元测试：mock LLM 计数 batch 调用，断言 90 items 触发 3 个 batch

**验收**：
- 同样 ~480 个候选，evaluate_content_batch 现在评估 ~90 个（30→90，3×）
- 总耗时不增加（并发跑），LLM 成本 3× 但产出 3× 候选评估覆盖

**风险**：
- LLM 月成本 3×（仅这一项），可能让 daily LLM 花费从 ¥0.5 涨到 ¥1.5——需要跟用户确认是否接受
- 大池子情况下并发可能撞 provider rate limit → llm_evaluation_concurrency 已有 cap，应该兜得住

**复杂度**：30-50 行，~30 min。

**版本**：**v0.3.52**（U1 之后）。

---

### U3 — B 站 search v_voucher 退避 + trending fallback

**现象**：`Search got v_voucher challenge` 出现 141 次（每分钟 3+ 次）。`Search: 8 queries, 0 API results, 0 unique candidates` 出现 9 次（**完整一轮 search 拿不到任何结果**）。

**根因**：B 站 WBI 风控不定期挑战。当前代码命中 challenge 后**立即重试**，再次命中再立即重试——形成连环挑战。每次连环空跑同时 LLM 已经付费生成了 keyword（`discovery.search.queries` 16 次 ≈ ¥0.20）。

**修法**：
1. `bilibili.api` 命中 v_voucher 后**指数退避**：第 1 次 1s / 第 2 次 5s / 第 3 次 30s / 第 4 次直接放弃。
2. Search round 整体连续 3 次返 0 → 当 round 主动 abort，落 `discovery.search.skipped: v_voucher_storm` INFO 日志。
3. discovery `_run_strategies` 检测到 search round 0 candidates 时，**自动 promote trending 多拉一波**（trending 不需要 wbi）。

**改动点**：
- `src/openbiliclaw/bilibili/api.py`（v_voucher retry policy）
- `src/openbiliclaw/discovery/strategies/search.py`（round 主动 abort）
- `src/openbiliclaw/runtime/refresh.py`（fallback to trending）

**验收**：
- 模拟连续 v_voucher → 第 4 次主动放弃，log 一条 INFO
- 运行 30 min daemon，搜索失败时观察 trending 是否补位
- LLM `discovery.search.queries` 调用数量减半（empty round 不再生成 keyword）

**风险**：
- 退避太长可能错过实际有效的搜索窗口 → 配置化 retry policy
- B 站 WBI 算法变化时这套逻辑可能跟不上 → 单独 metric 跟踪 v_voucher 命中率

**复杂度**：50-80 行，~1 小时。

**版本**：**v0.3.55**（独立波次）。

---

### U4 — Ollama 启动期 502 风暴 + chat fallback 摘除

**现象**：daemon 启动头 90s 内 9 次 502 Bad Gateway 命中 `localhost:11434/v1/chat/completions`。引发 speculator / awareness analyzer 连锁 fail。Ollama 实际是 **embedding-only** 配置但被错误用作 chat fallback——v0.3.15 修过的同病回归。

**根因 1**：Ollama 服务在加载 bge-m3 模型期间 `/v1/chat/completions` 端点不可用（模型未加载）。

**根因 2**：LLM provider registry 把 ollama 同时注册为 embedding + chat fallback。当主 chat provider（deepseek）短时不可达时，回退到 ollama，但 ollama 不在 `supports_chat=True` 名单里。

**修法**：
1. **Startup health-check Ollama**：daemon 启动时 GET `/api/tags`，wait 直到 200 OR timeout（30s）。期间所有需要 ollama embedding 的调用 throttle / queue。
2. **Provider registry 修复**：明确把 ollama 标记 `supports_chat=False`（或只在用户显式 enable 时启用）。
3. fallback chain 跳过 `supports_chat=False` 的 provider。

**改动点**：
- `src/openbiliclaw/api/runtime_context.py:restart_background_tasks`（startup wait 期间 Ollama）
- `src/openbiliclaw/llm/registry.py`（provider supports_chat 标记 + fallback chain skip）
- `src/openbiliclaw/llm/ollama_provider.py`（声明 `supports_chat=False`）

**验收**：
- 启动头 30s 内 0 次 502 命中（health-check pass 之后才开始用）
- speculator / awareness 启动期不再因 502 失败（profile 还没建好不跑，建好后 ollama 已 ready）
- 回归测试：mock provider registry，断言 ollama 不出现在 chat fallback chain

**风险**：
- health-check 有 timeout，超时后系统怎么处理？fallback 路径要明确（embedding 退化到 gemini/openai）
- 用户可能就是把 ollama 配为 chat（少数）→ 提供 explicit opt-in 配置

**复杂度**：80-120 行，~1.5 小时。

**版本**：**v0.3.54**。

---

### U5 — eval_batch 加 style cap（跟 franchise 同形）

**现象**：日志统计 13 次单 batch single style ≥ 7 条（≥23%），最高 fun_variety×10/30=33%、story_doc×12/30=40%。eval_batch 已经有 franchise cap（v0.3.50），**没有 style cap**。

**根因**：与 v0.3.50 一模一样——LLM 给所有 30 item 标了 style_key，但 eval_batch 不消费这个字段做 cap。

**修法**：
1. `_evaluate_batch` 内部加 style cap，跟 v0.3.50 franchise cap 同形：
   ```python
   _BATCH_STYLE_CAP: int = 8  # 8/30 = 27%, 比 fun_variety×10 的 33% 紧
   ```
2. 按 `style_key` 分桶，超额按 score drop。
3. INFO log：`eval_batch style cap: dropped N (cap=8/style; offenders=fun_variety×10)`

**改动点**：
- `src/openbiliclaw/discovery/engine.py`（新常量 + `_evaluate_batch` 加 style 分桶逻辑）
- `tests/test_discovery_engine.py`（回归测试，跟 franchise cap 测试同形）

**验收**：
- 模拟 9 条同 style → cap 触发，drop 1
- 统计后续日志 top_style_share ≤ 0.27

**风险**：低。逻辑跟刚做完的 franchise cap 几乎一致。

**复杂度**：50 行，~20 min。

**版本**：**v0.3.51**（跟 U1 一波）。

---

### U6 — MMR embedding cache prewarm 重试

**现象**：daemon 启动后头 31 分钟（00:34-01:05）所有 reshuffle 显示 `MMR embedding fetch: coverage=0/N elapsed=0ms`——cache 完全没命中。01:05:37 之后才开始命中（31/40）。

**根因**：v0.3.45 prewarm 任务在 startup 触发（`prewarm_pool_mmr_embeddings`），但启动期 Ollama 还在 502（U4 同根），prewarm 任务全部 fail。**fail 后没重试**——直到下次 refresh tick 才补跑（30 min 后）。

**修法**：
1. `prewarm_pool_mmr_embeddings` 失败时，**指数退避重试**（30s / 2min / 10min）。
2. 配合 U4：health-check 通过之前 prewarm 任务排队，通过后立即跑。

**改动点**：
- `src/openbiliclaw/recommendation/engine.py:prewarm_pool_mmr_embeddings`（重试机制）
- `src/openbiliclaw/api/runtime_context.py`（startup 调度）

**验收**：
- 启动后 30s 内 prewarm 第一次跑（即便 ollama 还没 ready，会重试）
- 5 分钟内 cache coverage 达到 ≥80%
- 回归测试：mock embedding fail 1 次，断言 30s 后重试

**风险**：低。

**复杂度**：30-50 行，~30 min。配合 U4 一起做最划算。

**版本**：**v0.3.54**（跟 U4 一波）。

---

### U7 — speculator quality gate 阈值降到 0.3（或 prompt rubric 调整）

**现象**：speculator force_tick 跑了一次，**5/5 candidates 全被 quality gate drop**（`confidence=0.35 < 0.4`）。LLM 像被钉死在 0.35。

**根因 A**：speculator prompt 给 LLM 的 confidence 标尺是「0.3-0.6，越有把握越高」。LLM 对未确认兴趣自然偏保守，只敢给 0.35。
**根因 B**：quality gate 阈值 0.4，**正好刚好高于 LLM 实际产出 0.35**。

**修法（二选一）**：
1. **快路 (推荐)**：gate 0.4 → 0.3——尊重 LLM 实际表达的不确定性，让 speculator 至少有产出。
2. **深路**：调整 prompt rubric，给 LLM 更明确的 high-confidence anchor（例：「user 已 like 同主题 ≥3 条 → confidence 0.5+」），让 LLM 敢给高分。

**改动点（快路）**：
- `src/openbiliclaw/soul/speculator.py`（quality gate 常量）
- `tests/test_speculator.py`（验证 0.35 不再被 drop）

**验收**：
- 同样的 daemon 跑一遍，speculator force_tick 至少 promote 1+ candidate
- prob test： mock LLM 返 confidence=0.35 → gate 通过

**风险**：低。speculator 弱信号探针，gate 放宽允许更多假设进入"探查中"，不会立即落地为兴趣（promote/reject 还有后续闭环）。

**复杂度**：1 行常量 + 测试，~10 min。

**版本**：**v0.3.53**。

---

### U8 — xhs_producer throttle 配置审视

**现象**：`xhs producer enqueued 5/5 search tasks` 整个 43 分钟会话**只触发 1 次**（00:42:08）。后续 ticks 没再产 task，XHS 池子停止更新。

**根因**：xhs_producer 内部有个 throttle（前一轮还没"消化"完不再发）。日志看不到具体阻塞原因，需要源码确认：
- 也许是 daily_search_budget 用完
- 也许是 task_interval_seconds 过长
- 也许是 in-flight task 没清理

**修法**：
1. 先**加 INFO 日志**：tick 时打印 `xhs_producer skip: reason=daily_budget_exhausted/throttled/in_flight`。
2. 根据日志结果决定调整哪个常量。
3. 如果是 daily_budget=20 用完了 → 配置项透出
4. 如果是 task_interval=45min 太长 → 调短到 5-10min

**改动点**：
- `src/openbiliclaw/runtime/xhs_producer.py`（加 skip-reason 日志）
- 可能 `config.toml` 默认值调整

**验收**：
- 跑 daemon 1 小时，xhs_producer 触发次数 ≥3
- 日志能解释每次 skip 原因

**风险**：低（先观察后调整）。

**复杂度**：先观察 ~10 min，调整 ~10 min。

**版本**：**v0.3.53**（跟 U7 一波）。

---

### U9 — topic_group supergroup 入池阶段合并

**现象**：discovery LLM 输出 "动漫"/"动漫杂谈"/"动漫二次元" 等同主题的不同字面表达，进入 pool 时各自独立。`_supergroup_canonical_map` 合并机制**只在 serve 时跑**——pool 在数据库层面看起来"主题分散"，是假象。

**根因**：v0.3.0 supergroup 设计是 serve-time merging（每次 reshuffle 跑），ingest 阶段不合并。

**修法**：
1. 把 `_supergroup_canonical_map` 应用到 `cache_content` 之前的 hook：
   - 入池前查 map → 如果命中，把 `topic_group` 替换为 canonical 形式
2. 注意保持 prewarm 节奏：map 是 background task 计算的，可能某些 ingest 时还没就绪 → 第一次入池可能用不上 map，refresh tick 重新跑后续才生效。也可以加一个**入池后修正**：refresh tick 末尾批量改写 pool 里的 topic_group。

**改动点**：
- `src/openbiliclaw/discovery/engine.py:_cache_results`（入池前 canonical 改写）
- `src/openbiliclaw/recommendation/engine.py:_classify_pool_backlog_locked`（同样）
- 可选：DB 层 batch update on refresh tick

**验收**：
- 同主题不同字面（"动漫"/"动漫杂谈"/"动漫二次元"）在 `Recommendation candidate summary` 显示为同一 topic_group
- top_topic_share 反映真实主题分布（不再被字面拆分掩盖）

**风险**：中等。改写已落库 topic_group 可能影响其他依赖它的查询（比如 `get_topic_group_samples`）。需仔细 dry-run。

**复杂度**：80-120 行，~1.5 小时。

**版本**：**v0.3.56**（独立波次）。

---

## 3. 执行波次

按"用户体感 × 修复成本"性价比排序：

### 第 1 波（**v0.3.51** — discovery 速度治根）

- **U1**：关 reasoning_effort
- **U5**：style cap

合计 ~70 行代码 + 回归测试。预期 30 min 完成。

**这一波最重要**——U1 是 daemon 慢的根，修了之后 reshuffle / pool 补货 / expression 出货都自然变快。

### 第 2 波（**v0.3.52** — discovery 候选利用率）

- **U2**：单 round 多 batch 并发评估

依赖 U1（reasoning 关了再并发才不烧钱）。~40 行。

### 第 3 波（**v0.3.53** — speculator + xhs_producer）

- **U7**：speculator gate 0.4 → 0.3
- **U8**：xhs_producer 加 skip-reason 日志

合计 ~30 行。可以跟 U2 一波一起 commit 但版本独立。

### 第 4 波（**v0.3.54** — Ollama 稳定性）

- **U4**：Ollama health-check + chat fallback 摘除
- **U6**：MMR prewarm 重试

两个治根 startup 期问题，~150 行。

### 第 5 波（**v0.3.55** — 网络韧性）

- **U3**：B 站 search v_voucher 退避 + trending fallback

~80 行。独立波次因为涉及 API client 改动。

### 第 6 波（**v0.3.56** — 主题语义合并）

- **U9**：supergroup 入池阶段合并

最大改动 + 数据迁移考虑。

---

## 4. 横切关注点

### 测试

- 每个 U 都附带至少一个回归测试，验证修复行为
- 整体回归测试：v0.3.50 之后 169 tests pass，本 spec 完成后维持 ≥ 200 通过

### 可观察性

- 每个修复加 INFO 级别日志，关键指标可被 grep 出来
- v0.3.51 后日志关键 marker：
  - `eval_batch ... elapsed=Xs`（U1 监控降幅）
  - `eval_batch style cap: ...`（U5 触发频率）
  - `pool franchise quota: ...`（v0.3.50 已有）

### 滚动方式

- 每波 commit 到 main 后 push，用户 `git pull && uv sync && 重启`
- 不发 backend Releases 二进制（项目策略）
- changelog.md 每波单独章节

### 用户成本影响

| 波次 | LLM 月成本变化 |
|------|------|
| v0.3.51 (U1+U5) | **-80%**（关 reasoning） |
| v0.3.52 (U2) | +200%（并发评估，但用户感知好得多）。仍比 0.3.50 低 |
| v0.3.53 (U7+U8) | +5%（xhs_producer 多跑几轮） |
| v0.3.54 (U4+U6) | 0 |
| v0.3.55 (U3) | -10%（无效 search query 不再生成） |
| v0.3.56 (U9) | 0 |

合计净影响：**月成本约 -50%**，体感大幅改善。

---

## 5. 不在本 spec 内（明确推迟）

- **delight scoring 频率优化**：当前 16 次/43min，v0.3.49 已经把 surface 阈值收紧；调度频率单独议
- **classify_pool_backlog 频率**：默认 30/run，间隔 30min 已知，但低优先级
- **B 站 trending API rate limit handling**：相对稳定，没暴露问题
- **ollama 替代 deepseek 的可行性**：成本下降但质量待验证，单独 spike

---

## 6. 验收（spec 完成的定义）

跑一份 1 小时 daemon 会话，对比 2026-05-05 的基线日志：

- [ ] `discovery.evaluate_batch` 单批 elapsed P95 < 60s（U1）
- [ ] `evaluate_content_batch: truncating N→M` 中 M ≥ 90（U2）
- [ ] B 站 search round 0-result 减少 50%+（U3）
- [ ] daemon 启动头 60s 0 次 Ollama 502（U4）
- [ ] `Recommendation candidate summary` 中 top_style_share ≤ 0.27（U5）
- [ ] daemon 启动头 5 min MMR cache coverage ≥ 80%（U6）
- [ ] speculator force_tick promote ≥1（U7）
- [ ] xhs_producer 1 小时内触发 ≥3 轮（U8）
- [ ] `Recommendation candidate summary` 中 "动漫" 系列字面被合并到 1 个 topic_group（U9）

每项达标即可关闭。
