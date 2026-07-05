# Keyword Inspiration Axis Library — Phase 2 Spec（yield 学习闭环 + 生命周期 + 收敛）

> **Status:** Reviewed — 2026-07-05, 3-round Codex adversarial review (R1-R2 findings applied, R3 VERDICT: APPROVE). Phase 1 (`2026-07-05-keyword-inspiration-axis-redesign-spec.md`)
> 已全部落地并通过真机验收(轴库/单次调用/覆盖优先装配/两级 fallback)。本 spec 覆盖 Phase 1
> §Phasing 划出的 Phase 2 范围。
> **Branch:** `feature/discovery-inspiration-mvp`(Phase 1 于本分支提交后继续)。

## Goal

让轴库从"能复用"变成"会学习",并偿还 Phase 1 划定的工程债。四个可衡量目标:

1. **yield 回填闭环**:轴的 `yield_score` 从恒 0 变成由真实成绩(admissions,可选 delight
   加权)驱动;好轴上浮、坏轴沉底,`freshness × effective_score` 排序开始有意义
   (prior 地板改为只保护从未消费过的轴,见 A2)。
2. **生命周期代谢**:time_sensitive 过期轴、持续零产出轴,从"被动查询时过滤"变成
   **持久状态迁移**(stale/retired)+ 陈旧行真删除;库不再只靠 cap-16 硬挤。
3. **config 收敛**:13 个 `inspiration_*` 旋钮压到 **4 个**(enabled / replace / backends /
   breadth 档位),其余从档位派生成内部常量。
4. **编排抽取**:①–⑥ 编排从 `KeywordPlanner`(~4000 行 god-file)搬进独立
   `InspirationKeywordPipeline`,行为零变化。

可选第五项(独立可裁):**embedding 近邻**——轴近义合并 + 关键词近似去重,服务不可用时
无损降级回 Phase 1 字符串行为。

## Non-Goals

- 不改 Phase 1 的调用契约:一轮 LLM 恒 ≤1、覆盖三层叠加、shortfall 显式化全部保持。
- 不动 gate / cohort 统计口径(`get_keyword_cohort_stats` 的 replace 门禁语义不变)。
- 不做多用户;单用户假设延续。
- yield 回填**不新增任何 LLM 调用**——全部是 SQL 聚合。

## Part A — yield 回填闭环

### A1. 归因链补全:复用既有 `angle_id`/`angle_label` 列

`discovery_keywords` 的 insert 只持久化**固定 metadata 列**(`database.py:~4898`),
自由 metadata 键会被丢弃——所以归因必须落在既有列上。现状恰好留了位置:
`angle_id` / `angle_label` 是持久化列,且 Phase 1 已把轴标签写进去
(`keyword_planner.py:1269-1272`,但 `angle_id` 目前塞的是 label 而非 id)。

- **新行**:`MaterializeCandidate` 增加可选 `axis_id`(LLM 解析时由 `axis_id_or_label`
  映射到真实 id;确定性补位时库轴自带);realize 后 metadata 的 `angle_id` 写**真实
  `axis_id`**、`angle_label` 写轴标签(修正 Phase 1 的双 label 占位)。
- **旧行(Phase 1 存量)**:`angle_id`==label。识别不能只嗅探 `axis:` 前缀(万一某个
  label 恰好以 `axis:` 开头就会误判):**`angle_id` 只有在轴表里真实存在
  (按 axis_id 查得到行)才按直接 id 用**,否则一律回退
  `derive_inspiration_axis_id(source_interest, angle_label)` 现场重导(id 对
  (interest, normalize(label)) 是确定性哈希,天然可重建)。回归用例:
  legacy `angle_id == angle_label == "axis:怪标签"`。
- **不改 `discovery_keywords` schema**。

### A2. 回填 = 全量重算(SET),不是增量(INCREMENT)

**幂等按构造**:每次回填对 trailing window(默认 30 天)内的 inspiration keyword 行做
聚合,然后 **SET** 轴行的统计字段——同样数据跑两遍结果相同,无需水位线/去重簿记。
旧成绩随窗口滑出自然衰减(成功也要保鲜,这是特性不是缺陷)。

```
per axis over trailing 30d of discovery_keywords (inspiration cohort),
axis 归属 = angle_id 在轴表真实存在时直接用,否则 derive(source_interest, angle_label)
           (存在性校验防 legacy label 恰好带 axis: 前缀的误判,见 A1):
  window_uses = COUNT(rows 已被消费过, 即 status 离开过 pending
                      —— 精确状态集在 Plan Task 1 里对照 schema 锁定)
  admissions  = SUM(yield_count)         -- yield_count 每词最多记一次(现有约束)
SET on discovery_inspiration_axis:
  window_uses       = window_uses        -- 新列
  admissions        = admissions
  yield_score       = (admissions + 0.3) / (window_uses + 1.0)   -- Laplace 平滑
  yield_backfilled_at = now
```

> 注意与轴表既有 `use_count` 的分工:`use_count` 是**选取簿记**(该轴被喂给 LLM 的
> 次数,upsert 时 bump),继续只服务多样性 tie-break;成绩公式与生命周期阈值
> **一律用 `window_uses`**(该轴产出的关键词被实际消费的行数)——分子分母同源。

- 平滑常数 0.3 与 Phase 1 的 `exploration_prior = 0.3` **刻意同值**:零使用的轴
  score = 0.3/1.0 = prior,排序连续无跳变。
- **排序地板改为条件式(修 Phase 1 的 `max(yield_score, prior)`)**:无条件 max 会把
  坏轴捞回地板——window_uses=5、零 admissions 的轴 score=0.05,`max(0.05, 0.3)` 让它
  和全新轴平级,"坏轴沉底"永远不发生。改为:
  `effective = yield_score if window_uses > 0 else max(yield_score, prior)`
  ——prior 只保护**从未被消费过**的轴(真探索);有过消费记录的轴按真实成绩排,
  低分立刻下沉(退休线 0.08 只是最终处决,下沉从第一次回填就开始)。
  `_axis_list_sort_key` 相应改造(读 window_uses 列)。
- **delight 加权(可选乘子)**:若现有 cohort join 能给出该轴 admitted 内容的
  mean_delight,则 `yield_score ×= clamp(0.5 + mean_delight, 0.5, 1.5)`;join 不可得时
  乘子恒 1.0。Plan 里先做 feasibility spike,不可行则本项降级为 Phase 3,不阻塞。
- 需要 schema 迁移:`discovery_inspiration_axis` 增加 `window_uses INTEGER NOT NULL DEFAULT 0`
  与 `yield_backfilled_at TEXT` 两列(`ALTER TABLE ... ADD COLUMN`,沿用库内既有的容错
  迁移模式)。

### A3. 触发点与节流

- **production inspiration stage 开始时**(② 取轴之前)执行回填,让本轮选轴立刻看到
  新成绩;纯 SQL,无 LLM,毫秒级。
- **节流**:全库 `MAX(yield_backfilled_at)` 距 now 不足 6 小时则跳过(常数
  `_AXIS_BACKFILL_MIN_INTERVAL_HOURS = 6`)。
- **preview 永不触发回填**(与 preview 不 bump usage 同一原则:观测不改变被观测系统)。

## Part B — 生命周期代谢(与回填同 tick)

回填之后同一事务内执行三条确定性迁移(全部注入 `now`,可单测):

1. **时效过期 → stale(持久化)**:`time_sensitive=1` 且超 `freshness_ttl_days` 的
   active 轴置 `status='stale'`。Phase 1 只在读取时过滤,现在真正落库。
2. **持续失败 → retired**:`window_uses >= 5` 且回填后 `yield_score < 0.08` 的 active 轴
   置 `status='retired'`(给过 5 次消费机会仍几乎零产出;有消费记录的轴分数可低于 0.3,
   0.08 对应 window_uses≥5 且 admissions≈0,如 0.3/6=0.05)。分母与成绩公式同源(见 A2 注)。retired 不参与任何选取,且**不再被 upsert 复活**(LLM 重提同名轴时
   合并进 retired 行但状态不变——防坏轴借尸还魂)。
3. **陈旧真删除**:`status IN ('stale','retired')` 且 `last_refreshed_at` 早于 90 天的
   行物理 DELETE(回应"表只增不减"的担忧)。

cap-16 溢出规则保持不变,作为最后兜底。

## Part C — config 收敛(13 → 4)

### 保留的 4 个

| key | 语义 |
|---|---|
| `inspiration_search_enabled` | 总开关(默认 off) |
| `inspiration_replace_merged_keywords` | 替换旧 merged planner(过 cohort 门禁后才开) |
| `inspiration_search_backends` | 检索后端列表 |
| `inspiration_breadth` **(新)** | `"low" \| "medium" \| "high"`,默认 `"medium"` |

### 删除的 10 个(精确清单)→ 档位派生表

删除清单(与 `config.py:330-341` 逐一对应):`inspiration_aspect_window_size`、
`inspiration_interest_sample_size`、`inspiration_max_probe_searches_per_stage`、
`inspiration_platforms_per_probe`、`inspiration_riskcontrolled_probe_budget`、
`inspiration_search_pages_per_probe`、`inspiration_search_results_per_query`、
`inspiration_max_seeds_per_aspect`、`inspiration_max_expansions_per_seed`、
`inspiration_max_keywords_per_platform`。

| 内部常量(原 key) | low | medium(=`_DEFAULT_INSPIRATION_*` 现值) | high |
|---|---|---|---|
| aspect_window_size | 16 | **32** | 48 |
| interest_sample_size | 3 | **6** | 8 |
| max_probe_searches_per_stage | 6 | **12** | 20 |
| platforms_per_probe | 1 | **2** | 3 |
| riskcontrolled_probe_budget | 2 | **4** | 8 |
| search_pages_per_probe | 1 | **1** | 2 |
| search_results_per_query | 3 | **5** | 8 |
| max_seeds_per_aspect | 2 | **3** | 5 |
| max_expansions_per_seed† | 2 | **4** | 6 |
| max_keywords_per_platform | 8 | **12** | 16 |

- medium 列 = 当前 `_DEFAULT_INSPIRATION_*` 常量的**逐项照抄**(表驱动断言强制)。
  注意 Phase 1 planner 内部另有 `selected_interests ≤ 4` 的调用预算 cap
  (`min(4, sample_size)`,spec §预算与失败处理)——派生 6 后有效值仍是 4,行为零漂移。
- † `max_expansions_per_seed`:Plan Task 4 先核实 Phase 1 死代码清扫后是否仍有消费者;
  无消费者 → 连内部常量一起删,不入派生表;有 → 按表派生。

### 迁移策略

- pre-alpha 单用户,沿用 Phase 1 rollout 先例:**硬删除,无兼容 shim**。
- **WARNING 机制(落地在既有 diagnostics 通道)**:`load_config_with_diagnostics`
  (`config.py:~1790`)在调用 `_build_discovery` **之前**扫描 raw `[discovery]` dict,
  发现删除清单里的 key → 往 `diagnostics.issues` 追加一条
  "`inspiration_xxx` 已移除,值被忽略,请改用 `inspiration_breadth`";CLI 已有的
  "配置提示" 面板自然渲染它。可通过 `load_config_with_diagnostics` 直接单测,不依赖
  日志捕获。不 fail-fast。
- **CLI one-shot 覆盖重新接线**:`keyword-inspiration-preview/dry-run` 的 `--limit` /
  `--interest-limit` 现在直接改两个将被删除的 config 字段(`cli.py:8613-8617`)。
  收敛后改为:CLI 构造"有效 inspiration 参数"视图 = `derive(breadth)` 再套一次性覆盖,
  **经 planner/pipeline 的构造注入**(internal config view)——**不改
  `preview_inspiration_keywords` 等四个兼容委托的签名**(否则与 Part D 冲突)。
  **两个 flag 的用户可见行为不变**。`config-show` 的渲染段(`config.py:~2255`)同步只
  展示 4 个 key。收口断言:全库 grep 无残留
  `getattr(self._discovery, "inspiration_<已删字段>")` 读取。
- `config.example.toml`、`docs/modules/config.md`、`docs/modules/cli.md` 同步只保留
  4 个 key + 档位说明。
- **回归护栏**:medium 档派生值与 `_DEFAULT_INSPIRATION_*` 现值逐项相等(表驱动断言),
  保证升级后行为零漂移。

## Part D — `InspirationKeywordPipeline` 抽取

- 新文件 `src/openbiliclaw/runtime/inspiration_pipeline.py`:类
  `InspirationKeywordPipeline`,承接 ①–⑥ 编排(选兴趣/取轴/probe/ground/单次调用/装配/
  回写/回填 tick),构造注入 db、llm、provider、discovery config、clock。
- `KeywordPlanner` 保留**四个兼容委托**(现有测试直接调用它们的私有 API):
  `_run_inspiration_stage`、`_run_shared_inspiration_stage`、
  `preview_inspiration_keywords`、`_selected_inspiration_interests`——签名不变,内部
  委托 pipeline。**纯搬家 + 委托,行为零变化**,现有测试不改任何断言仍须全绿(仅当
  测试 monkeypatch 的私有属性物理搬家时允许改 patch 路径,逐条列入报告)。
- 新增 pipeline 直接单测(注入 fakes,不经 planner)。
- `keyword_planner.py` 行数显著下降(验收记录搬走行数即可,不设硬阈值)。

## Part E — embedding 近邻(可选,独立可裁)

- 复用 `llm/embedding.py` 服务 + `embedding_cache` 表(bge-m3 via Ollama)。
- **分层约束**:embedding 服务是 async/provider-backed,存储 DAO 是同步的——合并解析
  **发生在 pipeline 层**(⑥ 之前):pipeline 用注入的 async embedding helper 把"新轴 →
  应并入的既有 axis_id"解析成普通 upsert 输入,再交给**保持同步、不做任何 I/O** 的
  `upsert_inspiration_axes`。DAO 零改动。
- **轴近义合并**:新轴与同 interest 的 active 轴 cosine ≥ 0.92 → 并入既有轴
  (evidence 合并,不新建行);解决"维修与DIY vs 故障自修"两根名额浪费一根的问题。
- **降级契约(硬要求)**:embedding 服务不可用/超时 → 无损回退 Phase 1 字符串规范化
  行为,不抛错、不阻塞 stage,telemetry 记 `axis_embedding_degraded=true`。
- 本 Part 整体可从 Phase 2 裁掉而不影响 A–D 验收。

## Acceptance Criteria

1. **幂等**:同一数据上连跑两次回填,`discovery_inspiration_axis` 全表字节相同。
2. **成绩驱动排序**:种入历史(轴 X 有 admissions、轴 Y 多 uses 零 admissions、轴 Z
   未使用)→ 回填后 `list_inspiration_axes` 顺序为 X > Z(≈prior) > Y;X 能压过
   比它更新鲜的零成绩轴(freshness × score 交叉用例)。
3. **平滑连续性**:未使用轴回填后 score == 0.3 == exploration prior(排序无跳变)。
4. **preview 隔离**:preview 跑 N 轮,`yield_backfilled_at`/`admissions`/status 全部
   不变;production stage 触发回填,6 小时内第二次 stage 跳过(节流)。
5. **生命周期**:注入 now 的单测覆盖——TTL 过期→stale 落库;`window_uses>=5` 且低分
   →retired 且 upsert 不复活(stale 可被新鲜 upsert 复活,retired 不可——不对称是
   有意的);stale/retired 超 90 天→物理删除;全部转移都出现在 telemetry。
6. **config**:example.toml 与 config.md/cli.md 只含 4 个 inspiration key;medium 档
   派生值与 `_DEFAULT_INSPIRATION_*` 现值逐项相等(表驱动);写入已删除 key →
   `load_config_with_diagnostics` 返回的 issues 含移除提示且值被忽略(直接单测,
   不靠日志捕获);low/high 档各自派生正确;`config-show` 渲染只含 4 个 key。
6b. **CLI 覆盖不回归**:`--limit` / `--interest-limit` 在收敛后行为不变(作用于派生
   参数对象的一次性覆盖),有测试。
7. **抽取零漂移**:Part D 落地后,现有 keyword_planner/discovery_inspiration 测试
   **不修改任何断言**全绿;pipeline 有独立单测。
8. **LLM 计数不变式**:回填/迁移/收敛/抽取路径全程 0 次新增 LLM 调用;Phase 1 的
   "一轮 ≤1"断言原样通过。
9. **(若保留 Part E)** 近义轴合并用例 + 服务不可用降级用例(mock 超时→字符串行为 +
   telemetry 标记)。
10. **真机验收**:smoke 环境跑 production `run_once` 一次(种入伪造历史)→ 轴表
    yield_score 变化、telemetry 含 backfill 计数;`--persist-axes` preview 两轮确认
    完全不触发回填。

## Open Decisions(拟定,review 后 lock)

- **SET 重算而非增量**,trailing 30d 窗口,幂等按构造 — proposed。
- **平滑式 `(admissions+0.3)/(uses+1)`**,常数绑定 exploration prior — proposed。
- **retired 不复活**(upsert 合并但状态保持) — proposed。
- **config 硬删除 + 启动 WARNING**,无兼容 shim(沿用 Phase 1 rollout 先例) — proposed。
- **delight 乘子做 feasibility spike**,不可行降级 Phase 3 — proposed。
- **Part E 可裁** — proposed。
- **归因走既有 `angle_id`/`angle_label` 列**(新行存真 id,旧行 derive 回退),不改
  `discovery_keywords` schema — locked(2026-07-05 Codex 对抗审查 R1)。
- **成绩分母 = 新列 `window_uses`**,与选取簿记 `use_count` 分工明确 — locked(同上)。
- **WARNING 走 `load_config_with_diagnostics` 的 issues 通道**,CLI one-shot 覆盖改接
  派生参数对象 — locked(同上)。
- **embedding 合并在 pipeline 层解析,DAO 保持同步零 I/O** — locked(同上)。
