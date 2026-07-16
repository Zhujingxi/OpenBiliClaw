# 事件采集补全 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-16-event-capture-completion-spec.md`](./2026-07-16-event-capture-completion-spec.md)
> **Status:** r1 — 待 codex 对抗 review
> **Execution order:** Wave A(Task 0→1→2)→ Wave B(Task 3→4→5→6)→ Wave C(Task 7→8→9→10)→ Task 11 文档。Wave 间可独立交付;Wave 内按序。
> **Tech:** Python 3.11+/`.venv/bin/python`(工作机为 3.12+,勿用裸 `python`);测试 `PYTHONPATH=$PWD/src .venv 解释器 -m pytest <file> -q`;lint `ruff format/check src/ tests/`;类型 `mypy src/`(strict)。扩展:`cd extension && npm test && npm run typecheck && npm run build`(node --test)。

**Invariants that MUST hold — re-read before each task:**

- Prompt-cache 静态性:system prompt 只能是模块级常量;`test_prompt_builder_system_messages_are_call_invariant` 全程绿。
- 回放不变性:无 retraction 的事件集,偏好分析 prompt 与改动前字节一致。
- 只标注不删除:retraction 折价仅 patch metadata,不删行、不改 event_type/url。
- 枚举白名单不扩散:不新增顶层 event_type;`comment_kind` ∈ `{"", "comment", "danmaku"}`,越界按缺失处理并 log WARNING。
- 正文净化双防线:`comment_text` 扩展侧与服务端各自截断 ≤200 字符并剥离 Unicode category-C;服务端为最终防线。
- tap 静默降级:tap 解析异常不抛到页面、不吞非正文事件;X tap 既有行为零回归。
- tap 权威即 DOM 抑制:xhs `strongSignalSource:"tap"` 后 DOM like/favorite/retraction 零发射。
- 阈值有出处:0.6 / 0.2 / 200 / 30 天窗口等常量必须带校准注释。

---

## Wave A — retraction 确定性折价(Phase 0)

### Task 0: DB 层 `mark_positive_events_retracted`

**Files:** 修改 `src/openbiliclaw/storage/database.py`;新增 `tests/test_event_retraction_discount.py`。

**Interfaces:** Consumes: identity urls(调用方已归一化)、retracted_action、within_hours。Produces: 标记行数;被标记行 `metadata.retracted=true`、`metadata.signal_strength=min(现值, 0.2)`。

**Steps:**

- [ ] Write one focused failing test:同 url 的 like 事件被标记、metadata patch 正确、返回计数。
- [ ] Run `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_retraction_discount.py -q` and confirm FAIL for the intended missing behavior.
- [ ] Add the minimal implementation:单事务 JSON patch(读 metadata → dict update → 写回),窗口过滤复用 `recent_event_urls` 的 created_at 比较模式(`database.py` 现有实现),url 匹配用与 PR #85 `_dedup_key` 同构的归一化(tweet_id/bvid 提取后比对,而非裸字符串相等)。
- [ ] Rerun and confirm PASS with no warnings.
- [ ] 补测试:零命中返回 0;`event_type != retracted_action` 不误伤;重复调用幂等(强度不叠折);行数与非 metadata 列不变(不变量 3)。
- [ ] Run `ruff format/check` + `mypy src/openbiliclaw/storage/database.py` + `pytest tests/test_database.py -q` 回归。

**Acceptance:**

- Numeric gate:标记路径 4 用例 + 不变量 3 断言全过,0 失败;`test_database.py` 既有用例零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_retraction_discount.py tests/test_database.py -q`; record the result in the PR.

### Task 1: ingest 钩子(/api/events → 折价)

**Files:** 修改 `src/openbiliclaw/api/app.py`(`/api/events` 接收路径,4407 附近);测试并入 `tests/test_event_retraction_discount.py`。

**Interfaces:** Consumes: accepted events 中 `feedback_type=="retraction"` 的事件(metadata.retracted_action + url)。Produces: 对 DB 的标记调用;`retracted_action` 缺失/空时跳过 + WARNING。

**Steps:**

- [ ] Failing test:POST 一条 X retraction(tweet_id url)后,先前入库的同 tweet_id like 行带上 `retracted=true`。
- [ ] Confirm FAIL(钩子不存在)。
- [ ] 实现钩子:propagate 之后同步调用(事件量低频,无需后台任务);异常捕获为 WARNING 不阻断响应(spec 错误行为)。
- [ ] Rerun PASS;补 bili bvid 键型用例 + retracted_action 缺失跳过用例。
- [ ] Run `pytest tests/test_api_app.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:X/bili 两键型 + 缺失跳过 3 用例全过;`test_api_app.py` 零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_retraction_discount.py tests/test_api_app.py -q`。

### Task 2: 偏好层消费 + 静态 prompt 指引 + 回放不变性

**Files:** 修改 `src/openbiliclaw/soul/preference_analyzer.py`(渲染)、`src/openbiliclaw/llm/prompts.py`(system prompt 常量追加静态一句);测试并入 `tests/test_event_retraction_discount.py` + `tests/test_llm_prompts.py` builder 清单确认。

**Interfaces:** Consumes: `metadata.retracted` / 折后 `signal_strength`。Produces: 事件渲染文本追加"(已撤销)";system prompt 增加撤销语义静态规则(紧跟 `prompts.py:252` 的 signal_strength 条款)。

**Steps:**

- [ ] Failing test:含 `retracted=true` 的 like 事件渲染文本含"(已撤销)"。
- [ ] Confirm FAIL。
- [ ] 实现渲染标记;prompt 常量追加静态句(不引入任何 per-call 数据)。
- [ ] Rerun PASS。
- [ ] **回放不变性测试**:构造不含 retraction 的事件集,断言 `build_preference_analysis_prompt`(或渲染函数)输出与基线快照字节一致——基线在本任务改动前生成并写入测试常量。
- [ ] Run `pytest tests/test_llm_prompts.py tests/test_preference_analyzer.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:回放不变性字节级断言通过;prompt invariance 测试通过;渲染标记用例通过。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_retraction_discount.py tests/test_llm_prompts.py tests/test_preference_analyzer.py -q`。

---

## Wave B — 评论正文 + 弹幕(Phase 2、3)

### Task 3: 服务端 `comment_text`/`comment_kind` 契约与净化

**Files:** 修改 `src/openbiliclaw/sources/event_format.py`;测试 `tests/test_event_format.py`。

**Interfaces:** Consumes: 事件 metadata。Produces: `_sanitize_comment_text`(截断 200 + 剥离 category-C);`comment_kind` 白名单校验(越界→"" + WARNING);`default_signal_strength_for_event` 对 `comment_kind=="danmaku"` 返回 0.6(带校准注释:低于 comment 0.75 因弹幕更随意,持平 follow 0.6)。

**Steps:**

- [ ] Failing tests:201 字符截断、控制字符剥离、越界 comment_kind 清空 + WARNING、danmaku 默认强度 0.6。
- [ ] Confirm FAIL → 最小实现 → PASS。
- [ ] `preference_analyzer.py` preserved metadata keys 增加 `comment_text`/`comment_kind`;渲染含「评论:『…』」;测试断言。
- [ ] 回归:`pytest tests/test_event_format.py tests/test_event_satisfaction.py tests/test_preference_analyzer.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:净化边界 4 用例 + 强度 + 渲染用例全过,零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_format.py tests/test_preference_analyzer.py -q`。

### Task 4: X tap 提取回复正文

**Files:** 修改 `extension/src/main/x-graphql-tap.ts`(CreateTweet 分支)、`extension/src/content/x.ts`(metadata 写入);测试 `extension/tests/x-graphql-tap.test.ts`、`extension/tests/x-content-script.test.ts`。

**Interfaces:** Consumes: `variables.tweet_text`。Produces: `XEngagement.text?: string`(扩展侧截断 200);`buildEventFromEngagement` 写 `metadata.comment_text`。

**Steps:**

- [ ] Failing test:CreateTweet + reply 变量 → engagement 带 text;无 text 字段不回归。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] 断言既有全部 tap 用例(retraction 映射、tweet_id 提取)零回归。
- [ ] `cd extension && npm test && npm run typecheck`。

**Acceptance:**

- Numeric gate:新增 2 用例过 + 既有 x-tap/x-content 用例 0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 5: kernel `extractCommentDraft` 钩子 + bilibili 实现

**Files:** 修改 `extension/src/shared/platforms/types.ts`(可选钩子)、`extension/src/content/kernel.ts`(comment 动作路径调用)、`extension/src/shared/platforms/bilibili.ts`(实现);测试 `extension/tests/kernel.test.ts`、`extension/tests/bilibili-adapter.test.ts`(如无则新建)。

**Interfaces:** Consumes: 点击目标 Element。Produces: 评论草稿字符串(截断 200)或 "";钩子缺失/抛异常时 comment 事件照发、无 `comment_text`。

**Steps:**

- [ ] Failing test:钩子返回文本 → comment 事件 metadata 带 `comment_text`;钩子抛异常 → 事件照发无正文。
- [ ] Confirm FAIL → 实现(bilibili:从 target `closest` 评论容器找 textarea/contenteditable 取值)→ PASS。
- [ ] 其余 6 平台不实现钩子——kernel 测试断言无钩子平台行为与现状完全一致。
- [ ] `npm test && npm run typecheck`。

**Acceptance:**

- Numeric gate:钩子 3 态(有文本/空/异常)用例过;既有 kernel 用例 0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 6: bili 弹幕 MAIN-world tap

**Files:** 新增 `extension/src/main/bili-dm-tap.ts`;修改 `extension/manifest.json`(MAIN world content_scripts 注册,匹配播放页)、`extension/src/content/bilibili.ts`(消息桥);新增 `extension/tests/bili-dm-tap.test.ts`。

**Interfaces:** Consumes: `POST */x/v2/dm/post` 成功响应(字段以真实抓包 fixture 固化)。Produces: postMessage `obc-bili-dm` `{text, bvid/oid}` → comment 事件(`comment_kind="danmaku"`、`comment_text` 截断 200、`signal_strength=0.6`)。

**Steps:**

- [ ] 先本机真实发一条弹幕抓包,把请求/响应形状固化为测试 fixture(记录在 PR;抓不到则以 B站公开 web 端行为为准并在 fixture 注明来源)。
- [ ] Failing test:fixture 请求 → 事件 payload 正确;坏 JSON / 非 2xx / 缺字段 → 静默丢弃。
- [ ] Confirm FAIL → 实现(包裹 fetch/XHR,模式对齐 `xhs-token-sniffer.ts` 的双通道包裹)→ PASS。
- [ ] manifest 注册核对:document_start、world MAIN、只匹配 bilibili.com;`npm run build` 产物包含新入口。
- [ ] `npm test && npm run typecheck && npm run build`。

**Acceptance:**

- Numeric gate:tap 4 态用例过;build 干净。
- Reproduce with `cd extension && npm test 2>&1 | tail -5 && npm run build 2>&1 | tail -2`;真实弹幕端到端结果记录在 PR。

---

## Wave C — xhs 域(Phase 1、4)

### Task 7: xhs 强信号 action tap + DOM 抑制

**Files:** 新增 `extension/src/main/xhs-action-tap.ts`、`extension/tests/xhs-action-tap.test.ts`;修改 `extension/manifest.json`、`extension/src/content/xiaohongshu.ts`(消息桥 + 事件构造)、`extension/src/shared/platforms/xiaohongshu.ts`(`strongSignalSource:"tap"`);修改 `extension/tests/kernel.test.ts`(xhs 抑制集成用例)。

**Interfaces:** Consumes: like/collect/uncollect/dislike 写端点成功调用(端点以真实抓包 fixture 固化;未知端点忽略)。Produces: `{type: like|favorite|retraction, note_id, retracted_action?}` → 事件(url 由 note_id 归一化拼接)。

**Steps:**

- [ ] 本机登录 xhs 抓包一次 like/unlike/collect/uncollect,固化端点与请求形状为 fixture(记录在 PR)。
- [ ] Failing test:四端点 → 正确 engagement;未知端点/坏 payload → 忽略;postMessage source 隔离(不与 token sniffer 串扰)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] adapter 设 `strongSignalSource:"tap"`;kernel 集成测试断言 xhs DOM like/favorite/retraction 零发射、comment/share 仍走 DOM。
- [ ] `npm test && npm run typecheck && npm run build`。

**Acceptance:**

- Numeric gate:tap 用例 + 抑制集成用例全过;既有 xhs 系测试 0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`;真实点赞/取消端到端两条事件记录在 PR。

### Task 8: `xiaohongshu-adapter.test.ts` 补齐

**Files:** 新增 `extension/tests/xiaohongshu-adapter.test.ts`。

**Interfaces:** Consumes: `extension/src/shared/platforms/xiaohongshu.ts` 公开面。Produces: detectPageType / extractNoteId / inferActionType / CARD·SEARCH selector 的用例覆盖,对齐其余 6 平台 adapter 测试结构。

**Steps:**

- [ ] 参照 `twitter-adapter.test.ts` 结构写全量用例(含 24-hex note id 边界、图标按钮无文案返回 null 的现状固化)。
- [ ] `npm test` PASS。

**Acceptance:**

- Numeric gate:xhs adapter 用例 ≥ 其余平台 adapter 测试的最小用例数;0 失败。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 9: xhs selector 统一 + passive 降级测试

**Files:** 新增 `extension/src/content/xhs/selectors.ts`;修改 `extension/src/content/xhs/passive.ts`、`extension/src/content/xhs/bootstrap.ts` 引用;测试 `extension/tests/xhs-passive.test.ts` 增降级用例。

**Steps:**

- [ ] 提取重复 selector 至单一模块(纯移动,行为零变化)。
- [ ] Failing test:selector 全 miss 时 `extractNoteMetadata` 返回部分数据不抛异常 → 实现兜底(如已满足则固化为回归测试)。
- [ ] `npm test && npm run typecheck`。

**Acceptance:**

- Numeric gate:重复 selector 定义处数 2 → 1;降级用例过;既有 passive/bootstrap 用例 0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 10: 后端 ingest 接受 `/discovery/item/` 变体

**Files:** 修改 `src/openbiliclaw/api/app.py`(8186-8190 附近);测试 `tests/test_api_xhs_ingest.py`。

**Steps:**

- [ ] Failing test:`/discovery/item/<24hex>` observed URL 被归一化接受(现状拒收)。
- [ ] Confirm FAIL → 归一化复用既有 note url normalize 函数 → PASS。
- [ ] `pytest tests/test_api_xhs_ingest.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:discovery/item 用例由拒收转接受;既有 ingest 用例 0 回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_api_xhs_ingest.py -q`。

---

### Task 11: 文档与 changelog 同步

**Files:** `docs/modules/extension.md`、`docs/modules/soul.md`、`docs/modules/storage.md`、`docs/architecture.md`、`docs/spec.md`、`docs/changelog.md`(README 图仅当含 tap 粒度时)。

**Steps:**

- [ ] 按 spec「Documentation obligations」逐项更新;changelog 在当前版本块加一条。
- [ ] 核对 CLAUDE.md pre-merge checklist 各项。

**Acceptance:**

- Numeric gate:spec 文档义务清单逐项勾对,PR 描述附核对结果。

## Verification after merge

- 本机真实后端重启后灰度观察 72h:`openbiliclaw cost --by caller` 确认偏好分析 cache 命中率不劣化(prompt 静态性);events 表按日查 `comment_kind="danmaku"`、`comment_text` 非空、`retracted=true` 行数,确认三路信号真实进流。Owner:white。回滚触发:cache 命中率跌破既有水平 10 个百分点,或出现 retraction 误伤(非同 identity 事件被标记)。
- xhs tap 观察:一周内 xhs like/favorite 事件量不为零且无 DOM 双发(同 note 同秒双事件)。

## Explicitly out of scope

- 画像层历史回溯重算(已固化进 profile 各层的旧 like 不追溯,仅影响未来分析批)。
- X/B站以外平台的评论正文、xhs 之外平台的强信号 tap 化。
- 弹幕语义分析(情绪/立场),仅采集文本。
- xhs passive/bootstrap 的 selector 现代化重写(仅去重,不改匹配逻辑)。
