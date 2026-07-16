# 事件采集补全 Spec — retraction 画像折价、评论/弹幕正文、xhs 强信号脱离 DOM 裸奔

**Created:** 2026-07-16
**Scope:** 画像偏好层(`soul/`)、事件契约(`sources/event_format.py`)、存储(`storage/database.py`)、浏览器扩展采集(`extension/src/main/`、`extension/src/content/`、`extension/src/shared/platforms/`)、xhs 后端 ingest(`api/app.py`)。
**Out of scope:** 评论/弹幕之外的 UGC 正文(转发语、简介编辑);xhs 之外平台的 tap 化;画像层对 retraction 的历史回溯性重算(只影响未来分析批);移动 Web / CLI 新增可视面(本 spec 全部为采集与画像内部改动,四端无新 UI,依 CLAUDE.md 第 5 条在此显式声明排除)。

## Goal

2026-07-05 事件获取体检遗留四个缺口(PR #85 合并时明确 out-of-scope),导致画像输入失真或缺失:

1. **撤销的赞永久留在画像里**:retraction 事件已采集,但对已入库的正向事件(like/favorite/share)零折价——用户明确"反悔"的兴趣证据继续以满强度(0.85/1.0)参与偏好分析。
2. **评论正文零采集**:7 平台的 comment 事件只记录"点了评论按钮",用户亲手写的文字——最强的兴趣表达之一——全部丢弃。
3. **弹幕零采集**:B站用户最高频的表达行为完全不存在于事件流。
4. **xhs 强信号裸奔**:like/favorite 靠按钮文案匹配(图标按钮直接漏采),aria-pressed 撤销"名义覆盖、实际未验证",且是唯一没有 adapter 单测的平台。

目标结果与验证命令:

- retraction 到达后,同 identity key 的先前正向事件被确定性标记 `retracted`,进入偏好分析时强度折至 ≤0.2;无 retraction 时偏好分析输入与现状**字节一致**。验证:`pytest tests/test_event_retraction_discount.py tests/test_preference_analyzer.py -q`。
- X 回复与 B站评论框captured 正文进入 `metadata.comment_text`(≤200 字符、控制字符剥离);B站弹幕以 `comment_kind="danmaku"` 事件入库,强度 0.6。验证:`cd extension && npm test`、`pytest tests/test_event_format.py -q`。
- xhs like/collect(及撤销)改由 MAIN-world 网络 tap 认定(`strongSignalSource:"tap"`),DOM 文本匹配路径被抑制;新增 `xiaohongshu-adapter.test.ts` 补齐唯一缺失的平台 adapter 测试。验证:`cd extension && npm test && npm run typecheck`。

## Design invariants (MUST hold in every phase)

1. **Prompt-cache 静态性**:任何 prompt 指引改动只落在模块级常量 system prompt 或 user prompt 的稳定段;`tests/test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant` 必须持续通过(CLAUDE.md LLM Prompt-Cache Convention)。
2. **无 retraction 即无差异(回放不变性)**:对不含 retraction 的事件集,偏好分析构建出的 prompt 与改动前**字节一致**。验证面:`tests/test_event_retraction_discount.py::test_replay_invariance_without_retractions`(质量铁律:改模型输入必须证明未波及无关路径)。
3. **只标注不删除**:retraction 折价通过 metadata 标注(`retracted=true` + 折后强度)实现,events 表任何行都不被删除或改写 event_type/url,审计链完整。验证面:DB 层测试断言行数与非 metadata 列不变。
4. **枚举白名单不扩散**:不新增顶层 event_type;弹幕复用 `event_type="comment"` + `metadata.comment_kind="danmaku"`。服务端对 `comment_kind` 白名单校验(`{"", "comment", "danmaku"}`),越界值按缺失处理并 WARNING(CLAUDE.md pitfall #4)。
5. **正文入库前净化**:`comment_text` 在扩展侧与服务端各截断一次(≤200 字符)、剥离 Unicode category-C 控制字符;服务端为最终防线。验证面:`tests/test_event_format.py` 边界用例。
6. **tap 失败静默降级**:xhs 强信号 tap、bili 弹幕 tap 的任何解析异常不得抛出到页面、不得吞掉既有非正文事件;X tap 现有行为(engagement 类型、tweet_id 提取)不因正文提取改动而回归。验证面:tap 单测异常路径 + 既有 `x-graphql-tap.test.ts` 全绿。
7. **tap 权威即 DOM 抑制**:xhs 设置 `strongSignalSource:"tap"` 后,DOM 文本匹配的 like/favorite(含 retraction)必须被 kernel 既有抑制逻辑(`kernel.ts:301-306` 同款契约)压制,杜绝双发。验证面:kernel×xhs 集成测试。
8. **阈值有出处**:弹幕强度 0.6、折后强度 0.2、正文截断 200 等常量必须带校准注释(与 `_DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE` 现值的相对关系),provider/模型更换后重开校准(CLAUDE.md pitfall #3)。

## Current diagnosis

### D1. retraction 已采集但画像侧无折价机制

- 产生点:`extension/src/main/x-graphql-tap.ts:82-84,218-227`(X tap 权威)、`extension/src/content/kernel.ts:301-318`(DOM aria-pressed),payload 均为 `{type:"feedback", metadata:{feedback_type:"retraction", retracted_action, signal_strength:0.2}}`。
- 反馈批学习**显式排除** retraction:`soul/engine.py:974-976`(`_is_retraction_feedback` 过滤,`engine.py:1592-1597`)。
- 增量管线**不排除**:`soul/pipeline.py:382-403` 把 retraction 强制走 `SignalType.BEHAVIOR_EVENT`(防它冒充强信号),但仍进 INTEREST 层 → `soul/layer_updaters.py:149-176` → `preference_analyzer.analyze_events`;satisfaction=neutral(`sources/event_format.py:144-145`)通过过滤(`preference_analyzer.py:220-237` 保留 neutral)。
- `signal_strength=0.2` 只被 LLM prompt 第 11 条自然语言指引消费(`llm/prompts.py:252`),无确定性权重计算。
- **对被撤销的那次正向事件零处理**:grep `retraction` 在 `interest_writeback.py`、`negative_exemplars.py`、`dislike_writeback.py`、`prompts.py` 全空;没有任何代码用 `retracted_action` 关联回原事件。原 like 若已进当前分析批之前的画像层,完全不受影响;若与 retraction 同批,只能指望 LLM 黑盒自行权衡。
- 既有测试:`tests/test_event_satisfaction.py:49-66`、`tests/test_event_format.py:415-429`、`tests/test_pipeline_advanced.py:249-258`、`tests/test_soul_engine.py:602-690`。**盲区**:无"撤销后正向权重折价"测试(机制不存在)。

### D2. 评论正文全链路缺失

- 7 平台 comment 事件都是点击动作:kernel `observeClicks`(`extension/src/content/kernel.ts:277-329`)→ `buildActionHintFromClickTarget`(`extension/src/shared/behavior.ts:53-65`,只取元素 textContent/aria/class)→ 各平台 `inferActionType` 关键词匹配(bilibili.ts:58、xiaohongshu.ts:52、douyin.ts:56、zhihu.ts:59、youtube.ts:61、reddit.ts:64、twitter.ts:85)。metadata 只有 `targetText`(按钮文案)/`href`/`actionLabel`。
- X tap 对 `CreateTweet` **刻意丢弃 `tweet_text`**:`extension/src/main/x-graphql-tap.ts:229-238` 只取 `in_reply_to_tweet_id`;`XEngagement` 类型无 text 字段。
- 后端事件契约(`sources/event_format.py:36-50`)metadata 无正文字段约定;`body_text` 已被"发现内容候选的正文"语义占用(`discovery/x_normalize.py:135-136`、`discovery/candidate_pool.py:46`、`llm/prompts.py:1140`),**不可复用**,需新名 `comment_text`。
- comment 权重已存在:`event_format.py:266`(0.75),属显式正向(`event_format.py:85`)。
- 画像序列化器 `build_profile_summary`(`discovery/strategies/_utils.py:442-511`)不含原始事件正文;正文要影响画像必须经偏好分析事件渲染进入(`preference_analyzer.py:72-74` preserved metadata keys)。

### D3. 弹幕零采集

- 全仓 `danmaku` 只是展示计数:`bilibili/api.py:94,562`(读 stat)、`extension/popup/popup.js:2877`(展示)。扩展 content script 无任何弹幕输入/发射监听(grep 无命中)。
- B站播放页采集落点:`extension/src/content/bilibili.ts:1-14`(仅 startCollector + message listener),逻辑全在 kernel(video 监听 `kernel.ts:333-390`)。
- 弹幕发送走 XHR `POST /x/v2/dm/post`(B站 web 播放器;实现时以真实抓包 fixture 确认字段名,预期 `msg`、`oid`、`bvid/aid`)。DOM 输入框方案(读 `.bpx-player-dm-input`)受播放器版本影响大;MAIN-world 网络 tap 模式(仿 `x-graphql-tap.ts`)是既有验证过的稳路径。

### D4. xhs 强信号裸奔 + 测试债 + ingest 不一致

- 强信号纯靠文案匹配:`extension/src/shared/platforms/xiaohongshu.ts:41-55`(`点赞/like`、`收藏/collect`……),图标按钮无文案 → 漏采。源码自认:`xiaohongshu.ts:3-5`("Like/collect/comment DOM is unstable on xhs and left out of this phase")、`extension/src/content/xiaohongshu.ts:5-6`、`extension/src/content/xhs/passive.ts:158-159,179-182`。
- **矛盾点**:注释称 deliberately skipped,但 `inferActionType` 实际仍返回 like/favorite → kernel 照发强信号。"跳过"只是没做可靠 selector,信号仍在发、质量未知。
- aria-pressed 撤销对 xhs"名义覆盖":机制在 kernel(`kernel.ts:299-319`),xhs 未设 `strongSignalSource`,理论走 DOM retraction 分支,但无任何测试/fixture 证明 xhs 真实 DOM 带 `aria-pressed`。PR #85 对 xhs 仅加了 search 提取 + dwellPageTypes(3 行)。
- xhs 已有 MAIN-world fetch/XHR tap 基建:`extension/src/main/xhs-token-sniffer.ts:1-196`(包裹 fetch/XHR,`isXhsApiUrl` = `/api/sns/web/` 或 `edith.xiaohongshu.com`),但只抓 `(note_id, xsec_token)`,不认定行为。like/collect 的写端点(预期 `/api/sns/web/v1/note/like`、`/note/collect`、撤销为 `/note/dislike`、`/note/uncollect`;实现时以真实抓包 fixture 确认)与 token 流量同源,可复用同一 tap 管道。
- 测试债:xhs 是**唯一**没有 adapter 测试的平台(twitter/douyin/reddit/youtube/zhihu 都有);`inferActionType`/`detectPageType`/`extractNoteId`/selector 零覆盖;passive 元数据只测命中、未测改版全 miss 的降级。
- ingest 不一致:采集端接受 `/discovery/item/` URL(`xiaohongshu.ts:13-19`),后端 `POST /api/sources/xhs/observed-urls` 只收 `/explore/`(`api/app.py:8186-8190`),变体被静默丢弃。
- selector 重复:passive.ts 与 bootstrap.ts 各自复制 title/author/cover selector(`passive.ts:174-191` vs `bootstrap.ts:822-830`),双处维护。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | retraction 确定性折价(DB 标注 + 偏好层消费 + 回放不变性) | **MUST** | 画像正确性:用户明确反悔的证据仍满强度参与分析 |
| 1 | xhs 强信号 tap 化 + DOM 抑制 + adapter 测试 | **MUST** | 当前在发未知质量的强信号(1.0/0.85 权重),数据质量风险最大 |
| 2 | 评论正文采集(X tap + B站评论框 + 通用兜底) | RECOMMENDED | 最强兴趣表达,新增能力 |
| 3 | B站弹幕 tap 采集 | RECOMMENDED | 高频表达行为,从零搭建 |
| 4 | xhs selector 统一 + ingest 对齐 + passive 降级测试 | RECOMMENDED | 维护性债务,随 Phase 1 顺手清 |

依赖:Phase 2/3 依赖 Phase 0 落地的 `comment_text` 服务端净化契约先行(Phase 0 只做 retraction,契约在 Phase 2 首个任务定义);Phase 1/4 相互独立。

**Wave A(可独立交付)**:Phase 0。**Wave B**:Phase 2 + 3(共享 `comment_text` 契约与净化)。**Wave C**:Phase 1 + 4(xhs 域)。任一 Wave 完成即可安全停下发布。

## Phase designs

### Phase 0 — retraction 确定性折价

**接口**:
- `Database.mark_positive_events_retracted(identity_urls: list[str], retracted_action: str, *, within_hours: int = 24 * 30) -> int`:按 url 匹配(复用 PR #85 的 identity key 提取思路:tweet_id/bvid/mid 归一化后比对)窗口内 `event_type == retracted_action` 的行,把 `metadata.retracted = true`、`metadata.signal_strength = min(现值, 0.2)` 写回(JSON patch,单事务),返回标记行数。不删行、不改 event_type/url(不变量 3)。
- ingest 钩子:`/api/events` 接收路径(`api/app.py:4407` propagate 之后)对 `feedback_type=="retraction"` 事件调用上述方法;`retracted_action` 缺失时跳过并 WARNING。
- 偏好层消费:`preference_analyzer` 事件渲染时,`metadata.retracted` 为真的事件在文本中追加"(已撤销)"标记;preserved metadata keys 已含 `signal_strength`(`preference_analyzer.py:72-74`),折后 0.2 自然生效。prompt 静态指引:在既有 system prompt 常量的 signal_strength 条款(`llm/prompts.py:252`)后追加一句静态规则——"标记为已撤销的正向行为应视为兴趣证据已被用户主动中和"。
- 折价窗口 30 天:retraction 携带的 identity key 只在该窗口内回溯,防止误伤重名 url 的远古事件;窗口值随首次真实数据校准(不变量 8 注释)。

**错误行为**:DB 匹配零行是正常路径(原 like 可能从未入库);匹配异常 WARNING 不阻断事件接收。

**测试**:DB 标注(命中/零命中/跨类型不误伤/幂等)、ingest 钩子(X tweet_id 与 bili bvid 两键型)、渲染标记、回放不变性(不变量 2)、prompt 静态性(既有 invariance 测试自动覆盖)。

**验收门**:`test_replay_invariance_without_retractions` 断言无 retraction 事件集的偏好 prompt 字节一致;含 retraction 用例断言被撤销事件渲染含"(已撤销)"且强度 0.2。命令:`pytest tests/test_event_retraction_discount.py tests/test_llm_prompts.py -q`。

### Phase 1 — xhs 强信号 tap 化

**接口**:
- 新 MAIN-world tap `extension/src/main/xhs-action-tap.ts`(与 token sniffer 同页注入,独立文件、独立 postMessage source `obc-xhs-action`):监听 fetch/XHR 对 like/collect/uncollect/dislike 写端点的**成功**调用(2xx),解析 note_id,产出 `{type: like|favorite|retraction, note_id, retracted_action?}`。端点清单以真实抓包 fixture 固化进测试;未知端点一律忽略(不变量 6)。
- `extension/src/content/xiaohongshu.ts` 增加 action-tap 消息桥,复用 X 的 `buildEventFromEngagement` 模式构造事件(url 由 note_id 拼 explore 链接)。
- adapter 设 `strongSignalSource: "tap"`(`extension/src/shared/platforms/xiaohongshu.ts`),kernel 既有抑制逻辑自动压制 DOM like/favorite 与 DOM retraction(不变量 7);`inferActionType` 保留 comment/share(tap 不覆盖的动作)。
- 新增 `extension/tests/xiaohongshu-adapter.test.ts`(detectPageType/extractNoteId/inferActionType/selector)与 `xhs-action-tap.test.ts`(端点解析、异常降级、非 2xx 忽略)。

**错误行为**:tap 解析异常静默(console.debug),不影响 token sniffer 与页面;消息桥校验 payload 结构,坏消息丢弃。

**验收门**:kernel×xhs 集成测试证明 tap 权威时 DOM 强信号零发射;`npm test && npm run typecheck` 全绿。真实端到端:开发者本机登录 xhs 点赞/取消一次,`/api/events` 收到 like 与 retraction 各一条(记录在 PR)。

### Phase 2 — 评论正文采集

**契约先行**:`sources/event_format.py` 定义 `metadata.comment_text`(≤200 字符,服务端 `_sanitize_comment_text` 截断 + 剥离 category-C)与 `metadata.comment_kind` 白名单 `{"", "comment", "danmaku"}`(不变量 4/5);`preference_analyzer` preserved keys 增加 `comment_text`/`comment_kind`,渲染时正文以「评论:『…』」并入事件行。

**采集三路**:
- X tap:`parseXMutation` 的 CreateTweet 分支提取 `variables.tweet_text`(截断 200),`XEngagement` 增可选 `text` 字段,`buildEventFromEngagement` 写入 `comment_text`。
- B站评论框:kernel comment 动作触发时,经 adapter 新可选钩子 `extractCommentDraft(target: Element): string`(bilibili adapter 实现:从点击目标向上找评论容器内 textarea/contenteditable 的 value/textContent),拿不到返回空串——事件照发、只是无正文(不变量 6 同款降级)。
- 其余平台:不实现 `extractCommentDraft` 即维持现状,显式 out-of-scope。

**验收门**:X tap 测试断言 reply 带正文且既有字段不回归;kernel 测试断言钩子缺失/抛异常时 comment 事件仍发;服务端净化边界用例(201 字符、控制字符、None)。命令:`cd extension && npm test`、`pytest tests/test_event_format.py -q`。

### Phase 3 — B站弹幕采集

**接口**:新 MAIN-world tap `extension/src/main/bili-dm-tap.ts`(仿 x-graphql-tap 注册进 manifest,document_start,MAIN world,匹配 bilibili.com 播放页):监听 `POST */x/v2/dm/post` 成功响应,提取弹幕文本与视频标识,postMessage `obc-bili-dm` → `extension/src/content/bilibili.ts` 桥接为事件:`event_type="comment"`、`metadata.comment_kind="danmaku"`、`metadata.comment_text=<文本截断 200>`、`metadata.signal_strength=0.6`(低于 comment 0.75——弹幕更随意;高于 follow 0.6 持平;校准注释按不变量 8)。
- 服务端:`default_signal_strength_for_event` 对 `comment_kind=="danmaku"` 返回 0.6(metadata 被剥离时的兜底,对齐 `event_format.py:301-302` retraction 先例)。

**错误行为**:请求体解析失败静默丢弃该条(不发半成品事件);tap 不改写任何请求。

**验收门**:tap 单测(正常/坏 JSON/非 2xx);`event_format` 弹幕强度与 satisfaction(positive,随 comment)测试;真实端到端:本机发一条弹幕,events 表出现 comment_kind=danmaku 行(记录在 PR)。

### Phase 4 — xhs 维护性收尾

- selector 统一:passive.ts 与 bootstrap.ts 重复的 title/author/cover selector 提到 `extension/src/content/xhs/selectors.ts` 单一来源。
- ingest 对齐:`api/app.py:8186-8190` 接受 `/discovery/item/` 变体(归一化到 explore 形式复用既有 `normalize` 函数)。
- passive 降级测试:selector 全 miss 时 `extractNoteMetadata` 返回部分数据不抛异常。

**验收门**:`npm test` + `pytest tests/test_api_xhs_ingest.py -q`;discovery/item URL 的 ingest 用例由拒收转接受。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0 | 被撤销正向事件进入偏好分析的有效强度 0.85–1.0 → ≤0.2,且渲染显式标注;无 retraction 路径字节零差异 |
| Phase 1 | xhs like/favorite 从"文案匹配、图标漏采、质量未知"变为网络层确定性认定;xhs adapter 测试覆盖从 0 → 与其余 6 平台对齐 |
| Phase 2 | 用户评论正文(X + B站)首次进入画像证据链,comment 事件信息量从按钮文案 → 真实表达 |
| Phase 3 | B站弹幕行为从 0 → 全量采集(文本 + 0.6 强度) |
| Phase 4 | xhs selector 单一来源;`/discovery/item/` 观察 URL 停止静默丢弃 |

## Documentation obligations

- `docs/modules/extension.md` — 新增两个 MAIN-world tap(xhs-action、bili-dm)、comment_text 采集、xhs strongSignalSource 变更(实现表 + 公开 API)。
- `docs/modules/soul.md`(或 memory/storage 对应文档)— retraction 折价机制与回放不变性。
- `docs/modules/storage.md` — `mark_positive_events_retracted` 公开 API。
- `docs/architecture.md` + `docs/spec.md` §3 图 — 扩展侧数据流新增 bili-dm / xhs-action tap 节点(README 图若含 tap 粒度则同步,否则声明不触发)。
- `docs/changelog.md` — 当前版本块新增条目。
- CLI / config 无变化,不触发对应文档。
