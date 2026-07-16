# 事件采集补全 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-16-event-capture-completion-spec.md`](./2026-07-16-event-capture-completion-spec.md)
> **Status:** r4 — codex 第三轮 6 findings 已修订,待第四轮 review
> **Execution order(r4 重排,codex r3 finding 3):** Wave A(Task 0→1→2)→ **Task 6(kernel 动作粒度抑制契约——Wave B 与 Wave C 的共享前置,必须先于两者合入)** → Wave B(Task 3→4→5)→ Wave C(Task 7→8→9)→ Task 10。文档按 Wave 拆分:每个 Wave 的最后一步完成其文档子集,Wave 才算可交付。Wave A 可独立交付;Wave B/C 各自依赖 Task 6,彼此独立。
> **Tech:** Python 3.11+/仓库 `.venv/bin/python`(勿用裸 `python`);测试 `PYTHONPATH=$PWD/src <venv>/python -m pytest <file> -q`;lint `ruff format/check src/ tests/`;类型 `mypy src/`(strict)。扩展:`cd extension && npm test && npm run typecheck && npm run build`(node --test)。

**Invariants that MUST hold — re-read before each task:**

- Prompt-cache 静态性:system prompt 只能是模块级常量;调用不变性测试全程绿。
- 回放不变性(作用域=事件渲染):无 retraction 标注的事件集,偏好分析事件渲染文本与改动前字节一致。
- 只标注不删除:折价仅 patch metadata / 缓冲 payload,不删行、不改 event_type/url。
- 枚举双白名单:`comment_kind` ∈ `{"", "comment", "danmaku"}`;`retracted_action` ∈ `{"like", "favorite", "share", "follow"}`;越界按缺失处理 + WARNING。
- 正文净化双防线:扩展侧与服务端**各自**截断 ≤200 且剥离 Unicode category-C,两端各有边界测试。
- tap 静默降级:解析异常不抛到页面、不吞既有事件;X tap 既有行为零回归。
- tap 权威按动作粒度 DOM 抑制:adapter `tapAuthoritativeActions` 声明的动作(X 含 share/comment)DOM 零发射;未声明动作与非 tap 平台不变。
- 网络成功 = 业务码成功:新增 tap 路径 HTTP 2xx 且业务码通过才发(B站 `code===0`、xhs 业务字段、X 正文路径无 GraphQL errors);X tap 既有动作发射时序本期不动。
- 原子回滚:每个 tap 化(tap+桥+manifest/build+adapter 声明)单提交交付,回滚即整体 revert。
- 阈值有出处:0.6 / 0.2 / 200 / tombstone TTL 24h/cap 500 常量带校准注释;DB 折价无时间窗口。
- 构建与双浏览器完整性:新 MAIN-world 入口登记 build.mjs entrypoints + Chrome/Firefox 双 manifest,build 产物存在性纳入验收。

---

## Wave A — retraction 确定性折价(Phase 0)

### Task 0: 共享 identity key 模块 + DB 层 `mark_positive_events_retracted`

**Files:** 新增 `src/openbiliclaw/sources/identity_keys.py`(从 `runtime/account_sync.py:41-87` 提升 `_bvid_from_url`/`_mid_from_url`/`_tweet_id_from_url`/`_dedup_key`,**新增 xhs note_id 键型**);修改 `runtime/account_sync.py` 改为引用共享模块(行为零变化);修改 `src/openbiliclaw/storage/database.py`;新增 `tests/test_identity_keys.py`、`tests/test_event_retraction_discount.py`。

**Interfaces:** Consumes: identity urls、retracted_action。**无时间窗口**(identity key 全局唯一无误伤面;撤销数月前的 like 正是应标注场景,codex r2 finding 10)。Produces: 标记行数;被标记行 `metadata.retracted=true`、`metadata.signal_strength=min(现值, 0.2)`。

**Steps:**

- [ ] Write one focused failing test:xhs note_id 键型提取(`explore|discovery/item|search_result` 三形态 24-hex)。
- [ ] Run `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_identity_keys.py -q` and confirm FAIL。
- [ ] 提升共享模块 + 加 note 键型;account_sync 引用改造后跑 `pytest tests/test_account_sync.py -q` 断言零回归。
- [ ] Failing test:同 identity key(而非裸 url 相等)的 like 行被标记、patch 正确、返回计数。
- [ ] 实现 `mark_positive_events_retracted`(单事务读 metadata → dict update → 写回;无窗口,按 identity key 全表匹配对应 event_type 行)。
- [ ] 补测试:零命中返回 0;跨 event_type 不误伤;幂等(强度不叠折);行数与非 metadata 列不变(不变量 3);四键型(tweet/bvid/mid/note)。
- [ ] Run `ruff format/check` + `mypy` + `pytest tests/test_database.py tests/test_account_sync.py -q` 回归。

**Acceptance:**

- Numeric gate:identity 四键型 + DB 标记 5 用例全过,0 失败;`test_database.py`/`test_account_sync.py` 零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_identity_keys.py tests/test_event_retraction_discount.py tests/test_database.py tests/test_account_sync.py -q`; record in PR.

### Task 1: `ingest_batch` 原子折价阶段 + 乱序 tombstone + API 层 DB 钩子

**Files:** 修改 `src/openbiliclaw/soul/pipeline.py`(`ingest_batch` 开头新增原子折价预处理 + tombstone 集;`pipeline.py:669-681` 阈值消费必须发生在折价之后)、`src/openbiliclaw/api/app.py`(接收路径只加 DB 面钩子);测试并入 `tests/test_event_retraction_discount.py` + `tests/test_pipeline_advanced.py`。

**Interfaces:** Consumes: 批内/缓冲内信号 + retraction 信号(url + `retracted_action`)。Produces: (a) **原子预处理(阈值消费前)**:批内与既有缓冲中同 identity key、类型匹配、**事件时间早于 retraction 时间**的正向信号被 patch(`retracted=true`、强度 ≤0.2);(b) tombstone `(identity_key, action) → retraction 事件时间戳`(TTL 24h、cap 500 逐出最旧,校准注释),入场折价仅当正向信号事件时间早于 tombstone 时间(`like→retract→like` 第二个 like 不折;事件时间缺失保守不折);(c) API 层 DB 标记调用;两侧均做 `retracted_action` 白名单校验(`{"like","favorite","share","follow"}` 之外跳过 + WARNING,不变量 4)。

**Steps:**

- [ ] Failing test(原子性,codex r3 blocker 1):批内正向信号数恰好触发阈值消费(threshold-ready)且含同批 retraction → 断言 `_update_layer` 收到的信号已折价。
- [ ] Confirm FAIL → 在 `ingest_batch` 开头实现原子折价阶段(批内 + 既有缓冲,复用 Task 0 identity 模块)→ PASS。
- [ ] Failing test(乱序 + 因果):retraction 先到 → tombstone 登记;随后同 `(key, action)` 且事件时间更早的 like → 入场折价;`like→retract→like` 第二个 like(事件时间晚于 retraction)不折;同 key 跨动作(like tombstone 不折 favorite);TTL 过期/cap 逐出后不折。
- [ ] Confirm FAIL → 实现 tombstone → PASS。
- [ ] Failing test(DB 面):POST X retraction 后,先前入库同 tweet_id like 行带 `retracted=true`;实现 API 层钩子(白名单 → `mark_positive_events_retracted`;异常 WARNING 不阻断响应)。
- [ ] 补用例:bili bvid 键型;`retracted_action` 缺失/越界(如 `"view"`)跳过 + WARNING 断言(pipeline 侧与 API 侧各一)。
- [ ] Run `pytest tests/test_api_app.py tests/test_pipeline_advanced.py -q` 回归 + ruff + mypy。

**Acceptance:**

- Numeric gate:原子性 threshold-ready 用例 + 乱序/因果 5 态 + DB 面 + 键型 + 双侧白名单 ≥10 用例全过;`test_api_app.py`/`test_pipeline_advanced.py` 零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_retraction_discount.py tests/test_api_app.py tests/test_pipeline_advanced.py -q`。

### Task 2: 全消费面渲染标记 + 静态 prompt 指引 + 渲染回放不变性

**Files:** 修改 `src/openbiliclaw/soul/preference_analyzer.py`(渲染)、`src/openbiliclaw/llm/prompts.py`(system prompt 常量追加静态撤销语义一句,紧跟 `prompts.py:252`)、**events 重读消费面清单内的其余渲染点(至少 `soul/cognition_cycle.py:247` 的 12h 认知循环 awareness 输入,codex r2 finding 5)**;测试并入 `tests/test_event_retraction_discount.py`;`tests/test_llm_prompts.py` builder 清单核对。

**Interfaces:** Consumes: `metadata.retracted` / 折后 `signal_strength`。Produces: 所有重读消费面的事件渲染文本追加"(已撤销)";静态 prompt 规则(不引入 per-call 数据,不变量 1)。

**Steps:**

- [ ] **先做基线**:改动前对固定的无 retraction 事件集生成渲染文本快照,存入测试常量(此步在本任务任何实现改动之前完成并单独提交)。
- [ ] **grep events 重读点清单**:全仓找出重读 events 表进 LLM 的消费面(preference / cognition_cycle / 其它),清单写入 PR;共用渲染函数的面标注"自动生效",不共用的逐一补标记。
- [ ] Failing test:含 `retracted=true` 的 like 事件在 preference 渲染含"(已撤销)";cognition 面同断言(或断言共用渲染函数)。
- [ ] Confirm FAIL → 实现渲染标记 + prompt 常量追加静态句 → PASS。
- [ ] **渲染回放不变性测试**:无 retraction 事件集的渲染输出与基线快照字节一致(不变量 2 作用域=事件渲染,system prompt 版本性变更不在此门)。
- [ ] Run `pytest tests/test_llm_prompts.py tests/test_preference_analyzer.py tests/test_soul_engine.py -q` + ruff + mypy。
- [ ] **Wave A 文档 gate**:更新 `docs/modules/soul.md`(双面折价 + 消费面清单)、`docs/modules/storage.md`(`mark_positive_events_retracted`)、`docs/changelog.md` 条目——Wave A 至此才算可交付。

**Acceptance:**

- Numeric gate:渲染回放不变性字节断言 + prompt 调用不变性 + preference/cognition 双面标记用例全过;重读点清单在 PR 中且逐项标注处理方式。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_retraction_discount.py tests/test_llm_prompts.py tests/test_preference_analyzer.py -q`。

---

## Wave B — 评论正文 + 弹幕(Phase 2、3)

### Task 3: 服务端 `comment_text`/`comment_kind` 契约 + 扩展侧共享净化工具

**Files:** 修改 `src/openbiliclaw/sources/event_format.py`、`src/openbiliclaw/soul/preference_analyzer.py`(preserved keys + 渲染);新增 `extension/src/shared/text-sanitize.ts`;测试 `tests/test_event_format.py`、`tests/test_preference_analyzer.py`、`extension/tests/text-sanitize.test.ts`。

**Interfaces:** Produces: 服务端 `_sanitize_comment_text`(截断 200 + 剥离 category-C);`comment_kind` 白名单(越界→"" + WARNING);`default_signal_strength_for_event` 对 danmaku 返回 0.6(校准注释:低于 comment 0.75 因弹幕更随意,持平 follow 0.6);扩展侧 `sanitizeUserText(s, 200)` 同规格(不变量 5 双防线)。

**Steps:**

- [ ] Failing tests(服务端):201 字符截断、控制字符(含 ` `/`​` 类)剥离、越界 comment_kind 清空 + WARNING、danmaku 默认强度 0.6。
- [ ] Confirm FAIL → 最小实现 → PASS。
- [ ] Failing tests(扩展):`sanitizeUserText` 同边界(截断/category-C/空串/非字符串)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] preserved keys 增加 `comment_text`/`comment_kind`;渲染「评论:『…』」;测试断言。
- [ ] 回归:`pytest tests/test_event_format.py tests/test_event_satisfaction.py tests/test_preference_analyzer.py -q` + `cd extension && npm test` + ruff + mypy + typecheck。

**Acceptance:**

- Numeric gate:两端净化边界各 ≥4 用例 + 强度 + 渲染用例全过,零回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_event_format.py tests/test_preference_analyzer.py -q && cd extension && npm test 2>&1 | tail -5`。

### Task 4: X tap 提取回复正文

**Files:** 修改 `extension/src/main/x-graphql-tap.ts`(CreateTweet 分支)、`extension/src/content/x.ts`(metadata 写入,经 `sanitizeUserText`);测试 `extension/tests/x-graphql-tap.test.ts`、`extension/tests/x-content-script.test.ts`。

**Interfaces:** Consumes: `variables.tweet_text`。Produces: `XEngagement.text?: string`;`buildEventFromEngagement` 写 `metadata.comment_text`(净化后)、`comment_kind="comment"`。

**Steps:**

- [ ] Failing test:CreateTweet + reply 变量 → engagement 带净化后 text;**响应含 GraphQL `errors` 时不附带正文(业务码校验,不变量 7b;X tap 既有动作发射时序不动)**;无 text 字段不回归;非 reply 的 CreateTweet 行为不变。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] 断言既有全部 tap 用例(retraction 映射、tweet_id 提取)零回归(不变量 6)。
- [ ] `cd extension && npm test && npm run typecheck`。

**Acceptance:**

- Numeric gate:新增 4 用例过 + 既有 x-tap/x-content 用例 0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 5: bili-interact-tap(dm/post 弹幕 + reply/add 评论)

**Files:** 新增 `extension/src/main/bili-interact-tap.ts`、`extension/tests/bili-interact-tap.test.ts`;修改 `extension/scripts/build.mjs`(entrypoints)、Chrome manifest 与 Firefox manifest(MAIN world、document_start、匹配 bilibili.com)、`extension/src/content/bilibili.ts`(消息桥 `obc-bili-interact` → 事件构造,正文经 `sanitizeUserText`)。

**Interfaces:** Consumes: `POST */x/v2/dm/post`、`POST */x/v2/reply/add`(**HTTP 2xx 且响应 `code===0` 才算成功**,不变量 7b;字段以真实抓包 fixture 固化,fixture 注明来源)。Produces: comment 事件——弹幕:`comment_kind="danmaku"`、`signal_strength=0.6`;评论:`comment_kind="comment"`;均带净化后 `comment_text` 与视频/稿件 url。**bilibili adapter 声明 `tapAuthoritativeActions: {comment}`**(消除"打开评论区即记 comment"假事件与提交双计,codex r2 finding 2)。依赖:Task 6(kernel 契约)已按 r4 执行序作为 Wave B 前置先行合入,本任务直接使用该契约。

**Steps:**

- [ ] 本机真实发一条弹幕 + 一条评论抓包,固化请求/响应形状为 fixture(记录在 PR)。
- [ ] Failing test:两端点 fixture → 事件 payload 正确;坏 JSON / 非 2xx / **2xx 但 `code!==0`** / 缺字段 → 静默丢弃;tap 不改写请求。
- [ ] Confirm FAIL → 实现(fetch/XHR 双通道包裹,模式对齐 `xhs-token-sniffer.ts`)→ PASS。
- [ ] bilibili adapter 声明 `tapAuthoritativeActions: {comment}`;kernel 测试断言 B站 DOM comment 点击零发射。
- [ ] build.mjs entrypoints + Chrome/Firefox 双 manifest 登记;`npm run build` 后断言 `dist/main/bili-interact-tap.js` 存在(不变量 9)。
- [ ] **原子交付**:tap + 桥 + manifest/build + adapter 声明合为单提交,哈希记录在 PR(回滚 = 整体 revert)。
- [ ] `npm test && npm run typecheck && npm run build`。
- [ ] **Wave B 文档 gate**:更新 `docs/modules/extension.md`(bili-interact-tap + comment_text)、`docs/privacy.md`(个人通讯范围)、**`docs/chrome-webstore-listing.md`(商店 listing 隐私描述,r4)+ PR 记录商店后台隐私披露表单需更新的操作项**、`docs/architecture.md` + `docs/spec.md` + README 双语架构图(新 tap 节点)、`docs/changelog.md`——Wave B 至此才算可交付。

**Acceptance:**

- Numeric gate:tap ≥6 态用例过(含业务码失败);DOM comment 抑制用例过;build 产物断言过;Firefox smoke-build(CI 既有 job)不回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5 && npm run build 2>&1 | tail -2 && ls dist/main/ | grep bili-interact`;真实弹幕+评论端到端结果记录在 PR。

---

## Wave C — xhs 域(Phase 1、4)

### Task 6: kernel 动作粒度抑制契约(`tapAuthoritativeActions`)

**Files:** 修改 `extension/src/shared/types.ts`(`PlatformAdapter` 接口所在文件,r4 修正路径;新字段 `tapAuthoritativeActions`,动作类型沿用 `inferActionType` 返回的字符串动作域,实现时确认是否已有具名类型)、`extension/src/content/kernel.ts`(正向路径 321-327 与撤销路径 301-306 统一为集合检查)、`extension/src/shared/platforms/twitter.ts`(声明 `{like, favorite, share, comment, retraction}`——其 tap 已发全部五类,消除 DOM share/comment 双计与"打开菜单即记事件",codex r2 findings 2/4);测试 `extension/tests/kernel.test.ts`、`extension/tests/twitter-adapter.test.ts`。

**Interfaces:** Consumes: adapter `tapAuthoritativeActions`。Produces: 声明动作 DOM 零发射;未声明动作与非 tap 平台完全不变;既有 `strongSignalSource` 语义收敛(保留或替换由实现决定,带迁移测试)。

**Steps:**

- [ ] 先核对 X 现状:确认 twitter adapter/kernel 当前正向 like / share / comment 是否会 DOM 发射(写进 PR 描述;若另有去重机制,统一收敛到本契约并保留回归测试)。
- [ ] Failing test(抑制矩阵):声明平台 × {like, favorite, share, comment, retraction} DOM 点击 → 零事件;未声明动作(view/scroll 等)照常;非 tap 平台全动作照发。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] X 全量回归:`x-graphql-tap.test.ts`、`x-content-script.test.ts`、`twitter-adapter.test.ts`、`kernel.test.ts` 0 失败。
- [ ] `npm test && npm run typecheck`。

**Acceptance:**

- Numeric gate:抑制矩阵(声明/未声明/非 tap × 五动作 + 非强信号)≥8 用例全过;X 系测试 0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 7: xhs 强信号 action tap + adapter 切换

**Files:** 新增 `extension/src/main/xhs-action-tap.ts`、`extension/tests/xhs-action-tap.test.ts`;修改 `extension/scripts/build.mjs`、Chrome/Firefox 双 manifest、`extension/src/content/xiaohongshu.ts`(消息桥 `obc-xhs-action` + 事件构造)、`extension/src/shared/platforms/xiaohongshu.ts`(`strongSignalSource:"tap"`);`extension/tests/kernel.test.ts` 增 xhs 集成用例。

**Interfaces:** Consumes: like/collect/uncollect/dislike 写端点调用(**HTTP 2xx 且业务成功字段通过才发**,不变量 7b;端点以真实抓包 fixture 固化;未知端点忽略)。Produces: `{type: like|favorite|retraction, note_id, retracted_action?}` → 事件(url 由 note_id 归一化拼接,与 Task 0 identity key 同构)。

**Steps:**

- [ ] 本机登录 xhs 抓包 like/unlike/collect/uncollect 各一次,固化端点与请求/响应形状(含业务成功/失败字段)为 fixture(记录在 PR)。
- [ ] Failing test:四端点 → 正确 engagement;未知端点/坏 payload/非 2xx/**2xx 但业务失败** → 忽略;postMessage source 与 token sniffer 隔离。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] adapter 声明 `tapAuthoritativeActions: {like, favorite, retraction}`;kernel×xhs 集成测试:DOM like/favorite/retraction 零发射(依赖 Task 6),comment/share 仍走 DOM(xhs 无 comment tap)。
- [ ] build.mjs + 双 manifest 登记;`npm run build` 断言 `dist/main/xhs-action-tap.js` 存在。
- [ ] **原子交付**:tap + 桥 + manifest/build + adapter 声明合为单提交,哈希记录在 PR(回滚 = 整体 revert,禁止只回退声明留 tap 运行)。
- [ ] `npm test && npm run typecheck && npm run build`。

**Acceptance:**

- Numeric gate:tap ≥7 用例(含业务失败)+ 集成抑制用例全过;既有 xhs 系测试(passive/sniffer/task-executor/state-bridge/native-save)0 回归;build 产物断言过。
- Reproduce with `cd extension && npm test 2>&1 | tail -5 && ls dist/main/ | grep xhs-action`;真实点赞/取消端到端两条事件 + 无双发验证记录在 PR。

### Task 8: `xiaohongshu-adapter.test.ts` + selector 统一 + passive 降级契约固化

**Files:** 新增 `extension/tests/xiaohongshu-adapter.test.ts`、`extension/src/content/xhs/selectors.ts`;修改 `extension/src/content/xhs/passive.ts`、`extension/src/content/xhs/bootstrap.ts`(引用共享 selector,纯移动);测试 `extension/tests/xhs-passive.test.ts` 增降级用例。

**Steps:**

- [ ] 参照 `twitter-adapter.test.ts` 结构写 xhs adapter 全量用例(detectPageType/extractNoteId 24-hex 边界/inferActionType 含图标按钮无文案返回 null 的现状固化/CARD·SEARCH selector)。
- [ ] selector 提取至单一模块(行为零变化),`npm test` 断言 passive/bootstrap 既有用例 0 回归。
- [ ] 降级契约回归测试(**不改行为**):空 title → `extractNoteMetadata` 返回 `null`(固化 `passive.ts:182` 既有防空卡契约);部分字段缺失 → 返回部分数据不抛异常。
- [ ] `npm test && npm run typecheck`。

**Acceptance:**

- Numeric gate:xhs adapter 用例数 ≥ twitter-adapter.test.ts 用例数;重复 selector 定义处数 2 → 1;降级契约 2 用例过;0 回归。
- Reproduce with `cd extension && npm test 2>&1 | tail -5`。

### Task 9: 后端 ingest `urls` 分支接受 `/discovery/item/` 变体

**Files:** 修改 `src/openbiliclaw/api/app.py`(8186-8190 附近,仅 `urls` 裸链分支);测试 `tests/test_api_xhs_ingest.py`。

**Steps:**

- [ ] Failing test:`/discovery/item/<24hex>` 裸链 observed URL 被归一化接受(现状拒收);`notes` 分支行为不变断言。
- [ ] Confirm FAIL → 归一化复用既有 note url normalize 函数 → PASS。
- [ ] `pytest tests/test_api_xhs_ingest.py -q` + ruff + mypy。

**Acceptance:**

- Numeric gate:discovery/item 裸链用例由拒收转接受;`notes` 分支 + 既有 ingest 用例 0 回归。
- Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_api_xhs_ingest.py -q`。

---

### Task 10: Wave C 文档 gate + 总核对

**Files:** `docs/modules/extension.md`(xhs-action-tap + tapAuthoritativeActions 契约)、`docs/architecture.md` + `docs/spec.md` + README 双语架构图(xhs tap 节点,若 Wave B 已画 tap 层则增量)、`docs/changelog.md`。(Wave A/B 各自的文档子集已在 Task 2 / Task 5 内完成。)

**Steps:**

- [ ] 完成 Wave C 文档子集;changelog 补条目。
- [ ] **总核对**:对照 spec「Documentation obligations」与 CLAUDE.md pre-merge checklist 逐项确认三个 Wave 的文档均已落地,结果写入 PR 描述。

**Acceptance:**

- Numeric gate:文档义务清单(含 privacy.md 与 README 双语图)逐项勾对,PR 描述附核对结果。

## Verification after merge

- **渲染静态性由测试兜底**:prompt 调用不变性 + 渲染回放不变性测试是主门。线上 `openbiliclaw cost --by caller` 的偏好/认知 caller 命中率仅作参考观察(system prompt 版本化变更本就会冷却一次旧缓存,且无最小样本量,不设自动回滚门,codex r2 finding 9);合并 72h 后命中率仍异常低(<30%,工具红色阈值)时人工排查是否引入了 per-call 漂移。
- **信号真实进流**:每日查询 events 表——`json_extract(metadata,'$.comment_kind')='danmaku'` 行数、`json_extract(metadata,'$.comment_text') != ''` 行数、`json_extract(metadata,'$.retracted')` 为真行数(r3 修正:三者均在 metadata JSON 内,codex r2 finding 8);三者在真实使用一天后均 >0(配合本机真实操作)。
- **双发检测(跨秒)**:xhs like/favorite 事件按 note identity key 分组,同 key 在 ±5s 窗口内出现 2 条同类型事件即视为 DOM/tap 双发 → 回滚 = revert Task 7 的原子提交(tap 与 adapter 声明整体回退)。Owner:white,持续一周。

## Explicitly out of scope

- 画像层历史回溯重算(已固化进 profile 各层的旧证据不追溯;折价只作用于缓冲面与未来重读面)。
- X/B站以外平台的评论正文、xhs 之外平台的强信号 tap 化。
- 弹幕/评论语义分析(情绪/立场),仅采集文本。
- xhs passive/bootstrap selector 现代化重写(仅去重,不改匹配逻辑;降级契约只固化不变更)。
