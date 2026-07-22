# 对话确认入口 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-22-dialogue-confirmation-entry-spec.md`](./2026-07-22-dialogue-confirmation-entry-spec.md)
> **Status:** r1 — 待 codex 对抗 review
> **Execution order:** Wave A(Task 0→1→2)→ Wave B(Task 3→4)→ Wave C(Task 5)→ Wave D(Task 6)。Wave 内按序;每 Wave 完成其文档子集方可交付。
> **Tech:** Python:仓库 `.venv/bin/python` + worktree 根 + `PYTHONPATH=$PWD/src`(import 自查);测试前 `mv config.toml /tmp/dce_config_stash.toml`,结束**必须还原并 diff 确认原件**(5070 字节含 source_incremental_hours;测试会向 cwd 写新 config);`ruff format/check src/ tests/`;`mypy src/`(strict)。前端:`cd extension && npm test && npm run typecheck && npm run build`;桌面 Web 为 `src/openbiliclaw/web/`(JS 直服,改后端即生效)。

**Invariants that MUST hold — re-read before each task:**

- 对话唯一确认入口;不新建通道/线程;探针不动。
- 锚:同时 ≤1;解除仅三条(结算/连续2轮unrelated/TTL 2h);持久化+台账;失效靠隐式 settles 兜底。
- 归属判断合并进现有提取调用(零新增 LLM 调用);防双计三道防线;revise→reject+派生;解析失败保守。
- 打扰双轨:系统抛出全局冷却 ≥12h + 同对象 72h + clarifying ≤1;用户主动零冷却;角标只计高优先级。
- 时间即事实:逐轮相对时间戳,确定性可注入 now,无阈值无模式;回放基线一次性更新单独提交。
- 结算复用既有函数,幂等,台账带 turn_id;卡片=结构化 durable turn(scope="hypothesis")。
- prompt-cache 静态性 + invariance 清单;新常量带校准注释;LLM 输出白名单/clamp+WARNING。
- 四端:popup+桌面 Web 本版;CLI 只读;移动 Web 显式排除。

---

## Wave A — 后端核心(时间戳/锚/归属)

### Task 0: 对话窗口逐轮时间戳渲染

**Files:** 修改 `src/openbiliclaw/soul/dialogue.py`(历史轮携带时间戳;渲染注入相对时间标注,`now` 可注入)、`src/openbiliclaw/llm/prompts.py`(若渲染在 builder 侧则同步;system 段不动);测试 `tests/test_dialogue_context.py`。

**Steps:**

- [ ] **基线更新单独提交**:时间戳是有意 prompt 变更——先提交新基线快照(固定 now + 固定轮时间的确定性输出)。
- [ ] Failing test:20 轮含不同时间 → 渲染带「3 天前/2 小时前/刚刚」标注(注入 now 断言字节确定);回灌轮用 `chat_turns.created_at`,内存轮用记录时间。
- [ ] Confirm FAIL → 实现(相对时间格式化函数,确定性,禁止渲染路径裸调当前时间)→ PASS。
- [ ] Run `pytest tests/test_dialogue_context.py tests/test_llm_prompts.py -q` + ruff + mypy。

**Acceptance:** 时间标注 3 档(分钟/小时/天)+ 确定性断言 + invariance 清单绿。Reproduce with `PYTHONPATH=$PWD/src <venv>/python -m pytest tests/test_dialogue_context.py -q`。

### Task 1: 锚状态与生命周期

**Files:** 修改 `src/openbiliclaw/soul/dialogue.py` 或新增 `src/openbiliclaw/soul/dialogue_anchor.py`(anchor {kind, ref, established_at, unrelated_streak};建立/解除三条件;TTL 2h 校准注释;持久化——与对话回灌同源存储,重启恢复);台账行(anchor_established/anchor_released + 原因);新增 `tests/test_dialogue_anchor.py`。

**Steps:**

- [ ] Failing tests:建立(三入口:疑惑抛出/卡片聊聊/待聊点开——本 Task 先以内部 API 表达,端点在 Wave B 接)→ 同时仅 1(新锚顶旧锚,旧锚台账 released:replaced);解除三条件各一(结算/2 轮 unrelated/TTL 越 2h);重启恢复(persist→新实例→锚仍在,TTL 按 established_at 继续算);台账行断言。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_soul_engine.py -q` 回归 + ruff + mypy。

**Acceptance:** 生命周期 ≥7 用例全过;既有对话测试零回归。

### Task 2: 归属判断——提取调用扩展 + 防双计 + 锚处理器

**Files:** 修改 `src/openbiliclaw/llm/prompts.py`(提取 builder user 段增「当前锚」;输出契约增 `anchor` 字段;system 静态)、`src/openbiliclaw/soul/dialogue_insight_analyzer.py`(锚段注入与输出解析、白名单校验)、`src/openbiliclaw/soul/engine.py`(锚处理器:relation 五分支——support/contradict 投票、answer 结算疑惑、revise 走 reject+派生(复用 confirmed→门控通道)、unrelated 计数;candidates 关键词重叠防御;锚定轮跳过检索式 settles 复用既有 scope 规则);测试并入 `tests/test_dialogue_anchor.py` + `tests/test_soul_engine.py`。

**Steps:**

- [ ] Failing tests(五分支):support→假设置信+投票;contradict→负票;answer→疑惑 resolve(命中解读)+派生;revise→原假设 reject+修正假设生成(送 confirmed→门控排程);unrelated→streak+1、整轮内容走普通路径。
- [ ] Failing tests(防双计):归锚内容出现在 candidates → 关键词重叠防御丢弃+WARNING;锚定轮 settles 跳过断言;旁支 candidate(与锚无关)正常走快线。
- [ ] Failing tests(防御):anchor 字段缺失/越界 relation → 不结算、锚保持、WARNING;无锚时提取行为与现状逐字节一致(回放门)。
- [ ] Confirm FAIL → 实现 → PASS。
- [ ] Run `pytest tests/test_llm_prompts.py tests/test_soul_engine.py -q` + ruff + mypy;**Wave A 文档 gate**:soul.md 锚与归属小节、changelog。

**Acceptance:** 五分支+防双计三防线+防御 ≥12 用例;无锚回放字节门绿;invariance 清单绿。

---

## Wave B — durable 卡片与列表 API

### Task 3: scope="hypothesis" 卡片 turn + 结算端点 + 疑惑锚定多轮升级

**Files:** 修改 `src/openbiliclaw/api/app.py`(scope 白名单加 "hypothesis";卡片 turn 携带结构化 payload `card{kind, ref, title, evidence_refs, actions}`;新端点 `POST /api/chat/cards/{ref}/action`(action ∈ confirm|reject|defer,幂等,调 update_from_feedback / defer 语义,台账带 turn_id);confusion scope 升级:抛出提问即建锚,后续锚定轮走 Task 2 归属路径,原单轮情感分类器保留为锚处理器输入信号);测试 `tests/test_api_app.py`。

**Steps:**

- [ ] Failing tests:hypothesis 卡片 turn 创建(payload 完整)→ 前端可轮询取到;action 三种各一(confirm→假设 validated≥0.75+rebuild_pending;reject→≤0.35+pending;defer→冷却记录);重复 action 幂等(状态不劣化)。
- [ ] Failing tests:confusion 抛出→锚建立;多轮回复经归属路径结算(端到端:问→答→resolved);含糊回复→追问一次→再含糊→defer 计数。
- [ ] Confirm FAIL → 实现 → PASS;`pytest tests/test_api_app.py tests/test_confusion_lifecycle.py -q` 回归 + ruff + mypy。

**Acceptance:** 卡片/action/多轮 ≥9 用例;confusion 既有单轮用例改写为锚定语义后全绿。

### Task 4: 待聊列表 + 角标 + 抛出冷却

**Files:** 修改 `src/openbiliclaw/api/app.py`(`GET /api/chat/pending-confirmations`:高优先级假设+open 疑惑,含角标计数;`POST /api/chat/pending-confirmations/{ref}/open`:用户主动点开→立即产出卡片/提问 turn+建锚,**零冷却**);系统主动抛出调度(对话空闲间隙顺口 1 条:全局冷却 12h 持久化,校准注释;同对象 72h 既有);测试 `tests/test_api_app.py`。

**Steps:**

- [ ] Failing tests:列表内容与计数口径(高优先级过滤);用户主动 open 不受冷却(连开 3 条都成功);系统抛出后 12h 内不再主动抛(持久化,重启仍生效);72h 同对象冷却叠加生效。
- [ ] Confirm FAIL → 实现 → PASS;回归 + ruff + mypy;**Wave B 文档 gate**:api 端点文档、changelog。

**Acceptance:** 双轨冷却矩阵 ≥6 用例全过。

---

## Wave C — 前端(Task 5)

**Files:** `extension/popup/`(角标 badge、对话顶部待聊入口、卡片渲染四按钮+已结算态+依据展开、疑惑气泡标识)、`src/openbiliclaw/web/js/`(桌面 Web 同款);`extension/tests/`(卡片渲染/action 调用/角标单测);Playwright 用例(卡片点确认→已结算态;待聊列表点开→卡片入流)。

**Steps:**

- [ ] Failing tests(node --test):payload→卡片 DOM;action 点击→端点调用+乐观更新;角标计数渲染;降级(无 payload turn 正常文字显示)。
- [ ] Confirm FAIL → 实现 popup → 桌面 Web 同步 → PASS。
- [ ] `npm test && npm run typecheck && npm run build`;Playwright 两用例;**Wave C 文档 gate**:extension.md、changelog。

**Acceptance:** 前端用例全过 + build 干净;两端行为一致(同 payload 同渲染语义)。

---

## Wave D — CLI/迁移/端到端(Task 6)

**Files:** `src/openbiliclaw/cli.py`(`questions` 只读列表);洞察确认迁移(认知更新区改只读:popup/桌面 Web 移除确认按钮,数据端点保留);文档总核对;真实端到端。

**Steps:**

- [ ] `openbiliclaw questions` 输出高优先级列表(与 API 同口径)+ 测试。
- [ ] 认知更新区确认按钮移除(两端),旧端点保留兼容(废弃注记)。
- [ ] **真实端到端**(本机 8433 式隔离环境或分支测试后端):对话结算一条真实假设(卡片 confirm)+ 多轮澄清一条疑惑,台账链完整——记录在 PR。
- [ ] 文档总核对(spec Documentation obligations 逐项)进 PR;changelog。

**Acceptance:** CLI 用例 + 迁移回归 + 端到端记录齐备。

## Verification after merge

- 一周观察:角标出现频率(预期 0-3 常态)、系统主动抛出次数(≤2/天)、defer 率(>2/3 则延长冷却)、锚平均轮数与解锚原因分布(台账查询)。
- 回滚:各 Wave 独立提交 revert;卡片/列表为 API+前端增量,后端关闭抛出调度即静默。

## Explicitly out of scope

移动 Web 卡片(跟进版);系统推送;多锚;对话检索(v2);探针迁移。
