# 对话确认入口 Spec — 假设/疑惑在对话中确认:卡片、待聊列表、话题锚与归属判断

**Created:** 2026-07-22(r3,codex 二轮 F13-F21 修订;与用户五轮讨论定稿)
**Scope:** `soul/dialogue.py`、`soul/dialogue_anchor.py`(新)、`soul/dialogue_insight_analyzer.py`、`soul/dialogue_learn_queue.py`(锚快照)、`soul/engine.py`、`soul/confusion.py`、`llm/prompts.py`、`api/app.py`(durable chat 扩展/待聊端点/legacy 转发)、`api/models.py`(payload 模型)、`storage/database.py`(chat_turns payload 列迁移)、`cli.py`;前端三处:`extension/popup/` + `extension/src/background/`(角标决策表)、**桌面端 `src/openbiliclaw/web/desktop/assets/js/`**、移动端 `src/openbiliclaw/web/js/`(仅洞察按钮只读化,卡片跟进版)。基于分支 `feat/cognitive-profile-pipeline`。
**Out of scope:** 探针迁移;系统推送;多锚;对话检索(v2);移动端卡片渲染(跟进版,但其洞察确认按钮只读化**在本版**,否则唯一入口不成立,r1/F11)。

## Goal

(同 r1,产品方向不变:对话是唯一主动确认入口;假设卡片四按钮+多轮;疑惑纯聊;角标+待聊列表双轨节流;锚+归属判断零新增 LLM 调用;永续对话+时间事实化。)

r2 修正的关键事实:**`src/openbiliclaw/web/js/` 是移动端**(`api/app.py:13024` `/m → _web_dir`),桌面端是 `src/openbiliclaw/web/desktop/`(`api/app.py:12926`,`/web → _desktop_dir`);popup 用 `session="popup"`、桌面用 `session="webui"`(`desktop/assets/js/app.js:5713`);角标由 service worker 决策表控制(`extension/src/background/badge.ts`、`service-worker.ts:381 computeActionBadge`),不归 popup。

## Design invariants (MUST hold)

1. **对话为唯一主动 UI 确认入口 + legacy 转发兼容(r2/F11)**:新客户端(popup/桌面/移动)全部移除洞察确认按钮(认知更新区只读);`POST /api/insights/feedback` 端点**保留并标记 deprecated**,服务端将其转发进与卡片 action 相同的结算路径,台账 `source="legacy_endpoint"`——旧客户端不破,唯一入口以"唯一主动 UI"定义。探针留在推荐流。
2. **卡片与结算态的 durable source-of-truth(r2/F2)**:`chat_turns` 增 `payload TEXT`(JSON)列(幂等迁移):卡片 turn 的 `card {kind, ref, title, evidence_refs, actions, state}` 落库;结算后 patch `payload.state`(pending→confirmed|rejected|deferred 单向)。假设侧投放/冷却元数据(`last_asked_at / deferred_until` per hash8)持久化于 soul 状态存储(与 rebuild_pending 同风格);全局抛出冷却 12h 同处持久化。schema 迁移列入 plan Task。
3. **卡片 turn 进现有流 + 「一个大脑多个屏幕」(r3/F19 修正)**:对话认知流是**单一**的(单 `_history`、单认知语境——产品定调);`session` 仅是 **UI 归属标签**(决定哪个端显示哪些 turn)。**回灌:单一 history 回灌全部 `{chat, hypothesis, confusion}` scope 的 completed turn,不分 session**(认知连续;r2 的"各回灌各的"作废——那会造成双端大脑分裂);前端 turn 列表按各自 session 过滤显示。卡片 turn 落库 `session` 取产生端(用户 open → 请求端;系统抛出 → 附着的用户消息所在端)。dialogue 单例的固定 `_session` 字段保留为默认标签,durable 路径逐请求传 session。
   **抛出附着的确定性(r3/F18)**:拦截点=durable 用户消息接收处理内——同一处理流程中先 INSERT 卡片 turn 再处理用户消息;顺序稳定性:turn 列表排序改为 `(created_at, rowid)`(rowid 单调,替换随机 turn_id tiebreak,兼容既有数据);去重:同 ref 已存在 state=pending 的卡片 turn 则不重复附着;重启/重试测试覆盖。
4. **action 契约:对象为真源、卡片为投影(r3/F16/F15/F13)**:
   - **对象级仲裁**:结算真源是假设对象本身(`update_from_feedback` 的 validated/confidence 态)。action 执行顺序=①查对象当前态,已终态 → 返回 `already_settled` 并顺手把本卡片 payload patch 为对应终态;②未终态 → **先执行副作用**(confirm/reject → `update_from_feedback`,幂等;defer → 冷却持久化)→ ③成功后 CAS `payload.state`(pending→终态)。崩溃于②③之间 → payload 仍 pending,用户重试被①的幂等吸收——**副作用先行+幂等,状态标记后行,无需 outbox**(故障注入测试覆盖)。同一 ref 多张卡片(多端/重复 open)由①对象级判定统一,不可能各自 CAS 出相反结果;`discuss` → CAS pending→discussing(幂等,重复 discuss 返回现态)并建锚。
   - **卡片 turn 状态语义(F13)**:卡片 turn 创建即 `status="completed"`(卡片自身就是内容,无 LLM 回复可生成),`payload.type="card"`;durable worker 对 completed turn 不触发 LLM 生成(guard 测试)。
5. **锚快照/代次/卡片关联(r3/F17)**:anchor 携带单调 `generation` 与 **`origin_turn_id`**(建锚来源卡片,疑惑提问 turn 同理)、`ambiguous_count`(非 ambiguous 到达即清零,r2/F21);学习 payload 捕获 `{anchor_ref, anchor_generation}`,处理时 generation 失配 → 丢弃+WARNING。**锚结算时经 origin_turn_id 把来源卡片 payload patch 为终态**(卡片关闭、角标计数随之下降);discuss 重试幂等(payload 已 discussing 返回现态)。
6. **锚生命周期四条解除(r2/F7)**:①结算;②连续 2 轮 `unrelated`;③TTL 2h;④**replaced**(新锚顶替,台账 released:replaced)。**confusion 解锚时(除结算外)状态回 `open`**——释放 clarifying 唯一槽,防止占死(`database.py` partial unique index 约束下的活性保证)。锚状态持久化+台账。
7. **confusion 结算单一所有者 + 崩溃可恢复(r3/F14)**:durable side-effect 的直接 `resolve/defer` 移除,结算统一归锚处理器(学习队列内串行);分类器输出并入。**队列是内存的、失败即丢——补持久化恢复**:confusions 行增 `last_processed_turn_id`(处理 receipt,与结算写同事务);12h 扫描发现「clarifying 且存在 completed 回复 turn 晚于 receipt」→ 重放该轮归属判断(幂等:receipt 比对,已处理跳过);带崩溃重放测试。假设卡片按钮与锚处理器的并发由对象级仲裁(不变量 4①)统一。
8. **归属判断可证伪(r2/F8)**:relation 枚举加 `ambiguous`(含糊:不结算、锚保持、允许一次追问;两次 ambiguous 计 defer)。kind×relation 合法矩阵入 spec(hypothesis: support/contradict/revise/ambiguous/unrelated;confusion: answer/ambiguous/unrelated;越界组合按 unrelated + WARNING)。防双计代码防御**具体定义**:candidates 内容与锚对象文本做「NFC 规范化 + 中文 bigram / 英文小写 token 分词 + Jaccard 重叠 ≥0.5 即丢弃 + WARNING」(校准注释,首轮重校;停用词表:的/了/是/在/我/有 + a/the/is 等);LLM prompt 指令与结算所有权两道防线不变。
9. **时间即事实,且前缀稳定(r2/F9 重设计)**:历史轮渲染**绝对时间戳**(`[MM-DD HH:mm]`,turn 创建时定死,**永不随 now 改写**——prompt 前缀跨轮字节稳定,cache 不破);「现在几点」以「当前时间:...」注入 **user prompt 尾段**(每轮变但在末尾,不污染前缀);LLM 由两者自行推断"三天前"。时区契约:统一使用本地时区,`chat_turns.created_at`(SQLite CURRSENT_TIMESTAMP 为 UTC)读取时转换为本地——转换函数单点、可注入、有测试。回放基线一次性更新(有意变更)。
10. **打扰双轨 + 角标归 SW(r2/F10)**:系统抛出全局冷却 ≥12h(持久化)+ 同对象 72h + clarifying ≤1;用户主动零冷却。**插件角标由 service worker 既有决策表扩展**(`badge.ts`/`computeActionBadge`):新增 pending-confirmations 信号——**复用既有 30s alarm 周期(`service-worker.ts:538`),不新增定时器**,请求轻量 count(列表端点 `?count_only=1`),runtime-stream 事件触发的刷新做去抖,离线/未初始化停轮询且不显示计数(r3/F20);优先级:健康/错误类 badge > 待确认数字。桌面端在对话入口显示计数(不涉 SW)。
11. **结算复用/幂等/台账/prompt-cache/LLM 防御/阈值出处**:沿用既有全套不变量;新常量(锚 TTL 2h、全局冷却 12h、Jaccard 0.5、追问上限 1)带校准注释。
12. **四端契约(r2/F1 修正)**:本版交付 popup + **桌面端(`web/desktop`)** 卡片/待聊/角标;**移动端(`web/js`)本版仅洞察按钮只读化**,卡片渲染跟进版(PR 声明);CLI `openbiliclaw questions` 只读。**README/README_EN 架构图无条件同步**(数据流变化,CLAUDE.md 强制,r2/F12)。

## kind × relation 合法矩阵(实现与测试对照)

| | support | contradict | revise | answer | ambiguous | unrelated |
|---|---|---|---|---|---|---|
| hypothesis 锚 | ✅投票+ | ✅投票- | ✅reject+派生 | ❌→unrelated | ✅追问/defer | ✅streak+1 |
| confusion 锚 | ❌→unrelated | ❌→unrelated | ❌→unrelated | ✅resolve(命中解读) | ✅追问/defer | ✅streak+1 |

## 交互设计定稿

(同 r1:卡片四按钮、疑惑纯聊气泡、待聊列表入口、已结算态原地替换、多轮示例契约「聊聊→反驳→修正→确认」。)

## Wave

| Wave | 内容 | Tier |
| --- | --- | --- |
| A | chat_turns payload 迁移 + 绝对时间戳渲染(基线更新)+ 锚(四条解除/代次/持久化)+ 归属判断(矩阵/ambiguous/Jaccard 防御)+ confusion 所有权收归 | MUST |
| B | 卡片 turn(scope 扩展/进流/session 规则)+ action 端点(CAS)+ 待聊列表/角标 API + 双轨冷却(持久化)+ legacy 转发 | MUST |
| C | 前端:popup(卡片/待聊/SW 角标信号)+ 桌面端 `web/desktop`(卡片/待聊/计数)+ 移动端洞察按钮只读化 | MUST |
| D | CLI questions + 认知更新区只读迁移收尾 + 文档(含 README 双语图无条件)+ 真实端到端 | MUST(收尾) |

## Expected impact / Documentation obligations

(同 r1,增:README/README_EN 图无条件同步;`docs/modules/api.md` 或对应 API 文档新端点;移动端只读化写入 changelog。)
