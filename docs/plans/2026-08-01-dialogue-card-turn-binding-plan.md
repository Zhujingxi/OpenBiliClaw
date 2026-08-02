# 对话卡片入流与 Turn 级上下文绑定 — Luna Max Implementation Plan

> **Spec:**
> [`2026-08-01-dialogue-card-turn-binding-spec.md`](./2026-08-01-dialogue-card-turn-binding-spec.md)
>
> **Executor:** Luna Max
>
> **Acceptance:** Codex（Luna Max 不得自行标记 accepted）
>
> **Initial status:** ready-for-implementation
>
> **Final implementation status:** implemented-awaiting-codex-acceptance

## 0. 执行规则

1. 严格按 Wave 0 → 5 执行；Wave 0 的 RED tests 未稳定复现，不得先改生产代码。
2. 当前 worktree 可能已有用户改动。先记录 `git status --short`，不得 reset/checkout/覆盖无关变更；
   每个 task 只暂存自己的文件。
3. 每个 task 采用“新增/改写测试 → 证明按预期失败 → 最小实现 → targeted tests → 回归”的顺序。
4. 不用 sleep 证明竞态；使用 `asyncio.Event`/barrier/fake provider/checkpoint。
5. 不通过扩大 timeout、自动降级成 unbound、吞掉 409 或延迟查询 current anchor 来让测试变绿。
6. 任何 client 提交的 `kind/ref/generation/title/evidence/dialogue_binding` 都不能成为 canonical 事实。
7. 新增阈值/长度/重试预算必须写校准注释；LLM structured output 继续白名单校验。
8. 每个 Wave 结束执行对应文档 gate。接口、模块边界、数据流、CLI/兼容行为变化必须同步文档。
9. 推荐每个 Wave 一个 Conventional Commit；Wave 2 与 Wave 3 的 runtime cutover 必须同一发布窗口，
   不得发布“API 已存新 binding、LEARN 仍晚抓 current”的中间态。

## 1. 开工前基线

### 1.1 必读

- 本 plan 与配套 spec；
- `docs/plans/2026-07-22-dialogue-confirmation-entry-{spec,plan}.md`；
- `docs/plans/2026-07-23-dialogue-settlement-queue-{spec,plan}.md`，尤其 admission timeline、
  LLM head-of-line 决策、HTTP 200/202 和 known limitations；
- `CLAUDE.md#documentation-requirements`；
- 仓库 `AGENTS.md`。

### 1.2 基线命令

在仓库已有开发环境中记录以下结果；不要打印或复制本地 secrets：

```bash
git status --short
python -m pytest tests/test_dialogue_settlement_queue.py tests/test_dialogue_context.py tests/test_api_app.py -q
ruff check src/ tests/
mypy src/
cd extension && npm test && npm run typecheck && npm run build
```

若基线因现有用户改动失败，先记录准确失败与归属，不得顺手改无关代码。

### 1.3 不变量覆盖索引

| Spec invariant | Primary tasks |
| --- | --- |
| B1 first-class target turn | 1.2、4.1–4.4 |
| B2 immutable reply relation | 1.2、2.1 |
| B3 server canonicalization | 1.1、1.3、2.1 |
| B4 POST-time freeze | 0.1、2.1 |
| B5 one digest end-to-end | 1.1、3.1–3.2 |
| B6 no future/current inference | 0.1、2.2–2.3 |
| B7 stale only drops | 0.1、3.2–3.3 |
| B8 explicit unbound/detached | 0.2、2.1–2.2、3.2、4.1 |
| B9 retry immutability | 0.2、1.2、2.1 |
| B10 opaque evidence hidden | 0.2、1.1、3.1–3.2、4.1–4.4 |
| B11 three-surface UX/scroll | 4.1–4.5、5.2 |
| B12 durable relation recovery | 1.2、2.3、3.1、4.1–4.4 |
| B13 prompt-cache/system safety | 3.1 |
| B14 legacy/ordinary compatibility | 0.2、1.2、3.1–3.3、5.1 |
| B15 context GET read-only | 0.2、1.3 |

## Wave 0 — 冻结 contract 与竞态（只写 RED tests）

### Task 0.1：建立可重复的 A→B 错绑测试

**Files:**
`tests/test_dialogue_turn_binding.py`（新）、
`tests/test_dialogue_settlement_queue.py`、
必要时现有 fake provider/checkpoint fixture。

**Steps:**

- [ ] 建立 completed/discussing card A 与 exact persisted anchor A@g；
- [ ] POST 一条 `reply_to_turn_id=A` 的 durable user turn，用 barrier 卡住 interactive LLM；
- [ ] 在另一 coroutine/HTTP client 完成 card B discuss/replacement；
- [ ] 放行 A reply，捕获 interactive prompt、LEARN envelope、raw event、card settlement effect；
- [ ] 写出最终期望：所有 readable context/digest/ref 都是 A；A 已 stale 时 effect=0，B effect=0；
- [ ] 循环 100 次，无 `sleep()`；
- [ ] 在改生产代码前确认当前实现稳定失败，并在测试注释记录原失败证据。

**Acceptance:** 测试失败原因必须是 late binding/prompt context 缺失，不是 fixture 失效或 timeout。

### Task 0.2：其余协议 RED matrix

**Files:** 同上，外加 `tests/test_storage.py`、`tests/test_soul_engine.py`、
`extension/tests/dialogue-confirmation.test.ts`。

**Write failing tests for:**

- [ ] B 在 POST 前替换 A → structured 409、无 row/event/reply；
- [ ] identical `turn_id` retry 与 divergent message/target retry；
- [ ] target missing/not-card/terminal/reserved；
- [ ] clear 后 active anchor 存在 → `detached`，任何 object settlement=0；
- [ ] 无 anchor normal chat → `ordinary` baseline 不变；
- [ ] same-ref cross-session target/origin；
- [ ] only-ID evidence 的 Python/JS contract fixtures；
- [ ] legacy `{}` payload 与旧 schema migration；
- [ ] context GET read-only spy（queue/anchor/card/event mutation 全为 0）。

**Acceptance:** spec §11 中后端核心用例均有明确 RED test 名称和失败原因。

**Suggested commit:** `test: freeze dialogue card turn binding contract`

## Wave 1 — Typed binding、数据库 relation 与 canonical resolver

### Task 1.1：新增共享 typed value object

**Files:**
`src/openbiliclaw/soul/dialogue_turn_context.py`（新）、
`tests/test_dialogue_turn_context.py`（新）、
共享 evidence fixture（放在 Python/JS 都可读取的稳定位置）。

**Steps:**

- [ ] 实现闭集 `BindingMode(bound|ordinary|detached)`；
- [ ] 实现 frozen `DialogueTurnContext`/`DialogueTurnBinding`；
- [ ] 严格 parse/serialize，拒绝缺 ref、非正 generation、未知 kind/source/version；
- [ ] canonical JSON + full SHA-256 digest；
- [ ] 实现 readable prompt/history/event projections；
- [ ] 实现 evidence label 去重/opaque ID 过滤/5×240 预算与校准注释；
- [ ] fixture 同时覆盖纯数字、hash、UUID、BV/av/cv、prefixed ID、URL、中文句子和重复值。

**Acceptance:** value object round-trip、digest determinism、坏值 fail closed、Python/JS evidence fixture 全绿。

### Task 1.2：持久化 reply relation

**Files:**
`src/openbiliclaw/storage/database.py`、
`src/openbiliclaw/api/models.py`、
`tests/test_storage.py`、`tests/test_api_models.py`（若模型测试集中在别处则沿用现有文件）。

**Steps:**

- [ ] fresh schema 增 `reply_to_turn_id NOT NULL DEFAULT ''` 与索引；
- [ ] legacy table additive/idempotent migration；
- [ ] create/get/list/normalize/fallback store 全部 thread relation；
- [ ] `ChatTurnIn/Out` 增 top-level relation；
- [ ] 修改 create DAO：既有 turn 返回前可比较 normalized immutable request；
- [ ] 保证旧 row/坏 payload 按 unbound legacy row 读取，不 crash；
- [ ] 不把 client `payload.dialogue_binding` 直接写入。

**Acceptance:** fresh + 两次 migration + legacy row + relation query + idempotent conflict tests 全绿。

### Task 1.3：canonical resolver 与只读 context endpoint

**Files:**
`src/openbiliclaw/api/app.py`、
必要的 `src/openbiliclaw/api/models.py`、
`tests/test_api_app.py`、`tests/test_dialogue_turn_binding.py`。

**Steps:**

- [ ] 从 target row 解析 card/question，不信任 client context；
- [ ] 从 queue admission registry 增加只读 typed peek；不 retain reservation、不 mutation；
- [ ] exact persisted 才 active；reserved/absent/foreign/terminal 各自结构化错误；
- [ ] same-ref cross-session 允许 visible target 与 anchor origin 不同；
- [ ] 实现 `GET /api/chat/contexts/{reply_to_turn_id}`，用 spy 证明绝对只读；
- [ ] card discuss response additive context preview；202 后沿用轮询再校验；
- [ ] context resolver 不把 opaque evidence 放入 output。

**Acceptance:** spec §4–5 的 HTTP matrix 全绿，OpenAPI/Pydantic 输出稳定。

**Suggested commit:** `feat: add durable dialogue turn relations`

## Wave 2 — POST-time capture 与 queue admission 修订

### Task 2.1：在 durable INSERT 前冻结 binding

**Files:**
`src/openbiliclaw/api/app.py`、
`src/openbiliclaw/storage/database.py`、
`tests/test_api_app.py`、`tests/test_dialogue_turn_binding.py`。

**Required ordering:**

```text
normalize request
→ existing turn idempotency check
→ optional system confirmation attach (unbound requests only)
→ synchronous canonical context/logical-anchor capture
→ synchronous durable user-turn INSERT
→ background reply task
```

**Steps:**

- [ ] bound request 跳过 `_maybe_attach_system_confirmation()`；
- [ ] unbound request 若本轮新附着 confirmation，无条件记 `detached`；否则再按 logical anchor
  区分 `ordinary`/`detached`；
- [ ] capture 与 INSERT 间不得有 `await`；增加结构测试或 barrier 证明；
- [ ] server 生成并持久化 `dialogue_binding` 与 digest；
- [ ] 根据 target canonicalize scope/subject：card reply→chat，question reply→confusion；拒绝冲突
  的非默认 client scope/subject；
- [ ] client reserved payload key 422；
- [ ] identical retry 直接采用已存 binding，不重新解析 current generation；
- [ ] question reply 的默认 request scope/空 subject 与落库后的 canonical confusion scope/subject
  可按已存 binding 判为 identical retry；
- [ ] divergent retry 409；任何 context error 都不创建 fallback row。

**Acceptance:** POST 前/后 replacement、attached confirmation、retry matrix 全绿。

### Task 2.2：给 LEARN 提供 server-only frozen override

**Files:**
`src/openbiliclaw/soul/dialogue_learn_queue.py`、
`src/openbiliclaw/api/runtime_context.py`、
`tests/test_dialogue_settlement_queue.py`、
`tests/test_api_runtime_context.py`（若无则沿用 runtime 现有测试）。

**Steps:**

- [ ] `LEARN` 支持受限 frozen admission override；只接受 `AnchorPersisted` 或
  `AnchorNotApplicable`；
- [ ] override 必须由 server-parsed durable binding 构造，其他 job kind/typed state fail closed；
- [ ] override 时不再调用 registry 的 latest snapshot；
- [ ] `ordinary/detached` 都传 NotApplicable，但 payload 分别携带
  `inventory_settles_allowed=true/false`；
- [ ] 保证 registry release/refcount/refresh 不因持久化 snapshot 出现 underflow 或覆盖 later head；
- [ ] 保留 API 外 compatibility path，但具名并用 spy 证明新 API 调用数为 0；
- [ ] 更新旧 queue spec 中“LEARN 受理=reply 后 submit”的定义，加入本 spec amendment。

**Acceptance:** A snapshot 在 B 成为 latest 后提交 LEARN，job 仍是 A；unbound job 永远不捕获 B；
queue 所有 owner/reservation/reload tests 继续全绿。

### Task 2.3：切通 durable reply task

**Files:**
`src/openbiliclaw/api/app.py`、
`src/openbiliclaw/soul/dialogue.py`、
相关测试。

**Steps:**

- [ ] `_complete_durable_chat_turn()` 从 durable row 重建 binding；
- [ ] `_generate_durable_chat_reply()` 将 typed binding 传入 dialogue；
- [ ] `SocraticDialogue.respond()` 的 LEARN payload 使用同一 binding/digest/frozen snapshot；
- [ ] 不把 API request object 或可变 dict 捕获进 background task；
- [ ] 重启/重复调 pending turn 时仍从 row 恢复同一 binding。

**Acceptance:** durable row 是唯一恢复源；代码搜索证明 API reply path 没有 reply 后 current-anchor lookup。

**Suggested commit:** `fix: freeze dialogue context at durable turn admission`

## Wave 3 — Prompt、event、memory 与 settlement 单快照切换

> Wave 2 与 Wave 3 必须一起发布。任何中间版本若持久化新 binding 却仍让 engine 晚抓 current，
> 都不满足安全边界。

### Task 3.1：interactive prompt 与 history renderer

**Files:**
`src/openbiliclaw/soul/dialogue.py`、
`src/openbiliclaw/llm/prompts.py`（若 builder 位于此处）、
`tests/test_dialogue_context.py`、`tests/test_llm_prompts.py`。

**Steps:**

- [ ] bound context 只进入本轮 user suffix，system prompt bytes 不变；
- [ ] prompt 只含 title/readable evidence，不含 ref/generation/turn id/digest/opaque ID；
- [ ] bound confusion 去除双重旧 scope wrapper；
- [ ] in-memory history 保存原始文本；durable hydrate 用稳定 relation prefix；
- [ ] 旧 turn、ordinary short history 与既有 prompt-cache baseline 不变；
- [ ] 加 spy 暴露 prompt 所用 digest（仅测试/日志 metadata，不写进 prompt）。

**Acceptance:** A/B prompt test、ID leakage test、system byte invariance、legacy history baseline 全绿。

### Task 3.2：event 与 engine 输入统一

**Files:**
`src/openbiliclaw/soul/engine.py`、
`src/openbiliclaw/soul/dialogue_insight_analyzer.py`、
必要的 memory/event formatter、
`tests/test_soul_engine.py`、`tests/test_memory_manager.py`。

**Steps:**

- [ ] `learn_from_dialogue` 接受 typed binding/digest 与 `inventory_settles_allowed`；
- [ ] 在任何 insight effect 前验证 frozen snapshot，得到 active/stale status；
- [ ] raw event 写 turn/reply target/mode/status/digest/kind/ref/generation/title；
- [ ] event natural-language context 使用冻结 title，不显示 opaque ID；
- [ ] stale event 写完立即返回，candidate/profile/object/projection/release effect=0；
- [ ] active analyzer 使用冻结 context，不从 active object 重建另一份文案；
- [ ] LLM 返回后、首 effect 前再次 exact validation；
- [ ] detached 禁止 anchor relation 与 inventory settles，ordinary 保持现状；
- [ ] bound candidate/object effect 通过自身 metadata/source refs 或 mandatory audit ledger 保留
  `source_turn_id/source_reply_to_turn_id/source_context_digest`，并引用 raw dialogue event；
- [ ] settlement/ledger 可观察到相同 digest；不得改 B。

**Acceptance:** event→analyzer→settlement digest equality；stale A 不产生 B side effect；普通聊天 baseline 绿。

### Task 3.3：confusion 与既有 side-effect ownership 回归

**Files:**
`src/openbiliclaw/api/app.py`、`src/openbiliclaw/soul/engine.py`、
`tests/test_confusion_lifecycle.py`、`tests/test_dialogue_settlement_queue.py`。

**Steps:**

- [ ] 新客户端回复 question 时，服务端 canonicalize scope/subject/reply target；
- [ ] `CONFUSION_REPLY_APPLY` 与 LEARN 不重复 resolve/object effect；
- [ ] legacy `scope=confusion` 仅在唯一 active question 时 server-side 转换，并 WARNING；
- [ ] replay queue、12h replay、ambiguous/unrelated 计数、anchor release 现有 tests 全回归；
- [ ] hypothesis card confirm/reject/defer/discuss 四 action 与 projection 全回归。

**Acceptance:** 每种 confusion outcome 恰一个 owner；旧 direct 路径没有新增第三个旁路。

**Suggested commit:** `fix: carry one dialogue context through reply and learning`

## Wave 4 — 三端 shared interaction 与 scroll

### Task 4.1：扩展 shared dialogue-confirmation contract

**Files:**
`src/openbiliclaw/web/shared/dialogue-confirmation.js`、
`extension/tests/dialogue-confirmation.test.ts`、
`extension/tests/dialogue-confirmation-wiring.test.ts`。

**Steps:**

- [ ] 增 context candidate normalize/store/validate/replace/clear helpers；
- [ ] 使用 surface-local storage namespace，本地数据永远先经 context GET；
- [ ] discuss/open 成功才选择；202 等轮询完成；
- [ ] submit builder 只发送 `reply_to_turn_id`，不发送 canonical context；
- [ ] 409/422/503 不得去掉 relation 自动重发；保留 draft 并宣布错误；
- [ ] 成功发送后 selection 默认保留，支持多轮；terminal/stale/clear 时移除；
- [ ] bound bubble 的 reply quote 指向 target turn；
- [ ] 复用共享 evidence fixtures，ID-only 不渲染。

**Acceptance:** shared pure-function/DOM contract tests 先绿，再接三端；不得复制三套状态机。

### Task 4.2：extension popup

**Files:**
`extension/popup/popup.html`、`extension/popup/popup.js`、相关 CSS、
`extension/tests/chat-layout.test.ts`。

**Steps:**

- [ ] composer context bar + clear + focus/aria；
- [ ] card discuss/question open 接 shared selection；
- [ ] `startChatTurn` thread `reply_to_turn_id`；
- [ ] history/refresh relation quote 与 stale recovery；
- [ ] 30 cards scroll，composer 不被推出，可回滚 target；
- [ ] extension CSP/build 不引入 inline script 或新远程资源。

### Task 4.3：desktop web

**Files:**
`src/openbiliclaw/web/desktop/assets/js/app.js`、
`src/openbiliclaw/web/desktop/assets/css/app.css`、desktop HTML、
`tests/test_desktop_dialogue_layout.py`、`tests/test_desktop_dialogue_layout_e2e.py`。

**Steps:** 与 Task 4.2 同一行为矩阵，session 保持 `webui`；键盘与 1024/1440px 验证。

### Task 4.4：mobile web

**Files:**
`src/openbiliclaw/web/js/api.js`、
`src/openbiliclaw/web/js/views/chat.js`、
`src/openbiliclaw/web/css/app.css`、mobile HTML、
`tests/test_mobile_dialogue_surface.py`、`tests/test_mobile_web_view_models.py`。

**Steps:** 与 Task 4.2 同一行为矩阵；保留现有 `session="popup"` durable 语义；重点验证
375px、软键盘、touch scroll、safe area、长标题两行截断和无横向滚动。

### Task 4.5：三端视觉/无障碍 gate

- [ ] transcript 唯一 scroll owner，flex/grid child `min-height:0`；
- [ ] 新 turn 只在 near-bottom 自动跟随；
- [ ] context clear/reply quote 可 Tab、Enter/Space 操作，有 `focus-visible`；
- [ ] error `role=alert`/`aria-live`，状态不只靠颜色；
- [ ] normal text contrast ≥4.5:1，reduced motion；
- [ ] 375/768/1024/1440px screenshot；
- [ ] 每端 30 张混合卡片可下滑到底并继续发送。

**Verification:**

```bash
cd extension
npm test
npm run typecheck
npm run build
```

**Suggested commit:** `feat: bind card context in all chat surfaces`

## Wave 5 — 真实请求、全回归、文档与 Luna handoff

### Task 5.1：live backend + real HTTP integration

**原则:** 使用临时/隔离 data dir 和测试用户状态；不移动、覆盖或提交真实 `config.toml`，不输出
API key/Cookie。真实 provider smoke 有费用时只发一组最小消息。

**Required scenarios:**

- [ ] 正常 ordinary chat；
- [ ] hypothesis card confirm/reject/defer/discuss；
- [ ] question open + reply；
- [ ] bound multi-turn + clear + detached；
- [ ] A→B replacement after POST（可控 fake provider 的真实 HTTP server）；
- [ ] A stale before POST 409 + draft retry；
- [ ] refresh/restart 恢复 relation；
- [ ] same ref popup/web projection；
- [ ] ID-only evidence 不显示；
- [ ] 一次真实 configured LLM provider bound happy path，核对回答确实围绕目标 card；
- [ ] CLI ordinary chat smoke 不结算 UI active card。

保存脱敏 transcript：HTTP status、turn ids、context digests、event metadata、card final states、
provider/model 名称和时间；禁止保存 secret 或完整私密 profile。

### Task 5.2：真实浏览器三端 E2E

在同一个 live backend 上分别打开 popup harness/真实 extension popup、`/web`、`/m`：

- [ ] card 在 transcript 内，选择后 composer 出现 context bar；
- [ ] user bubble 有 reply quote，可回到 card；
- [ ] 30 cards/long replies 可持续向下滚动；
- [ ] 切换/清除/刷新/失败保留草稿；
- [ ] 各端真实发送请求并完成 reply polling；
- [ ] 截图覆盖 375、768、1024、1440px，记录 console/network error 为 0 或解释已知基线。

### Task 5.3：全量质量门

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest
pytest --cov=openbiliclaw
cd extension && npm test && npm run typecheck && npm run build
```

另外对 A→B barrier 单独执行至少 100 次；不能只依赖全量套件碰巧跑一次。

### Task 5.4：强制文档同步

按实际代码范围更新，不得提前把未实现项写成已上线：

- [ ] `docs/modules/api.md`：request/output/context GET/errors/idempotency；
- [ ] `docs/modules/soul.md`：POST-time binding、prompt/event/memory/settlement 数据流；
- [ ] `docs/modules/extension.md` 与 `docs/mobile-web-spec.md`：三端 interaction/storage/scroll；
- [ ] `docs/modules/cli.md`：明确 CLI UI exclusion 与不隐式绑定语义；
- [ ] `docs/changelog.md`；
- [ ] cross-module data flow 已改变，更新 `docs/architecture.md`、`docs/spec.md`、
  `README.md`、`README_EN.md` 的架构图；
- [ ] amendment 旧 confirmation/settlement queue spec，消除“LEARN reply 后才 admission”的冲突；
- [ ] 无 config/installer 变化则在 PR 明确 N/A，不改对应文档；若实现时实际改变则补齐。

### Task 5.5：交给 Codex 的验收包

Luna Max 提交以下内容后停止，不得写“验收通过”：

1. 变更摘要和逐文件职责；
2. B1–B15 → test name 的 traceability 表；
3. targeted/full/extension 命令与结果；
4. A→B 100 次 barrier 结果；
5. live HTTP + real provider 脱敏记录；
6. popup/desktop/mobile 截图或视频路径；
7. `git diff --check`、`git status --short` 和未触碰的既有用户改动说明；
8. 已知限制/未解决项（若任一 MUST 未满足，状态是 blocked，不是 implemented）。

**Suggested commit:** `docs: document dialogue turn binding contract`

## 2. Codex 最终验收门

Codex 将独立执行而不是只阅读 Luna 报告：

- [ ] review diff，确认没有 late/current-anchor lookup、client-trusted context 或 silent fallback；
- [ ] 复跑 targeted/full/static/extension gates；
- [ ] 重新运行 A→B barrier 100 次；
- [ ] 重新启动 live backend 做真实 HTTP 和一次 provider smoke；
- [ ] 操作三个 UI surface，重点验收多卡滚动、context clear、refresh 和错误保留草稿；
- [ ] 查询 SQLite/event/ledger，确认同一 turn 的 reply target/context digest 一致；
- [ ] 对照 B1–B15 与文档清单；
- [ ] 给出 `accepted`、`accepted-with-follow-ups` 或 `rejected`，并列证据。

## 3. 回滚与已知边界

- runtime 回滚必须同时回退 Wave 2–3；保留 additive DB 列可安全兼容旧代码，但不能留下只写不读
  的半迁移行为；
- UI 可单端回滚展示，但新旧端混用时服务端仍必须保护 relation/error contract；
- feature flag 若新增，只能关闭 selection UI，不能让 bound 请求降级成 current-anchor inference；
- in-memory reply/learn job 仍可能在强杀时丢失，沿用 settlement queue 既有 known limitation；
- 多 backend writer 不在支持范围，部署/验收必须使用单 writer。
