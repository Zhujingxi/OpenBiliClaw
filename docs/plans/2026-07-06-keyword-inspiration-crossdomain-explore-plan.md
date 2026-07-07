# Keyword Inspiration 跨域 Explore 通道 — Implementation Plan（Phase 2.3）

> **Spec:** [`2026-07-06-keyword-inspiration-crossdomain-explore-spec.md`](./2026-07-06-keyword-inspiration-crossdomain-explore-spec.md)
> **Status:** Reviewed — 2026-07-06 (Codex 4-round adversarial review, R1 redesign + R2-R3 findings applied, R4 APPROVE)。承接 Phase 1/2/2.1（`1d79db17`）。
> **Executor:** Opus 4.8 subagent；Claude 逐 task 验收 + 真机验收；TDD。

**Goal:** explore 通道的关键词生成走轴库富链路,种子复用 merged call 现成的跨域 `explore_domains`
（非 like 兴趣）,默认开(coexist),失败降级旧 flatten,复用 Phase 2 回填做舒适区扩张。

**Tech Stack:** Python 3.11+, MyPy strict, Ruff 100-char, pytest（`asyncio_mode=auto`）。
Interpreter `.venv/bin/python`。测试内联手写假数据,无新 fixture。

**Invariants:** explore stage ≤1 次成功 LLM 调用（继承 2.1 有界重试）；system prompt 100% 静态；
E0 参数化后 regular 通道逐字不变；explore 只在 `keyword_planner_explore_due_soon` 到期触发；
富生成失败降级旧 `_explore_domain_queries`(不裸奔)；只做 coexist(replace-mode explore=Non-Goal)；
Phase 1/2/2.1 现有测试零断言修改。

---

### Task 1: 参数化核心 pipeline（E0,前置重构 — R1 S2）

**Files:** `src/openbiliclaw/runtime/inspiration_pipeline.py`；Test `tests/test_inspiration_pipeline.py`

**Steps:**
1. 失败测试(byte-stable)：`_run_inspiration_axis_pipeline` 增可选参数 `seed_interests=None`
   (None→现 `_selected_inspiration_interests`)、`axis_source=None`(None→现默认)、
   `explore_request=None`、`allow_deterministic_llm_fallback=True`(现行为;R2 S2)；**参数全默认时,
   选择/probe/allocation/prompt/产出/失败处理与重构前逐字相同**(断言现有 regular happy-path 测试
   的 keyword 文本 + ledger 不变)。
2. 实现:把内部硬编码的兴趣选择/轴 source/prompt extras/**LLM 失败后是否走确定性补位**抽成这四个
   参数,默认值 = 现行为。`allow_deterministic_llm_fallback=False` 时,LLM 失败直接返回空(不走
   确定性补位,让上层降级)。不改任何 regular 调用点(不传新参 → 走默认)。
3. Gate：`.venv/bin/python -m pytest tests/test_inspiration_pipeline.py tests/test_keyword_planner.py -q`
   + ruff + mypy。**现有测试零断言修改**是本 task 的硬门。

### Task 2: source 过滤轴查询（E5 的 DAO — R1 S2）

**Files:** `src/openbiliclaw/storage/database.py`；Test `tests/test_discovery_inspiration.py`

**Steps:**
1. 失败测试：`list_inspiration_axes_by_source(source, *, min_yield=0.0, limit, now)` 按 `source`
   过滤(不按 interest_label)、`status='active'`、**`_axis_is_time_expired(row, now)` 为假**
   (R2 S2:镜像 `list_inspiration_axes` 的过期时效轴抑制)、`yield_score >= min_yield`,按 Phase 2
   同排序返回、bounded limit;断言 (a) explore 轴能被捞出(现按 interest_label 捞不出);(b) 过期
   时效 explore 轴不返回。
2. 实现该 DAO;跨 source 合并规则:`_merge_axis_into`(inspiration_pipeline)撞既有轴时**保留既有
   source**(测试:explore 轴 upsert 撞 regular 轴 → source 仍 regular;全新跨域轴 → source=explore)。
3. Gate：targeted pytest + ruff + mypy。

### Task 3: explore prompt 契约（E2 的 prompt 侧,静态）

**Files:** `src/openbiliclaw/llm/prompts.py`；Test `tests/test_llm_prompts.py`

**Steps:**
1. 失败测试：`_INSPIRATION_AXIS_KEYWORD_SYSTEM_PROMPT` 含**静态**规则:带 `explore_request` 时
   core_concept 锚定**未覆盖但相关**的跨域具体实体,避开 `explore_request.avoid_covered`(带正反例)。
2. `build_inspiration_axis_keyword_prompt` 增可选 `explore_request` 入参(per-call,进 user message,
   `ensure_ascii=False, indent=2, sort_keys=True`;system 静态)。注册进
   `test_prompt_builder_system_messages_are_call_invariant`(带/不带 explore_request 两输入,system
   仍 byte-identical)。
3. Gate：`.venv/bin/python -m pytest tests/test_llm_prompts.py -q` + ruff + mypy。

### Task 4: `_run_explore_inspiration_stage`（E1+E2 的 stage — 种子=explore_domains）

**Files:** `src/openbiliclaw/runtime/inspiration_pipeline.py`；Test `tests/test_inspiration_pipeline.py`

**Steps:**
1. 失败测试(stubbed LLM)：`_run_explore_inspiration_stage(explore_platforms=[_BILIBILI], *, profile,
   digest, explore_domains, covered_topic_groups)` —— 把 `explore_domains` 的每个 domain 当
   **`seed_interest`(种子只用当前 domains)** 传给 Task 1 参数化 pipeline,`axis_source='explore'`、
   `allow_deterministic_llm_fallback=False`、prompt 带 explore_request(avoid_covered=
   covered_topic_groups)、`keyword_kind_by_platform` 全 explore;**历史高产 explore 轴
   (`list_inspiration_axes_by_source('explore')`)只作为匹配当前 domain 的 `existing_axes` 喂入,
   不当 seed_interest(R2 S2:否则 source_interest 会变成旧域、违反 AC2)**;冷启动为空不报错。断言
   产出 `keyword_kind='explore'`、platform=B站、backend=axis_keyword、轴 source=explore 带正确
   axis_id、每个 keyword 的 `source_interest` ∈ 当前 explore_domains(不是旧域/like 兴趣);恰好 1
   次成功 LLM 调用;restatement_rate ≤0.3(固定 explore fixture);继承 F2 max_tokens。
2. **allowed-interest 钳制(R3 S2)**：parser 现在直接信任 LLM 的 `raw_keyword["interest"]`
   (`keyword_planner.py:561` 一线)写进 `MaterializeCandidate.interest`→`source_interest`。explore
   装配前**丢弃 `interest` 不在当前 `explore_domains` 种子标签集里的候选**(归一化匹配),机制上
   保证每个 explore 关键词 `source_interest` ∈ 当前域。**对抗 fixture**:stubbed LLM 返回一条
   `interest=<旧 explore 域或 like 兴趣>` 而当前域不同 → 断言该候选被丢弃,产出里无 keyword 带陈旧
   标签(否则 AC2 只是种子层面成立、输出层面漏)。
3. 失败信号:LLM 抛/空 + `allow_deterministic_llm_fallback=False` → stage 返回**空 + degraded 标记**
   (不被确定性补位掩盖),让调度层降级(Task 5)。测试断言 degraded 时 stage 产出为空。
4. Gate：targeted pytest + ruff + mypy。

### Task 5: 调度接线 + 默认开 + 降级（E3+E4 — R1 S1）

**Files:** `src/openbiliclaw/runtime/keyword_planner.py`；Test `tests/test_keyword_planner.py`

**Steps:**
1. 失败测试：coexist `_generate_for`,`inspiration_search_enabled=true` + explore 到期(现成
   `explore_domains` + covered_topic_groups)→ 走 `_run_explore_inspiration_stage`(**不走**
   `_explore_domain_queries`),产 explore-kind 词、调 `mark_explore_planned`;未到期 → 不触发。
2. **降级(R1 S1)**：mock explore 富生成 degraded → 回退 `_explore_domain_queries(explore_domains)`
   (现成 domains,fallback 有真数据),explore 池仍补货、`mark_explore_planned` 照常、telemetry
   `explore_inspiration_degraded=true`。测试断言降级路径真的插了词(非空)。
3. **预算(R1 S2)**：due/not-due 调用计数测试——到期轮比不到期轮多且仅多一次 explore 富生成调用;
   regular 通道调用数不变。
4. 无双重 explore(走新的就不走旧 flatten,除降级);replace 模式路径**不改**(Non-Goal)。
5. Gate：`.venv/bin/python -m pytest tests/test_keyword_planner.py -q` + ruff + mypy。**regular
   stage 测试零断言修改**。

### Task 6: 舒适区扩张归因验证（E5 闭环,复用 Phase 2 回填）

**Files:** Test only `tests/test_discovery_inspiration.py`

**Steps:**
1. 失败测试：explore 轴 `source='explore'` 带正确 `axis_id`;种入伪造 explore-cohort keyword 历史
   (angle_id=explore 轴 id + admissions)→ Phase 2 `backfill_inspiration_axis_yield` → 断言该
   explore 轴 yield_score 上升;`list_inspiration_axes_by_source('explore', min_yield=...)` 能把它
   作为高产轴捞出(供 Task 4 复用)。
2. 预期零新增回填逻辑(Phase 2 按 axis_id 归因,与 source 无关);若有缺环补最小修正并说明。
3. Gate：targeted pytest + ruff + mypy。

### Task 7: 文档 + 完整门

**Files:** `docs/modules/discovery.md`、`docs/modules/storage.md`（新 DAO）、`docs/changelog.md`

**Steps:**
1. discovery.md：跨域 explore stage（种子=merged explore_domains、explore_request prompt、复用 2.1
   机制、source='explore' 轴 + 回填舒适区扩张、默认开 coexist + 降级旧 flatten、预算 +1 调用）。
   storage.md：`list_inspiration_axes_by_source` DAO。
2. changelog.md：当前版本块下一条 bullet。
3. 完整门：`.venv/bin/python -m pytest tests/test_llm_prompts.py tests/test_inspiration_pipeline.py
   tests/test_discovery_inspiration.py tests/test_keyword_planner.py tests/test_config.py
   tests/test_storage.py -q` ; `ruff check src/ tests/` ; `mypy src/`。
4. **真机验收留给 Claude**（Spec AC10）：不要跑。

---

## Sequencing & risk

- **Task 1（E0 参数化）是所有后续的前置且最险**(动核心 pipeline,regular 零漂移是硬门)——先做、
  独立验收 byte-stable 再往上建。Task 2/3 独立低风险。Task 4 是核心新 stage。Task 5 接线 + 降级
  (动 `_generate_for`),放最后。
- 风险点：explore 富生成质量首轮靠 prompt 的"未覆盖但相关"约束 + merged domains 的跨域性;差的
  explore 轴由 Phase 2 生命周期退休(自校正)。真机验收看跨域性 + 丰富度。
- 风险点：默认开动 explore 派发 → 必须保 regular 零影响 + 降级不裸奔(Task 5 双测)。
- 回滚：feature 分支未 push；不合并直到真机验收显示 explore 跨域丰富且 regular 无回归。

## Out of scope

- replace 模式 explore（R1 S1 kind 冲突）——后续。
- 多平台 explore（B站单平台）。
- 独立跨域域发现 LLM 调用（复用 merged domains）。
- explore 时钟/预算节奏调整。
