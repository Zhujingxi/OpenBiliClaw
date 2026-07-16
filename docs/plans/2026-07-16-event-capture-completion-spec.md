# 事件采集补全 Spec — retraction 画像折价、评论/弹幕正文、xhs 强信号脱离 DOM 裸奔

**Created:** 2026-07-16(r2,codex 对抗 review 第一轮 14 findings 修订)
**Scope:** 画像偏好层(`soul/`)、事件契约(`sources/event_format.py`)、存储(`storage/database.py`)、浏览器扩展采集(`extension/src/main/`、`extension/src/content/`、`extension/src/shared/platforms/`、`extension/scripts/build.mjs`、Chrome/Firefox 双 manifest)、xhs 后端 ingest(`api/app.py`)、隐私政策(`docs/privacy.md`)。
**Out of scope:** 评论/弹幕之外的 UGC 正文;xhs 之外平台的强信号 tap 化;画像层历史回溯重算(已固化进 profile 各层的旧正向证据不追溯,折价只作用于「尚未被分析批消费」与「未来重读 events 表」两个面,见 Phase 0);移动 Web / CLI 新增可视面(全部为采集与画像内部改动,四端无新 UI,依 CLAUDE.md 第 5 条显式声明排除)。

## Goal

2026-07-05 事件获取体检遗留四个缺口(PR #85 合并时明确 out-of-scope),导致画像输入失真或缺失:

1. **撤销的赞永久留在画像证据里**:retraction 事件已采集,但对正向事件(like/favorite/share)零折价——用户明确"反悔"的兴趣证据继续以满强度(0.85/1.0)参与后续消费。
2. **评论正文近乎零采集**:除 X 经 GraphQL tap 发出的 comment(只带 reply 目标 id、正文被刻意丢弃)外,各平台 comment 事件只记录"点了评论按钮";用户亲手写的文字——最强的兴趣表达之一——全部丢弃。
3. **弹幕零采集**:B站用户最高频的表达行为完全不存在于事件流。
4. **xhs 强信号裸奔**:like/favorite 靠按钮文案匹配(图标按钮直接漏采),aria-pressed 撤销"名义覆盖、实际未验证";xhs 与 bilibili 是仅有的两个缺 adapter 单测的平台(本 spec 补 xhs,bilibili 随 Wave B 的 bili tap 测试一并覆盖其新增面)。

目标结果与验证命令:

- retraction 到达后:(a) pipeline 缓冲区内同 identity key 的未消费正向信号被确定性折价;(b) events 表同 key 正向行被标注 `retracted`(服务未来全量重建 / 画像整理等重读路径);无 retraction 时事件渲染输出与现状**字节一致**。验证:`pytest tests/test_event_retraction_discount.py tests/test_preference_analyzer.py -q`。
- X 回复正文与 B站评论正文(**均走网络层、提交成功后采集**)进入 `metadata.comment_text`(双端截断 ≤200 字符 + 剥离 Unicode category-C);B站弹幕以 `comment_kind="danmaku"` 入库,强度 0.6。验证:`cd extension && npm test`、`pytest tests/test_event_format.py -q`。
- xhs like/collect(及撤销)改由 MAIN-world 网络 tap 认定(`strongSignalSource:"tap"`),DOM 路径的正向与撤销强信号**均被抑制**;新增 `xiaohongshu-adapter.test.ts`。验证:`cd extension && npm test && npm run typecheck && npm run build`(build 产物必须包含新 tap 入口)。

## Design invariants (MUST hold in every phase)

1. **Prompt-cache 静态性**:system prompt 只能是模块级常量,内容可随版本变化但必须调用不变;`tests/test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant` 全程绿。
2. **无 retraction 即无差异(回放不变性,作用域=事件渲染)**:对不含 retraction 标注的事件集,偏好分析的**事件渲染文本(user prompt 段)**与改动前字节一致。system prompt 常量的版本性变更不在此门内(它由不变量 1 管辖)。验证面:`tests/test_event_retraction_discount.py::test_event_rendering_invariance_without_retractions`。
3. **只标注不删除**:retraction 折价通过 metadata 标注(`retracted=true` + 折后强度)与缓冲区信号标记实现;events 表任何行都不被删除、event_type/url 不被改写。验证面:DB 层测试断言行数与非 metadata 列不变。
4. **枚举双白名单**:不新增顶层 event_type;`comment_kind` ∈ `{"", "comment", "danmaku"}`;`retracted_action` ∈ `{"like", "favorite", "share", "follow"}`——两者越界值均按缺失处理并 log WARNING(CLAUDE.md pitfall #4),折价钩子对越界 `retracted_action` 直接跳过。
5. **正文净化双防线(两端都做全套)**:`comment_text` 在**扩展侧与服务端各自**截断 ≤200 字符**且**剥离 Unicode category-C;两端各有边界测试,服务端为最终防线。
6. **tap 失败静默降级**:xhs action tap、bili interact tap 的任何解析异常不得抛出到页面、不得吞掉既有事件;X tap 既有行为(engagement 类型、tweet_id 提取、retraction 映射)零回归。验证面:tap 单测异常路径 + 既有 `x-graphql-tap.test.ts` 全绿。
7. **tap 权威按动作粒度 DOM 抑制(r3)**:adapter 声明 `tapAuthoritativeActions`(动作集合,如 X: `{like, favorite, share, comment, retraction}`;xhs: `{like, favorite, retraction}`;bilibili: `{comment}`——reply/add tap 上线后),kernel 对声明动作的 DOM 发射全部抑制——消除"网络提交 + DOM 点击"双计与"仅打开评论区/转发菜单即记事件"的假动作(codex r2 findings 2/4;现状 `kernel.ts:301-306` 只抑制撤销分支)。未声明动作与非 tap 平台行为不变。验证面:kernel 抑制矩阵测试(平台 × 动作)。
7b. **网络成功 = 业务码成功(r3)**:新增 tap 路径(bili reply/dm、xhs action、X 正文提取)发射前必须校验**业务码**(B站 `code===0`、xhs 业务 success 字段、X GraphQL 响应无 `errors`),HTTP 2xx 但业务失败一律不发(codex r2 finding 3)。X tap 既有动作(like/retraction 等)的发射时序**本期不动**(改动会影响既有事件量,超出范围,spec 记为已知差异)。验证面:各 tap 的业务失败 fixture 用例。
8. **阈值有出处**:弹幕强度 0.6、折后强度 0.2、正文截断 200、tombstone TTL 24h/cap 500 等常量必须带校准注释,provider/模型更换后重开校准(CLAUDE.md pitfall #3)。DB 折价无时间窗口(见 Phase 0 面 2)。
9. **构建与双浏览器完整性**:新增 MAIN-world 入口必须同时登记 `extension/scripts/build.mjs` entrypoints、Chrome manifest 与 Firefox manifest;`npm run build` 产物存在性是验收的一部分(CLAUDE.md pitfall #6 的扩展版:多安装形态必须同步)。

## Current diagnosis

### D1. retraction 已采集但画像侧无折价机制,且分析批不重读 events 表

- 产生点:`extension/src/main/x-graphql-tap.ts:82-84,218-227`(X tap 权威)、`extension/src/content/kernel.ts:301-318`(DOM aria-pressed),payload 均为 `{type:"feedback", metadata:{feedback_type:"retraction", retracted_action, signal_strength:0.2}}`。
- 反馈批学习**显式排除** retraction:`soul/engine.py:974-976`(`_is_retraction_feedback`,`engine.py:1592-1597`)。
- 增量管线**不排除**:`soul/pipeline.py:382-403` 强制 retraction 走 `SignalType.BEHAVIOR_EVENT`,仍进 INTEREST 层 → `soul/layer_updaters.py:149-176` → `preference_analyzer.analyze_events`;satisfaction=neutral(`sources/event_format.py:144-145`)通过过滤(`preference_analyzer.py:220-237`)。
- **关键数据流事实(r2 修订)**:增量画像消费的是**内存中的事件/信号**——`api/app.py:4423` `_ingest_profile_update_events(accepted_events)` 直接用请求里的事件对象;pipeline 缓冲的是内存信号。**分析批不重读 events 表**,因此仅做 DB 标注对增量路径无效。events 表被重读的面是:`openbiliclaw init` 全量重建、12h 画像整理(profile consolidation)等离线路径。折价设计必须双管齐下:内存缓冲折价(作用于未消费信号)+ DB 标注(作用于未来重读)。
- `signal_strength=0.2` 只被 LLM prompt 自然语言指引消费(`llm/prompts.py:252`),无确定性权重计算。
- **对被撤销的那次正向事件零处理**:grep `retraction` 在 `interest_writeback.py`、`negative_exemplars.py`、`dislike_writeback.py`、`prompts.py` 全空。
- 既有测试:`tests/test_event_satisfaction.py:49-66`、`tests/test_event_format.py:415-429`、`tests/test_pipeline_advanced.py:249-258`、`tests/test_soul_engine.py:602-690`。盲区:无折价测试(机制不存在)。

### D2. 评论正文链路缺失(X 有 comment 动作事件但正文被丢弃;其余平台只有按钮点击)

- X tap 对 `CreateTweet` 已发 comment 事件但**刻意丢弃 `tweet_text`**:`extension/src/main/x-graphql-tap.ts:229-238` 只取 `in_reply_to_tweet_id`;`XEngagement` 无 text 字段。
- 其余平台 comment 事件是点击动作:kernel `observeClicks`(`extension/src/content/kernel.ts:277-329`)→ `buildActionHintFromClickTarget`(`extension/src/shared/behavior.ts:53-65`)→ 各平台 `inferActionType` 关键词匹配(bilibili.ts:58 等)。metadata 只有按钮文案/`href`/`actionLabel`。
- **DOM 读评论框方案被否决(r2)**:kernel 的 comment 动作在"点击任何匹配『评论』文案的元素"时触发(打开评论区、点回复入口都算),此时读输入框会采到**未发送草稿**;而真正的提交按钮文案(「发布」「发送」)根本不匹配 comment 关键词(`bilibili.ts:58`)。正文采集必须以**网络层提交成功**为准:B站评论提交走 `POST /x/v2/reply/add`(实现时以真实抓包 fixture 固化),与弹幕 tap 合并为一个 MAIN-world tap。
- 后端契约:`sources/event_format.py:36-50` metadata 无正文字段;`body_text` 已被"发现内容候选正文"占用(`discovery/x_normalize.py:135-136`、`llm/prompts.py:1140`),需新名 `comment_text`。comment 权重已存在(`event_format.py:266`,0.75)。
- 画像序列化器 `build_profile_summary`(`discovery/strategies/_utils.py:442-511`)不含原始正文;正文影响画像必须经偏好分析事件渲染(`preference_analyzer.py:72-74` preserved keys)。
- **隐私面(r2)**:评论/弹幕正文属用户通讯内容;`docs/privacy.md:20` 当前只声明侧边栏聊天消息为「个人通讯」采集项——扩大采集范围必须同步隐私政策与商店披露文案。

### D3. 弹幕零采集

- 全仓 `danmaku` 只是展示计数:`bilibili/api.py:94,562`、`extension/popup/popup.js:2877`。扩展侧无任何弹幕监听。
- B站播放页采集落点:`extension/src/content/bilibili.ts:1-14`(仅 startCollector + message listener)。弹幕发送走 `POST /x/v2/dm/post`(实现时以真实抓包 fixture 确认字段,预期 `msg`、`oid`、`bvid/aid`)。MAIN-world 网络 tap(仿 `x-graphql-tap.ts`)是既有验证过的稳路径;与 D2 的 reply/add 同属 bilibili 写接口,**合并为一个 `bili-interact-tap`**。

### D4. xhs 强信号裸奔 + 测试债 + ingest 不一致

- 强信号纯靠文案匹配:`extension/src/shared/platforms/xiaohongshu.ts:41-55`,图标按钮漏采。源码自认:`xiaohongshu.ts:3-5`、`extension/src/content/xiaohongshu.ts:5-6`、`extension/src/content/xhs/passive.ts:158-159,179-182`。注释称 deliberately skipped 但 `inferActionType` 实际仍发 like/favorite——信号在发、质量未知。
- aria-pressed 撤销对 xhs"名义覆盖":机制在 kernel(`kernel.ts:299-319`),无测试/fixture 证明 xhs 真实 DOM 满足前提。
- **kernel 抑制缺口(r2)**:`strongSignalSource:"tap"` 现有逻辑只在撤销分支生效(`kernel.ts:301-306`);正向 like/favorite 的 DOM 发射路径(`kernel.ts:321-327`)**没有 tap 抑制**——xhs tap 化必须先扩展 kernel 抑制契约,并核对 X 当前靠什么避免正向双发(实现时确认 twitter adapter 行为后统一)。
- xhs 已有 MAIN-world tap 基建:`extension/src/main/xhs-token-sniffer.ts:1-196`(`isXhsApiUrl` = `/api/sns/web/` 或 `edith.xiaohongshu.com`),只抓 token。like/collect 写端点(预期 `/api/sns/web/v1/note/like`、`/note/collect`、撤销 `/note/dislike`、`/note/uncollect`;以真实抓包 fixture 固化)与 token 流量同源。
- 测试债:xhs 缺 adapter 测试(`inferActionType`/`detectPageType`/`extractNoteId`/selector 零覆盖);passive 只测命中、未测降级。
- ingest 不一致(r2 精确化):采集端接受 `/discovery/item/`(`xiaohongshu.ts:13-19`);后端 `POST /api/sources/xhs/observed-urls` 的 **`urls` 裸链分支**只收 `/explore/`(`api/app.py:8186-8190`)丢弃变体,rich `notes` 分支(`app.py:8200` `_cache_xhs_notes`)仍按最后路径段提取 note_id 可进。修复只针对 `urls` 分支。
- selector 重复:`passive.ts:174-191` 与 `bootstrap.ts:822-830` 双处维护。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | retraction 确定性折价(缓冲折价 + DB 标注 + 渲染消费 + 回放不变性) | **MUST** | 画像正确性:用户明确反悔的证据仍满强度参与消费 |
| 1 | xhs 强信号 tap 化 + kernel 正向抑制契约 + adapter 测试 | **MUST** | 当前在发未知质量的强信号(1.0/0.85 权重),数据质量风险最大 |
| 2 | 评论正文采集(X tap 正文 + bili interact tap 的 reply/add) | RECOMMENDED | 最强兴趣表达,新增能力 |
| 3 | B站弹幕采集(bili interact tap 的 dm/post) | RECOMMENDED | 高频表达行为,与 Phase 2 共享同一 tap |
| 4 | xhs selector 统一 + ingest urls 分支对齐 + passive 降级契约固化 | RECOMMENDED | 维护性债务,随 Phase 1 顺手清 |

依赖:Phase 2/3 共享 `comment_text` 服务端契约(Wave B 首任务)与 `bili-interact-tap`(一个 tap 文件、两组端点),建议同一执行者连续完成;Phase 1 的 kernel 抑制契约改动影响所有 tap-authoritative 平台,必须带 X 回归测试;Phase 0/1/4 相互独立。

**Wave A(可独立交付)**:Phase 0。**Wave B**:Phase 2 + 3。**Wave C**:Phase 1 + 4。任一 Wave 完成即可安全停下发布——**前提是该 Wave 的文档义务(模块文档/changelog/架构图/隐私政策中与其相关的子集)已随 Wave 完成**(r3,codex r2 finding 6:文档 gate 逐 Wave 执行,不许推迟到全部 Wave 之后)。

## Phase designs

### Phase 0 — retraction 确定性折价

**折价作用面(r3 修订钩子时序与乱序)**:
- **面 1(内存,主路径;r4 原子化)**:折价作为 `ProfileUpdatePipeline.ingest_batch()` 开头的**原子预处理阶段**,在任何阈值消费(`_update_layer`)之前执行:(a) 扫描本批 retraction 信号,对**批内**同 identity key、事件类型 == retracted_action、且事件时间早于 retraction 时间的正向信号折价(`retracted=true`、`signal_strength=min(现值,0.2)`);(b) 对**既有缓冲**中的匹配信号同样折价;(c) 登记 tombstone。API 层不再负责时序编排(codex r2 blocker 1 / r3 blocker 1:钩子放 ingest 前错过同批,放 ingest 后可能已被阈值消费——只有入口原子化才闭环)。必须有 threshold-ready 同批测试:批内正向信号数恰好触发阈值消费且含同批 retraction 时,消费发生前折价已生效。
- **面 1b(乱序 tombstone,r4 带 action 与因果时间)**:tombstone 结构为 `(identity_key, action) → retraction 事件时间戳`(TTL 24h、容量上限 500,超限逐出最旧——校准注释:24h 覆盖扩展缓冲补发与 account_sync 双周期,500 远超个人真实撤销频率)。`ingest_batch` 入场时,正向信号命中同 `(key, action)` tombstone **且其事件时间早于 tombstone 时间戳**才折价——撤销之后的重新点赞(`like→retract→like` 的第二个 like)不折;事件时间缺失时保守不折(codex r3 finding 2)。必须测试:同 key 跨动作(like tombstone 不折 favorite)、`like→retract→like` 序列。
- **面 2(DB,离线重读路径;r5 与内存面因果对齐)**:`Database.mark_positive_events_retracted(identity_urls: list[str], retracted_action: str, *, retraction_at: datetime) -> int` —— 单事务 JSON patch(`metadata.retracted=true`、`metadata.signal_strength=min(现值,0.2)`),**只标记事件时间早于 `retraction_at` 的行;时间不可得的行保守不标**(与面 1b 同一因果规则,codex r4 finding 1)。覆盖 `openbiliclaw init` 全量重建、12h 认知/画像整理等重读 events 表的路径。**无时间窗口(r3)**:identity key(tweet_id/bvid/note_id/mid)全局唯一,不存在"重名 url 误伤";撤销数月前的 like 正是应标注场景。不删行、不改 event_type/url(不变量 3)。
- **面 2b(迟到正向事件对账,r5 新增)**:retraction 已入库后才补到的旧正向事件(典型:account_sync 回填数月前的 like,事件时间早于 retraction)必须在**落库路径**上被标注——retraction 事件本身持久化在 events 表,即持久 tombstone。服务端批量落库入口(`/api/events` 与 account_sync 的事件写入)对白名单正向事件按 identity key 查询已存 retraction(`feedback_type="retraction"` 行,低频、一批一查),命中且事件时间早于 retraction 时间则带 `retracted` 标注入库;时间不可得保守不标。事件时间晚于 retraction 的(重新点赞)正常入库,与面 1b 语义一致。
- **消费面覆盖(r3 扩展,r2 finding 5)**:"(已撤销)"渲染标记与折后强度必须覆盖**所有重读 events 的 LLM 消费面**——至少含 preference 渲染与 12h 认知循环(`cognition_cycle.py:247` 重读 events 的 awareness 输入);实现时 grep events 重读点形成清单并逐一确认(共用渲染函数则自动生效,不共用则各自补标记),清单写入 PR。
- **明确不覆盖**:已被分析批消费并固化进 profile 层的旧证据(out of scope,历史不回溯)。spec 承诺:"从 retraction 到达起,该证据不再以满强度进入任何后续重读型 LLM 消费;缓冲中未消费的信号被折价"。

**identity key(r2 扩展)**:复用并扩展 PR #85 `_dedup_key` 的归一化(tweet_id / bvid / mid),**新增 xhs note_id**(24-hex,来自 `xiaohongshu.com/(explore|discovery/item|search_result)/<id>`)——否则 Wave C 的 xhs 撤销无法折价对应正向事件。归一化函数从 `runtime/account_sync.py:41-87` 提升到共享模块(如 `sources/identity_keys.py`),account_sync 原地引用。

**API 层钩子(r4 职责收窄)**:`/api/events` 接收路径只负责面 2(DB 标注)——对批内每条 `feedback_type=="retraction"` 事件校验 `retracted_action` ∈ 白名单(不变量 4,越界跳过 + WARNING)后调用 `mark_positive_events_retracted`;面 1/1b 的内存折价完全由 `ingest_batch` 原子阶段自治(白名单校验在 pipeline 侧同样执行)。同步调用(低频),异常 WARNING 不阻断接收。

**偏好层消费**:事件渲染时 `metadata.retracted` 为真的事件追加"(已撤销)"标记(折后 0.2 经既有 preserved keys 自然生效);system prompt 常量追加一句静态撤销语义规则(紧跟 `llm/prompts.py:252`)。**回放不变性作用域 = 事件渲染文本**(不变量 2):system prompt 的版本性变更不违门。

**测试**:缓冲折价(同批/先后到达/已消费不触碰)、DB 标注(命中/零命中/跨类型不误伤/幂等/白名单拒绝)、identity key 四键型(tweet/bvid/mid/note)、渲染标记、渲染回放不变性、prompt 调用不变性(既有测试)。

**验收门**:`pytest tests/test_event_retraction_discount.py tests/test_llm_prompts.py tests/test_pipeline_advanced.py -q` 全绿;含 retraction 用例断言缓冲信号与 DB 行双面折价、渲染含"(已撤销)"。

### Phase 1 — xhs 强信号 tap 化

**kernel 抑制契约先行(r3 动作粒度)**:adapter 新增 `tapAuthoritativeActions: Set<ActionType>` 声明,kernel 在 DOM 动作发射前(正向路径 `kernel.ts:321-327` 与撤销路径 301-306 统一)检查该集合,命中即抑制。X 声明 `{like, favorite, share, comment, retraction}`(其 tap 已发全部五类——`x-graphql-tap.ts:76` CreateRetweet 即 share;DOM 侧 `twitter.ts:84-85` 的 share/reply 点击当前会双计且"打开菜单即记事件",本契约一并消除);xhs 声明 `{like, favorite, retraction}`;bilibili 在 reply/add tap 上线后声明 `{comment}`。既有 `strongSignalSource` 字段语义收敛进新契约(保留或替换由实现决定,带迁移测试)。实现前核对 X 现状正向 DOM 发射行为并写入 PR(不变量 6/7)。

**xhs action tap**:新 MAIN-world tap `extension/src/main/xhs-action-tap.ts`(独立文件、postMessage source `obc-xhs-action`,与 token sniffer 互不串扰):监听 like/collect/uncollect/dislike 写端点调用,**HTTP 2xx 且响应业务成功字段通过(以真实抓包 fixture 固化判定,不变量 7b)**才发,解析 note_id,产出 `{type: like|favorite|retraction, note_id, retracted_action?}`。端点清单以真实抓包 fixture 固化;未知端点忽略。消息桥在 `extension/src/content/xiaohongshu.ts` 构造事件(url 由 note_id 拼 explore 链接)。

**构建完整性(不变量 9)**:tap 入口登记 `extension/scripts/build.mjs:13` entrypoints + Chrome/Firefox 双 manifest;`npm run build` 后断言产物存在。

**测试**:tap 四端点/坏 payload/非 2xx/未知端点;kernel×xhs 集成(DOM like/favorite/retraction 零发射、comment/share 仍走 DOM);X 全量回归;新增 `xiaohongshu-adapter.test.ts`。

**验收门**:`cd extension && npm test && npm run typecheck && npm run build`(产物含 xhs-action-tap);真实端到端:本机登录 xhs 点赞/取消各一次,`/api/events` 收到 like 与 retraction 各一条且无 DOM 双发(记录在 PR)。

**回滚契约(r3,codex r2 finding 7)**:xhs tap 化(tap + 消息桥 + manifest/build 登记 + adapter 声明)必须作为**单个原子提交**交付,PR 记录哈希;线上双发或漏采时回滚 = `git revert <该提交>`,tap 与 DOM authority 同步回退,禁止只回退 adapter 声明而留 tap 运行的半吊子状态。bili-interact-tap 同规则。

### Phase 2 — 评论正文采集(网络层,提交成功后)

**契约先行**:`sources/event_format.py` 定义 `metadata.comment_text`(`_sanitize_comment_text`:截断 200 + 剥离 category-C)与 `metadata.comment_kind` 白名单(不变量 4/5);`preference_analyzer` preserved keys 增加 `comment_text`/`comment_kind`,渲染「评论:『…』」。扩展侧新增共享净化工具(`extension/src/shared/text-sanitize.ts`:同规格截断 + category-C 剥离),X tap 与 bili tap 共用,**两端都有边界测试**(不变量 5)。

**采集两路(均为提交成功后,业务码校验按不变量 7b)**:
- X tap:`parseXMutation` CreateTweet 分支提取 `variables.tweet_text` → `XEngagement.text?: string`(经共享净化)→ `buildEventFromEngagement` 写 `comment_text`;**正文路径校验响应 GraphQL 无 `errors` 才附带正文**(X tap 既有动作发射时序不动,不变量 7b)。既有字段零回归。
- B站:`bili-interact-tap`(见 Phase 3,同一文件)监听 `POST /x/v2/reply/add`,**HTTP 2xx 且响应 `code===0`** 才发,提取评论正文 + 视频标识 → comment 事件(`comment_kind="comment"`、`comment_text`)。DOM 侧:bilibili adapter 声明 `tapAuthoritativeActions: {comment}`,点击"评论"按钮不再发 comment 事件(消除打开评论区即记事件 + 提交双计,codex r2 finding 2)。
- 其余平台:显式 out-of-scope。

**验收门**:`cd extension && npm test`、`pytest tests/test_event_format.py tests/test_preference_analyzer.py -q`;X tap 既有用例零回归。

### Phase 3 — B站弹幕采集(与 Phase 2 共享 bili-interact-tap)

新 MAIN-world tap `extension/src/main/bili-interact-tap.ts`(document_start、MAIN world、匹配 bilibili.com 播放页,登记 build.mjs + 双 manifest):
- `POST */x/v2/dm/post` HTTP 2xx 且 `code===0` → `{kind:"danmaku", text, oid/bvid}` → comment 事件(`comment_kind="danmaku"`、`comment_text` 经净化、`signal_strength=0.6`——低于 comment 0.75 因弹幕更随意,持平 follow 0.6,校准注释按不变量 8)。
- `POST */x/v2/reply/add` HTTP 2xx 且 `code===0` → Phase 2 的 comment 事件。
- 服务端:`default_signal_strength_for_event` 对 `comment_kind=="danmaku"` 返回 0.6(metadata 剥离时兜底,对齐 `event_format.py:301-302` 先例)。
- 错误行为:解析失败静默丢弃该条;tap 不改写任何请求。

**验收门**:tap 单测(dm/reply 正常、坏 JSON、非 2xx、缺字段);`npm run build` 产物含 bili-interact-tap;真实端到端:本机发一条弹幕 + 一条评论,events 表出现对应两行(记录在 PR)。

### Phase 4 — xhs 维护性收尾

- selector 统一:passive.ts / bootstrap.ts 重复 selector 提到 `extension/src/content/xhs/selectors.ts`,**纯移动、行为零变化**。
- 降级契约固化(r2 修订,不改行为):以回归测试**固化现有降级契约**——selector 全 miss 时 `extractNoteMetadata` 对空 title 返回 `null`(`passive.ts:182`,刻意防空卡/浪费 LLM 的旧故障),部分字段缺失时返回部分数据不抛异常。**不引入**"全 miss 也返回部分数据"的行为变化。
- ingest 对齐:`api/app.py:8186-8190` 的 **`urls` 裸链分支**接受 `/discovery/item/` 变体(归一化复用既有 note url normalize);`notes` 分支不动。

**验收门**:`npm test` + `pytest tests/test_api_xhs_ingest.py -q`;discovery/item 裸链用例由拒收转接受;selector 定义处数 2 → 1。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0 | 被撤销正向证据的后续 LLM 消费强度 0.85–1.0 → ≤0.2(缓冲面 + 重读面),渲染显式标注;无 retraction 渲染字节零差异 |
| Phase 1 | xhs like/favorite 从"文案匹配、图标漏采"变为网络层确定性认定;kernel 抑制契约统一覆盖正向+撤销;xhs adapter 测试 0 → 全覆盖 |
| Phase 2 | 用户评论正文(X + B站,提交成功后)首次进入画像证据链 |
| Phase 3 | B站弹幕 0 → 全量采集(文本 + 0.6 强度) |
| Phase 4 | xhs selector 单一来源;`urls` 分支 `/discovery/item/` 停止静默丢弃;降级契约有回归测试 |

## Documentation obligations

- `docs/modules/extension.md` — 两个新 MAIN-world tap(xhs-action、bili-interact)、comment_text 采集、xhs strongSignalSource、kernel 抑制契约变更。
- `docs/modules/soul.md` — retraction 双面折价机制与回放不变性。
- `docs/modules/storage.md` — `mark_positive_events_retracted` 公开 API。
- `docs/architecture.md` + `docs/spec.md` §3 图 + **`README.md` / `README_EN.md` 顶部架构图(无条件,CLAUDE.md 强制)** — 扩展侧数据流新增两个 tap 节点。
- **`docs/privacy.md` + `docs/chrome-webstore-listing.md`(r4:仓库既有商店 listing 文档,一并更新)+ 商店后台隐私披露表单操作项记录进 PR** — 「个人通讯」采集范围扩展到用户提交成功的评论/弹幕正文(仅送本机后端);r1 finding 10 / r3 finding 4。
- `docs/changelog.md` — 当前版本块新增条目。
- CLI / config 无变化,不触发对应文档。
