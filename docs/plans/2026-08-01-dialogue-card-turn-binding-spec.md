# 对话卡片入流与 Turn 级上下文绑定 Spec

**Created:** 2026-08-01

**Status:** Ready for Luna Max implementation; awaiting Codex acceptance

**Implementation owner:** Luna Max

**Acceptance owner:** Codex

**Depends on:**
[`2026-07-22-dialogue-confirmation-entry-spec.md`](./2026-07-22-dialogue-confirmation-entry-spec.md)、
[`2026-07-23-dialogue-settlement-queue-spec.md`](./2026-07-23-dialogue-settlement-queue-spec.md)

**Surfaces:** extension popup、desktop web、mobile web；CLI 仅保留兼容，不新增卡片选择 UI

## 0. 决策摘要

采用“两者结合”，不是在“卡片进聊天流”和“关系绑定”之间二选一：

1. 卡片/疑惑问题继续作为一等 `chat_turn` 留在统一聊天流里，负责展示、恢复和审计；
2. 用户消息通过 `reply_to_turn_id` 显式指向卡片/问题 turn；
3. 服务端在**用户消息首次落库时**解析并冻结可信上下文；
4. 可见回复、原始 dialogue event、记忆提取和卡片结算只能消费同一份冻结快照；
5. 全局 dialogue anchor 只负责生命周期、代次校验和串行结算，不再负责事后猜测“这句话属于哪张卡”。

```text
Card / Question Turn A
        │
        └── User Turn U
              reply_to_turn_id = A
              dialogue_binding.context_digest = D
                    │
                    ├── interactive reply prompt ── D
                    ├── durable history replay ─── D
                    ├── raw dialogue event ─────── D
                    ├── memory extraction ─────── D
                    └── anchor settlement ─────── D
```

任何节点若改为读取“当前 anchor/current card”，即违反本 spec。

## 1. 已确认的问题

### 1.1 当前请求没有携带卡片身份

popup、desktop web、mobile web 的普通聊天提交都只发送 `message/session/scope`；
`ChatTurnIn` 没有 reply relation，普通 `chat_turns.payload` 也是空对象。用户点击
“聊聊”后，输入框看起来在继续这张卡，但 durable user turn 本身并不知道它回复了谁。

### 1.2 绑定发生得太晚

当前链路先执行 interactive LLM reply，`SocraticDialogue.respond()` 返回后才提交
`LEARN`。结算队列把这一刻称为 learn admission，并在这里读取 queue-global logical
anchor。它不是用户按下发送、也不是 user turn 落库的时刻。

已确定性复现如下：

```text
1. 卡片 A 正在讨论
2. 用户发送一条本意回复 A 的消息，LLM 被 barrier 阻塞
3. 另一端点击卡片 B，B 替换 A 成为当前 anchor
4. 放行 LLM
5. LEARN 在回复完成后才 admission，捕获到 B

expected_ref = A
captured_ref = B
misaligned  = true
```

现有 generation CAS 只能防止“捕获 A 后 A 已过期”，不能防止“一开始就晚捕获成 B”。

### 1.3 回复生成也缺少显式卡片上下文

interactive reply 主要依赖一次性 hydrate 的共享 `_history`：其中可能包含多张卡片标题，
但没有声明本轮正在回复哪张卡。hydrate 完成后新打开的卡片也可能尚未进入内存 history。
因此即使没有并发切换，“是的”“这个不对”之类短输入也可能得到泛化回复或对应错卡。

### 1.4 event 与记忆无法还原本轮所指

原始 `dialogue` event 目前只有 user/assistant/session 等通用字段，没有 user turn、
reply target、anchor kind/ref/generation 或卡片标题。后续认知周期看到“对”“不是这个意思”
时，无法知道它在谈什么。

## 2. 目标与非目标

### 2.1 Goals

- 从 UI 到 event/记忆/结算建立一条可审计的 turn relation；
- A→B 任意交错都只能得到“仍属于 A”或“因 A 已过期而安全丢弃”，绝不能绑定 B；
- 卡片仍自然地存在于聊天流，不在输入框上方复制完整卡片；
- 三个图形端共享行为语义、错误语义和滚动规则；
- 普通聊天、旧 turn、旧客户端、CLI/OpenClaw 的既有可用性不被破坏；
- 只有 ID 的证据不进入 UI、LLM prompt 或自然语言 event context。

### 2.2 Non-goals

- 不支持多 active anchor；
- 不把 in-memory settlement queue 改成 durable job system；
- 不解决多 Uvicorn writer/跨进程串行；仍遵循单 backend writer 限制；
- 不重做卡片视觉品牌、推荐探针或 legacy feedback endpoint；
- 不给 CLI 新增选择卡片/引用卡片的交互；
- 不在本任务拆分 settlement analyze/apply，也不更换 LLM provider；
- 不把原始 `evidence_refs` 全量复制进 event 或 prompt。

## 3. 规范数据模型

### 3.1 `chat_turns` relation

`chat_turns` 新增 additive 列：

```sql
reply_to_turn_id TEXT NOT NULL DEFAULT ''
```

- 空字符串表示没有 reply relation；
- 非空值必须指向一个已经 completed 的 card/question turn；
- 关系写入后不可修改；
- 建议增加 `reply_to_turn_id` 普通索引，供 history/UI 查询；
- 旧数据库迁移幂等，旧行统一读作空字符串。

`ChatTurnIn` 与 `ChatTurnOut` 同步增加 `reply_to_turn_id: str = ""`。关系必须是
top-level 字段，不能只藏在自由形态 JSON 中。

### 3.2 `DialogueTurnBinding`

所有新建的 durable chat user turn 在 `payload.dialogue_binding` 保存服务端生成的绑定：

```json
{
  "dialogue_binding": {
    "version": 1,
    "mode": "bound",
    "context_digest": "<sha256 canonical context>",
    "context": {
      "reply_to_turn_id": "confirmation-...",
      "source_type": "card",
      "kind": "hypothesis",
      "ref": "29c2b5ac",
      "generation": 7,
      "anchor_origin_turn_id": "confirmation-...",
      "title": "用户可能正在构建 AI 工具开发能力",
      "evidence_labels": ["最近连续收藏了 Agent 工程实践内容"],
      "captured_at": "2026-08-01T12:00:00+08:00"
    }
  }
}
```

`mode` 是闭集：

| mode | 条件 | 学习/结算语义 |
| --- | --- | --- |
| `bound` | 请求显式携带有效 `reply_to_turn_id` | 只允许处理冻结的 exact kind/ref/generation |
| `ordinary` | 未引用任何 turn，且 admission 时没有 logical anchor | 保持既有普通聊天与 inventory settles |
| `detached` | 未引用 turn，但 admission 时存在 persisted/reserved anchor | 可进行通用偏好提取；禁止 anchor relation 和 inventory object settles |

`detached` 是必要状态：用户清除上下文、另一端仍有 active anchor 时，消息不能被偷偷归给
该 anchor，也不能从普通 `settles` 旁路结算它。

### 3.3 Canonical context 与 digest

新增一个共享、typed、不可变的 Python value object（建议
`soul/dialogue_turn_context.py`），集中负责：

- mapping 的严格 parse/serialize；
- enum、positive generation、必填字段校验；
- canonical JSON 与完整 SHA-256 `context_digest`；
- prompt 可读片段和 event metadata 的投影；
- evidence label 过滤和长度预算。

digest 覆盖 `version`、`reply_to_turn_id`、`source_type`、`kind`、`ref`、`generation`、
`anchor_origin_turn_id`、`title`、`evidence_labels`，不覆盖 `captured_at`。所有下游日志/测试
使用完整 digest，不用 hash8。

### 3.4 Evidence 规则

- card/question turn 仍可保留原始 `evidence_refs`，用于内部链接与历史兼容；
- binding 只保存服务端筛出的 `evidence_labels`；
- 纯数字、hex/hash、UUID、BV/av/cv、`event:*`、`turn:*` 等 opaque ID 不得成为 label；
- 去重后最多 5 条，每条最多 240 个字符；该预算约束动态 prompt 增量，常量旁必须写明
  “最多约 1.2K 字符上下文”的校准出处；
- 过滤后为空时，三端不显示“依据（0）”，prompt/event 也不生成空依据段；
- Python 与共享 JS 使用同一组 contract fixtures，防止三端与后端规则漂移。

## 4. 服务端 canonicalization

### 4.1 客户端只声明 relation，不声明事实

客户端提交 `reply_to_turn_id`，不得被信任提交 `kind/ref/generation/title/evidence`。服务端
按 target row 解析：

| Target turn | 必要条件 | Canonical result |
| --- | --- | --- |
| hypothesis card | `status=completed`、`payload.type=card`、`kind=hypothesis`、`state=discussing` | `kind=hypothesis`、ref/title/evidence 来自 row |
| confusion question | `status=completed`、`payload.type=question`、`scope=confusion`、对象未终态 | `kind=confusion`、ref/title/evidence 来自 row |

scope/subject 同样由 target canonicalize：hypothesis card reply 的 durable user turn 写
`scope=chat`；confusion question reply 写 `scope=confusion`，并从 question row 写入
`subject_id/subject_title`。新客户端即使统一以 chat composer 发起，也不负责判断最终 scope；
若请求显式提交与 target 冲突的非默认 scope/subject，服务端返回 422，不能静默混用两套事实。

服务端再从 settlement queue 的 logical admission registry 读取 exact state：

- 必须是匹配 target `(kind, ref)` 的 `AnchorPersisted(generation > 0)`；
- `reserved` 返回可重试的 processing conflict，不等待、不降级；
- `absent/failed/foreign ref/terminal card` 返回 stale conflict；
- 同 ref 跨 session 投影允许绑定各自可见的 card turn；`reply_to_turn_id` 记录用户实际看见的
  turn，`anchor_origin_turn_id` 记录生命周期 owner，两者可以不同，但 kind/ref/generation
  必须一致。

绑定解析完成后，到 `create_chat_turn()` 提交之间不得出现 `await`。在受支持的单 event
loop/single writer 模式下，这使 capture + durable insert 成为同一 admission turn；即使
insert 后立刻发生 replacement，存下来的仍是 A，只会在学习时判 stale，不会升级为 B。

### 4.2 系统卡片附着顺序

- 有 `reply_to_turn_id` 的请求不得再触发 `_maybe_attach_system_confirmation()`；否则系统可能
  在 A reply 前插入并建立 B；
- 无 reply relation 的请求可保持既有“先附着卡片、再落 user turn”顺序；绑定 mode 在附着
  结束后捕获。只要本次新附着了 confirmation，本轮就必须是 `detached`（无论它是否立即建锚），
  因为用户输入发生在看到新卡之前；没有新附着时再按 logical anchor 是否存在区分
  `ordinary/detached`；
- 对应测试必须覆盖相同秒级 timestamp，继续以 SQLite rowid 保序。

### 4.3 Idempotent retry

处理指定 `turn_id` 时先查 durable row：

1. message/session/scope/subject/reply_to 与已存 normalized request 全部相同：返回原 row；
   pending 时可恢复现有 completion 调度，但不得重新解析或替换 context；
2. 任一字段不同：`409 turn_id_conflict`；
3. 即使原 context 此刻已经 stale，相同 retry 仍返回原 row，不能把它改绑到新 generation。

这里的 retry normalization 必须以**已存 binding**为依据：例如 question reply 首次请求使用默认
`scope=chat/subject=""`，落库后是 canonical `scope=confusion/subject=<question>`，相同 retry 仍应
判相同。该比较可以读取已存 target/context，但不得重新查询 current anchor registry。

## 5. HTTP 契约

### 5.1 `POST /api/chat/turns`

新增请求示例：

```json
{
  "turn_id": "turn-client-generated",
  "session": "popup",
  "scope": "chat",
  "message": "这个方向对，但更像工作需求而不是兴趣",
  "reply_to_turn_id": "confirmation-card-a"
}
```

成功仍返回 `ChatTurnOut`，其中 `reply_to_turn_id` 与
`payload.dialogue_binding.context_digest` 可用于 UI 恢复和测试。客户端提交的
`payload.dialogue_binding` 一律拒绝为 `422 reserved_payload_key`，不能静默采用。

### 5.2 `GET /api/chat/contexts/{reply_to_turn_id}`

新增只读校验端点，用于三端在 refresh 后验证本地 composer selection：

```json
{
  "active": true,
  "reply_to_turn_id": "confirmation-card-a",
  "source_type": "card",
  "kind": "hypothesis",
  "generation": 7,
  "title": "用户可能正在构建 AI 工具开发能力",
  "evidence_labels": [],
  "context_digest": "..."
}
```

该 GET 只读，不得建立/释放 anchor、patch card、submit reconcile 或产生 event。客户端本地
没有 selection 时不调用它，也不自动“发现并绑定”全局 anchor。

200 只用于 `active=true` 的完整 canonical preview；target 不存在、inactive 或 processing 分别
使用 §5.4 的 404/409 结构化错误，不另造含糊的 `200 active=false` 分支。

### 5.3 Discuss/open response

- `card.discuss` 成功 body additive 返回 canonical context preview；
- `202 processing` 时客户端等既有轮询完成，再调用 context GET；
- pending confirmation open 返回 card/question turn 后，客户端用其 turn id 调 context GET；
- 只有 context GET `active=true` 后才启用带绑定的发送；失败时保留输入草稿。

### 5.4 结构化错误

| HTTP | code | 行为 |
| --- | --- | --- |
| 404 | `reply_target_not_found` | 保留草稿，移除失效 selection，提示重新打开 |
| 409 | `reply_target_inactive` | 不创建 user turn，不回退普通聊天 |
| 409 | `reply_target_processing` | 保留草稿，按 `Retry-After` 重试 context 校验 |
| 409 | `turn_id_conflict` | 不覆盖既有 turn |
| 422 | `invalid_reply_target` / `reserved_payload_key` | 明确暴露调用方错误 |
| 503 | `dialogue_busy` | 沿用有限重试，不丢 relation |

所有错误 message 都必须可读，并在 UI 通过 `role=alert`/`aria-live` 宣告；禁止捕获后以空
`reply_to_turn_id` 重发。

## 6. Prompt、history、event 与 memory 契约

### 6.1 Interactive reply

`SocraticDialogue.respond()` 增加 typed binding 参数。`bound` 时把下列动态片段放在本轮
user prompt，而不是 system prompt：

```text
<dialogue_context>
你正在回复一张“阿B 的猜测”卡片。
卡片内容：用户可能正在构建 AI 工具开发能力
可读依据：最近连续收藏了 Agent 工程实践内容
</dialogue_context>
<user_message>
这个方向对，但更像工作需求而不是兴趣
</user_message>
```

- prompt 不包含 ref、generation、turn id、digest 或 opaque evidence ID；
- title/evidence 必须来自冻结 context，不得从 current anchor 重读；
- dynamic context 只影响本轮 user suffix，system bytes 保持不变；
- `_history` 保存原始用户文本与 relation-aware durable replay，不把 XML wrapper 当用户原话；
- bound confusion 不再叠加旧 `_contextual_chat_message()` 的第二份“关于……”前缀。

### 6.2 Durable history

hydrate 历史时，bound user turn 渲染稳定、可读的关系：

```text
[回复卡片「用户可能正在构建 AI 工具开发能力」] 这个方向对，但更像工作需求而不是兴趣
```

用户界面仍显示原始 message + 单独 reply quote；上面这段只用于 LLM history。旧 payload
没有 binding 时维持当前字节基线。

### 6.3 LEARN admission

本 spec 修订 settlement queue 旧定义：

- bound API learn 的业务 admission 是**用户 turn 首次 durable insert**；
- interactive reply 结束后的 `queue.submit(LEARN)` 只传递已经冻结的 typed snapshot；
- queue 为 `LEARN` 增加受限的 server-only frozen override，只接受
  `AnchorPersisted` 或 `AnchorNotApplicable`；不得接受 caller 构造的 reserved/failed；
- `ordinary/detached` 显式传 `AnchorNotApplicable`，不能让 queue 在 submit 时读取 latest
  anchor；
- `detached` 同时传 `inventory_settles_allowed=false`；
- API 以外未迁移的兼容调用可以暂留 legacy admission，但必须具名、测试并记录 WARNING；
  不得让新 API 路径走 legacy 分支。

### 6.4 Raw dialogue event

在任何 insight/object side effect 前记录一条可读、可审计 event：

```json
{
  "event_type": "dialogue",
  "title": "回复「用户可能正在构建 AI 工具开发能力」：这个方向对，但更像工作需求…",
  "context": "用户在回复卡片「用户可能正在构建 AI 工具开发能力」时说：……\n阿B 回复：……",
  "metadata": {
    "source": "chat",
    "session": "popup",
    "turn_id": "turn-client-generated",
    "reply_to_turn_id": "confirmation-card-a",
    "binding_mode": "bound",
    "binding_status": "active",
    "context_digest": "...",
    "anchor_kind": "hypothesis",
    "anchor_ref": "29c2b5ac",
    "anchor_generation": 7,
    "context_title": "用户可能正在构建 AI 工具开发能力"
  }
}
```

`anchor_ref/generation` 允许出现在机器 metadata 以便审计，但不得出现在用户可见证据或
自然语言 context。执行学习前先 validate 冻结 snapshot，以便把 `binding_status` 写成
`active` 或 `stale`；event 仍记录真实发生的 A 对话。`stale` 后立即返回，不能写 candidate、
profile、object settlement、card projection 或释放 B。LLM 分析返回后、首个 effect 前再做
一次 exact generation validation。

bound turn 产生的 candidate、object mutation 与 settlement ledger 还必须具备可回溯 provenance：
至少能从审计 ledger/event 还原 `source_turn_id`、`source_reply_to_turn_id` 与
`source_context_digest`。若对象 schema 已有 metadata/source refs，就直接携带这些字段；若对象
本身不适合扩 schema，则强制写对应 ledger 并引用本轮 dialogue event id。不能只把 context 用于
LLM 输入，落库后又失去它来自哪张卡的信息。

### 6.5 Single-context rule

reply prompt、history renderer、LEARN payload、event metadata、insight analyzer 输入与 settlement
ledger 的 `context_digest` 必须一致。允许某个消费者不展示 digest，不允许重新生成不同 context。

## 7. UI/UX 契约（三端一致）

### 7.1 Composer selection

点击“聊聊”或打开疑惑问题后，在 composer 上方显示轻量 context bar：

```text
正在回复  阿B 的猜测 · 用户可能正在构建 AI 工具开发能力        清除
```

- 卡片本体仍在 transcript 中；context bar 不复制全文/按钮/证据；
- 标题最多两行，极长文本截断，不得撑出横向滚动；
- “清除”只清除本 surface 的 composer selection，不伪造 defer/settled；下一条 POST 无
  `reply_to_turn_id`，服务端按 `ordinary/detached` 判定；
- selection 默认跨多轮保留，直到用户清除、context GET 失效、卡片结算或被另一 context 替换；
- selection 存在各 surface 自己的 storage namespace；本地值只是候选，refresh 后必须经只读
  context GET 校验，不能当事实；
- 切换 A→B 时必须先拿到 B 的 active context，再原子替换本地 A；失败则继续保留 A 和草稿。

### 7.2 Turn rendering

bound user bubble 上方显示一条小型 reply quote（kind + title），点击/键盘激活可滚回 target
turn。target 已不在当前 window 时只展示 quote，不发额外隐式 mutation。assistant bubble 仍跟在
该 user turn 后。

### 7.3 Scroll 与布局

- transcript 是唯一纵向 scroll owner；page/card/composer 不得互相形成滚动锁；
- 外层 flex/grid 子项显式允许收缩（`min-height: 0`），transcript 使用 `overflow-y: auto`；
- composer/context bar 不被 20+ cards 推出 viewport；移动键盘出现时仍能输入、发送和滚动；
- 新 turn 只在用户原本接近底部时自动跟随；用户阅读旧卡片时不强拉到底；
- context bar、清除按钮、reply quote 均有 visible `focus-visible`，可键盘操作；
- 错误不能只用颜色表达；正常文本对比度至少 4.5:1；respect reduced motion；
- 验证宽度至少覆盖 375、768、1024、1440px，无横向滚动。

### 7.4 ID-only evidence

若一张卡的 evidence 全是 opaque ID：

- 卡片不显示依据区域；
- context bar/reply quote 不显示 ID；
- prompt/event natural-language context 不含 ID；
- 内部 metadata/ref 仍可保留，便于审计。

## 8. 并发时间线

### 8.1 Happy path

```text
discuss(A) applied
  → client stores reply_to=A
  → POST user U(reply_to=A)
  → server resolves A + persisted generation g
  → INSERT U(reply_to=A, digest=D, snapshot=A@g)
  → reply prompt(D)
  → LEARN frozen(A@g, D)
  → pre/post validate A@g
  → event/memory/settlement(D)
```

### 8.2 A→B replacement after POST

```text
POST U(reply_to=A) + INSERT snapshot A@g
  → reply LLM blocked
  → discuss(B) replaces A
  → reply resumes using D(A), never B
  → LEARN receives A@g
  → pre-validation stale
  → event records A + stale
  → all cognitive/settlement effects = 0; B unchanged
```

### 8.3 Replacement before POST

```text
client still holds A
  → B replaces A
  → POST U(reply_to=A)
  → server sees target A != logical persisted B
  → 409 reply_target_inactive
  → durable user turn/event/reply = 0; draft retained
```

### 8.4 Same ref across sessions

popup card A1 与 web card A2 可投影同一 ref。用户在 A2 上显式聊聊后，binding 可保存
`reply_to=A2`、`anchor_origin=A1`，只要 exact ref/generation 匹配。结算仍按 ref 投影全部 session，
UI reply quote 则指向用户实际看见的 A2。

## 9. Backward compatibility

- 旧 `chat_turns` 无列/空 payload：迁移后 `reply_to_turn_id=""`，history 保持当前渲染；
- 新 API client 不传 reply relation：正常创建 `ordinary/detached` turn；
- 旧 `scope=confusion + subject_id` 请求在迁移窗口由服务端查找唯一 active question turn，
  canonicalize 成 reply relation 并记一次 deprecation WARNING；找不到/不唯一则 409，不接受
  client title 冒充 context；
- `delight/probe/avoidance_probe` 的 subject context 不属于本次 card binding，行为保持；
- legacy `/api/insights/feedback` 与 card action 协议不删除；
- CLI/OpenClaw 不新增 reply UI。其普通对话不得因 UI 端 active anchor 被隐式绑定；如无法迁移到
  typed binding，必须保留具名 compatibility mode 并写测试/PR exclusion；
- popup 与 mobile 当前可共享 `session="popup"` 的 durable display 语义，本 spec 不擅自改 session
  命名；两者的本地 composer selection 仍由不同 origin/storage 隔离。

## 10. 可证伪不变量

| ID | MUST | 失败判据 |
| --- | --- | --- |
| B1 | card/question 是 durable first-class turn | UI 只有悬浮卡，没有可恢复 target turn |
| B2 | bound user turn 有 immutable `reply_to_turn_id` | 只能从 session/current anchor 推断关系 |
| B3 | context 由服务端 canonicalize | 客户端 ref/title/generation 可进入持久化事实 |
| B4 | binding 在首次 user turn INSERT 前冻结 | reply 完成后才查询 anchor |
| B5 | 全链路复用同一 digest | prompt/event/memory/settlement 任两处 digest 不同 |
| B6 | 不做 future/current anchor inference | A turn 在任何交错下使用 B ref/generation |
| B7 | stale 只可 drop，不可 upgrade | A stale 后 B 得到 event/object/card effect |
| B8 | unbound 不隐式消费 active anchor | 清除 context 后仍分析/结算当前卡 |
| B9 | identical retry 不改绑定，divergent retry 409 | 同 turn id 可换 message/target/generation |
| B10 | opaque evidence 不进入可见/自然语言层 | 任一端、prompt 或 event context 显示纯 ID |
| B11 | 三端共享选择/错误/scroll 语义 | 任一端不能清除、恢复、发送或滚动 20+ cards |
| B12 | reply relation 可从 durable history 恢复 | refresh 后 user bubble 不知道回复了谁 |
| B13 | dynamic context 不污染 system prompt | bound turn 修改 system bytes 或泄露 opaque IDs |
| B14 | 旧数据与普通聊天兼容 | 旧 `{}` payload 无法读取，或无 anchor 普通 settles 全被禁用 |
| B15 | GET context 绝对只读 | GET 产生 queue job、anchor/card/event mutation |

## 11. 必过测试矩阵

1. 无 sleep barrier：A reply LLM 阻塞 → B discuss → 放行；循环 100 次，断言 prompt=A、
   LEARN=A、event=A/stale、B effects=0；
2. B 在 A POST 前替换：409，turn/event/reply=0；
3. identical retry：同 row、同 digest、单 reply/单 learn；divergent target/message：409；
4. 同 ref 跨 session：visible reply target 与 anchor origin 可不同，canonical ref/generation 相同；
5. clear selection + active anchor：mode=`detached`，anchor/object settlement=0；
6. 无 active anchor 普通聊天：mode=`ordinary`，既有 inventory settles byte/behavior baseline 不变；
7. card/question/terminal/not-a-context/missing target 的成功与错误表驱动测试；
8. reserved builder：`reply_target_processing`，完成后 retry 可 bound，不能先落 unbound row；
9. history hydrate：bound turn 有稳定可读 prefix；旧 turn 字节不变；
10. only-ID evidence：Python contract + shared JS + 三端 DOM + prompt/event 均不显示；
11. popup/desktop/mobile：选择、切换、清除、refresh 恢复、stale error 保留草稿；
12. 每端 30 张混合 card/question + 长回复：能下滑到底、回滚旧 turn、composer 始终可用；
13. live backend + real SQLite + real HTTP；另有一次真实已配置 LLM provider happy-path 请求；
14. CLI ordinary chat smoke：无 UI binding，不能意外结算浏览器正在聊的卡。

## 12. 交付与验收边界

Luna Max 完成代码与测试后只能把状态标记为 **implemented-awaiting-codex-acceptance**。
Codex 验收至少包括：

- 对照 B1–B15 逐项审查；
- 独立重跑 A→B barrier，而不是只接受实现者截图；
- 启动真实 backend，以真实 HTTP 检查三端；
- 使用隔离数据目录执行一次真实 provider 请求（不打印 key/cookie）；
- 审查 schema migration、旧 turn 兼容、prompt 中 ID 泄漏及三端 scroll；
- 核对本次 data-flow 变更要求的模块文档、changelog 和四处架构图。

在 Codex 给出验收结论前，本 spec 不视为完成。
