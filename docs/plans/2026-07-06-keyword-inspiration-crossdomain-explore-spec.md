# Keyword Inspiration: 跨域 Explore 轴库通道 Spec（Phase 2.3）

> **Status:** Draft r2 — 2026-07-06,按 Codex R1 六条发现重设计。承接 Phase 1/2/2.1（`1d79db17`）。
> **Branch:** `feature/discovery-inspiration-mvp`。

## Goal

让 explore（舒适区外跨域）通道的**关键词生成**升级到轴库富链路（吃 Phase 2.1 具体锚点丰富度），
**默认开**（coexist 模式,explore 时钟到期即走）。

**核心设计转向（Codex R1）**：跨域"**话题发现**"和"**丰富关键词生成**"是两件事。旧 explore
链路已经把**发现舒适区外的域**这件事做对了(`ExploreStrategy._generate_domains` /
merged call 的 `explore_domains` 块,用 `covered_topic_groups` 避开已饱和话题)。所以本方案
**不自己重新发现跨域域,也不拿尾部 like 兴趣冒充**(R1 S2:尾部 like 兴趣仍在舒适区内)——而是
**复用 merged call 已产出的 `explore_domains` 当种子**,只把它们的关键词从"旧 flatten"升级到
"轴库富生成"。

## 现状（诊断，R1 已核实）

- coexist 模式(默认)下,merged call 已产出 `explore_domains`(跨域话题 + queries,避开
  `covered_topic_groups`);`_generate_for` 用 `_explore_domain_queries` 把 queries 摊平 →
  explore-kind 入池(`keyword_planner.py:1301`)。**跨域域现成,不缺发现。**
- 缺的只是:这些 explore 词走旧 flatten,**没吃轴库 / Phase 2.1 丰富度**。
- explore B站单平台,`keyword_planner_explore_due_soon` 到期 + B站有 deficit 才触发。
- `_run_inspiration_axis_pipeline` 内部**硬编码**兴趣选择/轴/probe/allocation(R1 S2),注入种子
  是一次**参数化重构**,不是小注入点。
- replace 模式(默认关)的 `_run_shared_inspiration_stage` 用一张 `keyword_kind_by_platform`
  map,B站不能既 regular 又 explore(R1 S1)——**故本方案只做 coexist,replace-mode explore
  列 Non-Goal。**

## Fix

### E0. 参数化核心 pipeline（R1 S2,前置重构）

把 `_run_inspiration_axis_pipeline` 的**种子/轴 source/prompt extras/fallback 策略参数化**:抽出
`seed_interests`(默认仍是 `_selected_inspiration_interests` 的 like 兴趣)、`axis_source`
(默认现值)、可选 `explore_request`(默认 None)、`allow_deterministic_llm_fallback`(默认 `True`
=现行为;R2 S2)。**regular 路径行为逐字不变**(byte-stable 测试:参数全默认时选择/prompt/产出/
失败处理与重构前完全一致)。这是让 explore 复用同一骨架而不复制/污染 regular 的前提。

### E1. Explore 种子 = merged call 的 `explore_domains`（不是 like 兴趣）

- 种子来源:coexist 模式 merged call 已产出的 `explore_domains`(每个 domain = 一个跨域话题,
  已避开 `covered_topic_groups`)。把每个 domain 作为一个 `seed_interest`(伪兴趣)喂进
  参数化 pipeline。
- **不用尾部 like 兴趣**(R1 S2:仍在舒适区内);**不新增独立跨域发现调用**(域现成)。
- **种子只用当前 merged domains**(R2 S2:历史 explore 轴**不当种子**——它们是旧域、可能已被
  covered,当种子会让 explore 词的 `source_interest` 变成旧域、违反 AC2)。历史高产 explore 轴的
  用法见 E5:只作为**匹配当前域的 `existing_axes`** 喂进去(丰富当前域的轴,不引入新话题)。
- **冷启动允许为空**(首次没有 explore 轴,纯靠 merged 的 domains,不退化)。

### E2. 参数化 pipeline 的 explore 单次调用 + prompt 契约

- 新 `_run_explore_inspiration_stage(explore_platforms=[_BILIBILI], *, profile, digest,
  explore_domains)`:用 E1 的 domain 种子跑参数化 pipeline,产 `keyword_kind='explore'`、
  platform=B站、`inspiration_backend='axis_keyword'`、轴 `source='explore'`。
- `build_inspiration_axis_keyword_prompt` 增可选 `explore_request` 入参(per-call 变量,进
  user message);system prompt 增一条**静态**规则:带 explore_request 时 core_concept 锚定
  **未覆盖但相关**的跨域具体实体,避开 `explore_request.avoid_covered`。system 仍 byte-identical
  (过 `test_prompt_builder_system_messages_are_call_invariant`)。
- **F1/F1.5/F2/F3 全继承**:具体性 prompt、`is_specific` 择优、max_tokens 动态、core/decoration
  观测——explore 词同样具体、跨轴、可核 restatement_rate。
- **一轮 explore stage 恰好 ≤1 次成功 LLM 调用**(继承 Phase 2.1 有界重试)。
- **失败要显式 degraded,不能被内部确定性 fallback 掩盖(R2 S2)**：`_run_inspiration_axis_pipeline`
  现在 LLM 失败且有轴时会走确定性补位(Phase 1 两级 fallback)。explore 需要的是"LLM 失败 →
  返回空 + degraded 让调度层降级旧 flatten"。故 E0 参数化新增
  `allow_deterministic_llm_fallback`(默认 `True`=regular 现行为;explore 传 `False`),explore
  的 LLM 失败直接 degraded,不被确定性补位掩盖。
- **allowed-interest 钳制(R3 S2,机制保证 AC2)**：parser 现在直接信任 LLM 的
  `raw_keyword["interest"]` 写进 `MaterializeCandidate.interest`→`source_interest`。种子是当前域
  **不机械保证**输出 interest 就是当前域(模型可能漂移返回旧域/like 兴趣)。故 explore 装配前**丢弃/
  重映射 `interest` 不在当前 `explore_domains` 种子标签集里的候选**——保证每个 explore 关键词的
  `source_interest` ∈ 当前域。加对抗 fixture:LLM 返回 `interest=<旧域/like 兴趣>` 而当前域不同 →
  该候选被丢弃,无 keyword metadata 带陈旧标签。

### E3. 预算契约（诚实,R1 S2）

- explore 富生成是 **+1 次 LLM 调用,仅在 explore 到期 AND B站有 deficit 时**(与旧时钟同门,
  节奏不变)。**跨域域发现仍搭 merged call 便车**(无新发现调用);新增的只有"把域实现成富
  关键词"这一次。旧设计特意 piggyback 避免独立调用,本方案明确接受这次偶发调用,换 explore
  丰富度。
- 不变式表述修正:不再声称"零额外调用",而是"**explore stage 内部 ≤1 次成功调用;整轮 planner
  在 explore 到期时比旧路径多一次 explore 富生成调用**"。加 due/not-due 的调用计数测试。

### E4. 默认开 + 降级（R1 S1,fallback 现在成立）

- **默认开**:`inspiration_search_enabled=true` 且 explore 到期时,coexist 的 `_generate_for`
  explore 分支改走 `_run_explore_inspiration_stage`(取代 `_explore_domain_queries` 的旧
  flatten),成功即 `mark_explore_planned`。
- **降级(现在可实现)**:explore 富生成失败/provider 不可用 → **回退旧
  `_explore_domain_queries(explore_domains)`**——`explore_domains` 是 merged call 现成产物,
  fallback 有真数据可摊平,explore 池仍补货,`mark_explore_planned` 照常(时钟前进)。telemetry
  记 `explore_inspiration_degraded=true`。(R1 S1:旧 fallback 之所以之前"不可实现",是因为我
  错设成独立发现;现在种子就来自 merged 的 domains,fallback 天然成立。)
- **无双重 explore**:新 stage 与旧 flatten 二选一(走了新的就不走旧的,除非降级)。
- replace 模式 explore **不改**(Non-Goal,R1 S1 的 B站 kind 冲突留待后续)。

### E5. 舒适区扩张闭环 + source 过滤查询（R1 S2）

- explore 轴入库 `source='explore'`;Phase 2 yield 回填按 `axis_id` 归因**与 source 无关**,
  故 explore 轴同样被回填(零新增回填逻辑)。
- **补 source 过滤查询(R1 S2 修循环依赖)**:新增 `list_inspiration_axes_by_source(source,
  *, min_yield, limit, now)`——因为现 `list_inspiration_axes` 按 `interest_label IN(选中兴趣)`
  过滤,explore 轴的 label 是跨域域名、不在 like 兴趣选择里,**永远捞不出来**。用 source 过滤
  查询让高产 explore 轴能作为 E1 的 `existing_axes`(匹配当前域时)被复用。
  **生命周期镜像(R2 S2)**:该查询必须与 `list_inspiration_axes` 同款过滤——`status='active'`
  **且** `_axis_is_time_expired(row, now)` 为假(排掉过期时效轴,不等生命周期 tick)+ min_yield +
  Phase 2 同排序 + bounded limit。
- **跨 source 合并规则(R1 S2)**:新 explore 轴 upsert 若撞上既有 regular 轴,`_merge_axis_into`
  **保留既有 source**(既有 regular 优先)——语义合理(该话题其实已在舒适区),不强制翻成 explore。
  只有全新跨域轴才 `source='explore'`。

## Non-Goals

- **replace 模式 explore**(R1 S1 的 kind 冲突)——本 phase 不做,留后续。
- 多平台 explore(维持 B站单平台)。
- 独立跨域"域发现"LLM 调用(复用 merged call 现成 `explore_domains`)。
- 拿 like 兴趣(含尾部)冒充 explore 种子(R1 S2 明确排除)。
- 改 explore 时钟/预算节奏(`explore_refresh_hours` 不动)。
- 改 regular 通道行为(E0 参数化后 regular 逐字不变)。

## Acceptance Criteria

1. **E0 参数化零漂移**：`_run_inspiration_axis_pipeline` 参数化后,regular 路径(默认参数)的选择/
   prompt/产出与重构前 byte-stable;现有 regular stage 测试零断言修改全绿。
2. **跨域种子（核心语义）**：explore 种子来自 merged `explore_domains`,**不等于**
   `_selected_inspiration_interests` 的**全量** like 兴趣集(不只是选中窗口——R1 S2);单测喂一组
   `explore_domains` + covered_topic_groups,断言 explore 关键词的 `source_interest`(=域名)
   落在 domains 里、不落在 covered 里。
3. **继承 Phase 2.1 丰富度**：explore 关键词 `restatement_rate ≤ 0.3`(复用指标 + 固定 explore
   fixture);core_concept 具体锚点。
4. **≤1 次成功调用 + 预算**：explore stage 恰好 ≤1 次成功 LLM 调用;coexist 到期轮比 not-due 轮
   多且仅多一次 explore 富生成调用(due/not-due 调用计数测试);max_tokens 有界重试继承。
5. **kind/platform 正确**：产出落 `discovery_keywords`,`keyword_kind='explore'`、platform=B站、
   `inspiration_backend='axis_keyword'`、status='pending';`explore.py` 的
   `claim(bilibili, keyword_kind=explore)` 能认领到(DB + claim 断言)。
6. **默认开 + 时钟**：`inspiration_search_enabled=true` 下 explore 到期 → 走新 stage(不走旧
   flatten),`mark_explore_planned` 被调用;未到期 → 不触发。到期/未到期两路单测。
7. **降级成立（R1 S1）**：mock explore 富生成失败 → 回退 `_explore_domain_queries(explore_domains)`,
   explore 池仍补货(explore_domains 现成)、`mark_explore_planned` 照常、telemetry
   `explore_inspiration_degraded=true`。
8. **source 过滤查询 + 归因（R1 S2）**：`list_inspiration_axes_by_source('explore', ...)` 能捞出
   explore 轴(现 `list_inspiration_axes` 按 interest_label 捞不出);explore 轴 `source='explore'`
   入库带正确 `axis_id`;种入伪造 explore-cohort 历史 → Phase 2 回填令该 explore 轴 yield_score
   上升(舒适区扩张机制证据);跨 source 合并保留既有 source。
9. **不回归**：Phase 1/2/2.1 全套件全绿(regular 通道、LLM 计数、pipeline 抽取零漂移)。
10. **真机验收（Claude 亲跑）**：explore 到期场景跑一轮 → explore-kind 词入池、跨域(source 是
    域名非 like 兴趣)、具体锚点、单次调用;对比旧 explore flatten 词看跨域丰富度提升。

## Open Decisions（拟定,review 后 lock）

- **只做 coexist 模式,replace-mode explore 列 Non-Goal** — proposed（R1 S1 的 B站 kind 冲突）。
- **explore 种子 = merged call 现成 `explore_domains`,非 like 兴趣**;冷启动允许无历史 explore 轴 —
  proposed（R1 S2）。
- **E0 先参数化 pipeline 再建 explore,regular byte-stable** — proposed（R1 S2）。
- **诚实预算:+1 次 explore 富生成调用(仅到期时),域发现仍 piggyback** — proposed（R1 S2）。
- **降级 = 旧 `_explore_domain_queries` flatten 现成 domains** — proposed（R1 S1 修 fallback）。
- **新增 `list_inspiration_axes_by_source`,跨 source 合并保留既有 source** — proposed（R1 S2）。
