# Init Progress Visibility — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-10-init-progress-visibility-spec.md`](./2026-07-10-init-progress-visibility-spec.md)
> **Status:** draft v1,未 review
> **Execution order:** Task 1 → 2 → 3 → 4(Wave A,后端一个 PR 可 ship)→
> Task 5 → 6 → 7(Wave B,前端三面 + CLI)→ Task 8(Wave C,安装链路,独立 PR)→ Task 9(docs)
> **Tech:** Python 3.11+,解释器用 `.venv/bin/python`(`python`/`python3` 无依赖包);
> 测试 `.venv/bin/python -m pytest <file>`;lint `ruff check src/ tests/`;
> format `ruff format src/ tests/`;类型 `mypy src/`;
> 扩展侧 `cd extension && npm run test && npm run typecheck`

**Invariants that MUST hold — re-read before each task:**

- **单写者不破**:子进度与心跳全部经 `InitCoordinator._write`(持锁,sequence 严格递增);任何其他组件不得直接写 `init_runs`。
- **进度单调不回退**:同一 run_id 内前端渲染 pct 永不下降(client 端按 run_id clamp)。
- **向后兼容**:`stages[]` 与 status 的新字段全部 optional 带默认;旧 popup 读新后端、新前端读旧 status 都不炸。
- **Prompt-cache 约定不破**:进度回调只观测分片完成,不改 prompt 内容/分片方式/序列化;`test_prompt_builder_system_messages_are_call_invariant` 必须照常通过。
- **心跳不淹没 SSE**:`touch()` 只落库,不发布 `init_progress` 事件。
- **四面契约**:popup / desktop web / setup 向导 / CLI 四面同步;移动 web 显式排除(无 init UI)。

**并发会话纪律(本仓库多会话共用工作区)**:每个 task 提交前重拍 `git status`
快照,`git add` 只加本 plan 列出的精确路径,绝不 `git add -A`;
`src/openbiliclaw/api/app.py`、`api/models.py` 当前挂着其他会话的未提交改动,
动这两个文件前先确认对方已收尾,否则用 HEAD+自编辑按 hunk 重建 index。

### Task 1: Coordinator 子进度 / 心跳 / eta 数据面

**Files:** modify `src/openbiliclaw/runtime/init_coordinator.py`;
test `tests/test_init_coordinator.py`。

**Interfaces:** Consumes: 现有 `_write` / `update_init_run` / `stages_json`。
Produces: `_STAGE_ETAS = {1: 90, 2: 180, 3: 70, 4: 120}`(带 calibration 注释:
迁移自 cli.py eta 常量,provider 换代需复核);`_initial_stages()` 各 stage 加
`eta_seconds`;`async stage_progress(run_id, stage, *, done, total, note=None)`
(clamp `0≤done≤total`,`total≤0` 忽略,写 stage dict 的
`progress={done,total,note}`,发布 `init_progress`);`stage_done` 清除该 stage
的 progress;`async touch(run_id)`(空 `_write`,无 event_type);
`get_status()` 增加 `last_activity`(取 `run["updated_at"]`,无 run 时 `""`)。

**Steps:**

- [ ] 在 `tests/test_init_coordinator.py` 写失败测试:stage_progress 落库+事件、越界 clamp、total≤0 忽略、stage_done 清 progress、touch 落库但不调 event_hub、并发 stage_progress/touch 下 sequence 严格递增、get_status 透出 last_activity 与 eta_seconds。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_init_coordinator.py`,确认新用例按预期 FAIL(缺方法/字段)。
- [ ] 实现上述接口,全部走 `_write`。
- [ ] 重跑同命令确认 PASS 无警告。
- [ ] `ruff check src/openbiliclaw/runtime/ tests/test_init_coordinator.py && mypy src/`。

**Acceptance:**

- Numeric gate:100 次交错 `stage_progress`+`touch` 后 sequence 恰为初值+100 且单调(测试内断言);违背即单写者被破坏。
- Reproduce with `.venv/bin/python -m pytest tests/test_init_coordinator.py -q`;结果记入 PR。

### Task 2: API 模型与 heartbeat task

**Files:** modify `src/openbiliclaw/api/models.py`(`InitStageProgressOut` 新模型;
`InitStageOut` 加 optional `progress`、`eta_seconds`;`InitStatusOut` 加
`last_activity: str = ""`)、`src/openbiliclaw/api/app.py`
(`_run_guided_init_wrapper` 内启动 30s 周期 `coordinator.touch(run_id)`
heartbeat task,内部异常吞掉 log WARNING,`finally` 取消);
test `tests/test_web_guided_init.py`。

**Interfaces:** Consumes: Task 1 的 coordinator 接口。Produces:
`/api/init-status` 响应新字段(全 optional)。

**Steps:**

- [ ] 先确认 `api/app.py` / `api/models.py` 上其他会话的未提交改动已收尾(见并发会话纪律)。
- [ ] 写失败测试:stub run 期间 init-status 响应含 `last_activity` 且随 touch 刷新;stage 带 progress 时响应原样透出;无新字段的旧 stages_json 反序列化不炸。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_web_guided_init.py -k "progress or activity"` 确认 FAIL。
- [ ] 实现模型字段与 heartbeat task。
- [ ] 重跑确认 PASS;再跑全文件 `pytest tests/test_web_guided_init.py` 回归。
- [ ] `ruff check src/ tests/ && mypy src/`。

**Acceptance:**

- Numeric gate:stub init 挂起 65s(fake clock)场景下,`last_activity` 距今 ≤30s(即至少 2 次心跳落库);失败=心跳兜底不成立,Goal 指标 1 破。
- Reproduce with `.venv/bin/python -m pytest tests/test_web_guided_init.py -q`。

### Task 3: 分析器进度回调线程化

**Files:** modify `src/openbiliclaw/soul/preference_analyzer.py`
(`analyze_events` / `_analyze_events_chunked` 加
`progress_callback: Callable[[int, int], Awaitable[None]] | None = None`,
每分片完成——含重试/降级路径——`await` 一次,回调异常吞掉 log WARNING)、
`src/openbiliclaw/soul/engine.py`(`analyze_events` 透传);
test `tests/test_preference_analyzer.py`。

**Interfaces:** Consumes: 现有分片实现。Produces: `(done, total)` 回调序列。
**不触碰任何 prompt 构造与序列化**。

**Steps:**

- [ ] 写失败测试:N 分片 → 回调恰 N 次、done 严格递增至 total;单发路径(不分片)回调 0 次或 (1,1)(选定其一并固化);回调抛异常不影响分析结果且有 WARNING。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_preference_analyzer.py -k callback` 确认 FAIL。
- [ ] 最小实现 + engine 透传。
- [ ] 重跑确认 PASS;跑 `pytest tests/test_preference_analyzer.py tests/test_llm_prompts.py` 回归(prompt-cache 不变量)。
- [ ] `ruff check src/ tests/ && mypy src/`。

**Acceptance:**

- Numeric gate:8 分片 stub 下回调序列 == [(1,8)…(8,8)],乱序/缺失即分片计数有并发 bug。
- Reproduce with `.venv/bin/python -m pytest tests/test_preference_analyzer.py -q`。

### Task 4: run_guided_init 进度生产者接线

**Files:** modify `src/openbiliclaw/cli.py`(`run_guided_init`:阶段 1 各源
collect 边界调 `stage_progress(1, done=完成源数, total=选中源数, note="正在采集 <平台>")`;
阶段 2 传回调映射到 `stage_progress(2, done, total, note=f"第 {d}/{t} 批")`;
CLI 路径 coordinator=None 时改传 console 打印回调
`分析偏好:第 {d}/{t} 批完成`——即 spec Phase 4,一并做掉);
test `tests/test_web_guided_init.py`。

**Interfaces:** Consumes: Task 1 coordinator 接口 + Task 3 回调。
Produces: 运行期 status 里阶段 1/2 的 progress 序列。

**Steps:**

- [ ] 写失败测试(stub 引擎/采集器):阶段 1 期间 note 随源切换且 done 递增;阶段 2 期间 progress.done 从 0 递增到 total;阶段完成后该 stage 无 progress 残留。
- [ ] 跑 `.venv/bin/python -m pytest tests/test_web_guided_init.py -k stage_progress` 确认 FAIL。
- [ ] 实现接线(注意:阶段 1 的"选中源数"= include_* 为真的源计数,B 站算第一个)。
- [ ] 重跑确认 PASS;全量 `pytest tests/test_web_guided_init.py tests/test_web_guided_init_e2e.py` 回归。
- [ ] `ruff check src/ tests/ && mypy src/`。

**Acceptance:**

- Numeric gate:stub 全程 status 快照序列中 sequence 严格递增、pct 等效值(按前端公式折算)非降;任何回退即 stage 3/4 并发写序被破坏。
- Reproduce with `.venv/bin/python -m pytest tests/test_web_guided_init.py -q`。

### Task 5: 扩展 popup 进度视图升级(参考实现)

**Files:** modify `extension/popup/popup-init-control.js`(`initProgressView`:
阶段内 fraction = `progress.total>0 ? min(0.95, done/total) :
min(0.95, 1-exp(-elapsed/eta))`,eta 缺省回退 0.5 常量;并发 running 取均值;
per-run_id 单调 clamp;子进度 note 拼入 stageLabel;新增 `stalenessView(status, nowMs)`
→ `{fresh: bool, staleSeconds, text}`,>90s 出 amber 停滞文案)、
`extension/popup/popup.js`(渲染 stall 行与「通常需要 2–5 分钟」idle 文案);
test `extension/tests/init-control.test.ts`。

**Interfaces:** Consumes: `/api/init-status` 新字段(容忍缺失)。
Produces: 纯函数视图,desktop/setup 两面照此镜像。

**Steps:**

- [ ] 写失败测试:有 progress 时 pct 随 done 推进;无 progress 时 elapsed 伪进度渐进且封顶 0.95 折算;乱序/回退 status 序列下 pct 非降;旧 status(无新字段)行为与现状一致(38% 等刻度);stall 阈值 90s 前后文案切换。
- [ ] 跑 `cd extension && npm run test` 确认新用例 FAIL。
- [ ] 最小实现(elapsed 起点 = 该 stage 首次被观察到 running 的 client 时刻,存 module 级 per-run_id map)。
- [ ] 重跑 `npm run test` 确认 PASS;`npm run typecheck`。
- [ ] 手动:加载扩展对着真实后端跑一次 init,确认进度活动、stall 文案不误触发。

**Acceptance:**

- Numeric gate:20 步模拟序列(含分片推进、字段缺失、乱序)pct 严格非降、终值 100;任一回退 = clamp 失效。
- Reproduce with `cd extension && npm run test`;结果记入 PR。

### Task 6: Desktop web 同构升级

**Files:** modify `src/openbiliclaw/web/desktop/assets/js/app.js`
(`initProgressView` 镜像 Task 5 公式与 clamp;`updateInitOnboardingStatus`
渲染 note / stall 行;idle 清单与开始按钮旁加「通常需要 2–5 分钟,可离开
此页面」;运行 stage 行加「本阶段通常约 X 分钟」);
test 参照现有 `tests/test_desktop_web_*.py` 模式新增/扩展一个用例文件
(字符串级断言新文案与公式要素存在)。

**Steps:**

- [ ] 写失败测试(python 侧字符串断言:app.js 含 clamp 逻辑标识、stall 文案、预期文案)。
- [ ] 跑对应 pytest 文件确认 FAIL。
- [ ] 镜像实现(保留 `pct:95` 首轮池与 embedding 借位两个覆盖态不动)。
- [ ] 重跑确认 PASS。
- [ ] 手动:`openbiliclaw serve-api` + 浏览器走一次 init 看三种态;**注意 serve-api 路由固定于启动时,改后端后必须重启**(JS 是活的,后端不是)。

**Acceptance:**

- 手动验收:真实 init 全程截图三帧(分片推进中 / stall 提示 / 完成),记入 PR;pytest 断言全绿。
- Reproduce with `.venv/bin/python -m pytest tests/test_desktop_web_init_progress.py -q`(文件名以实际创建为准)。

### Task 7: Setup 向导同构升级

**Files:** modify `src/openbiliclaw/web/setup/index.html`(进度渲染段
`index.html:879-905` 附近,镜像 Task 5 公式/clamp/note/stall/预期文案)。

**Steps:**

- [ ] 镜像实现(向导是单文件内联 JS,无独立测试基建——用 Task 6 同款 python 字符串断言覆盖关键要素)。
- [ ] 跑对应 pytest 确认 PASS。
- [ ] 手动:桌面形态首启落 `/setup/` 走一次完整向导。

**Acceptance:**

- 手动验收:向导内进度随分片推进 + stall 文案可见,记入 PR。
- Reproduce with 手动流程 + `.venv/bin/python -m pytest tests/ -k setup -q`。

### Task 8: 复用 cookie 即时校验(独立 PR)

**Files:** modify `scripts/agent_bootstrap.py`(后端健康后、若本 run 复用了
cookie:GET `/api/init-status` 读 `prerequisites.bilibili_check`,not ok →
missing 追加 `bilibili.cookie (stale — reused cookie failed live validation)`,
status 降级 `needs_secrets`)、`scripts/install.sh` + `scripts/install.ps1`
(missing 含 stale cookie 时打印明确的"Cookie 已失效,请重新登录后由扩展
同步"文案;原免责声明分支保留给后端未起场景);
test:bootstrap 侧新增/扩展打桩测试(如 `tests/test_agent_bootstrap.py`,
以实际现存文件为准)。

**Steps:**

- [ ] 写失败测试:打桩 init-status 返回 bilibili_check not ok → 汇总为 needs_secrets 且 missing 条目精确匹配;bilibili_check ok → 不降级。
- [ ] 跑对应 pytest 确认 FAIL。
- [ ] 实现 bootstrap 消费逻辑 + 两个安装脚本文案分支;`bash -n scripts/install.sh` 语法校验。
- [ ] 重跑确认 PASS。
- [ ] 手动:`REUSE_FROM=<带过期 cookie 的旧安装> ./scripts/install.sh` 真跑一次,记录状态块输出。

**Acceptance:**

- Numeric gate:打桩两场景(stale/valid)状态判定 2/2 正确;stale 场景 status ≠ complete。
- Reproduce with `.venv/bin/python -m pytest tests/ -k bootstrap -q` + 手测输出贴 PR。

### Task 9: 文档同步

**Files:** modify `docs/modules/init.md`(子进度/心跳/eta 字段与
init-status 响应变化)、`docs/modules/soul.md`(analyze_events 新参数,
若该文档有 public API 节)、`docs/changelog.md`(当前版本块两条 bullet:
过程可见性、cookie 校验)、`docs/agent-install.md`(stale-cookie 状态语义)。

**Steps:**

- [ ] 按 spec「Documentation obligations」逐项更新。
- [ ] 自查 CLAUDE.md pre-merge checklist:架构图/CLI/config/README highlights 均不触发,PR 描述里注明。

**Acceptance:**

- checklist 全勾;`git diff --stat docs/` 覆盖上述四个文件。

## Verification after merge

- 观察面:发版后 1 周,GitHub issues / 反馈渠道中"卡在 X%""是不是卡死了"
  类新增反馈数;目标:相对前一周期显著下降(基线:近两周此类反馈为主要
  init 投诉)。Owner:white。
- 技术观察:一次真实全新安装(git 模式)+ 一次 Docker `docker exec … init`
  + 一次桌面包首启,确认三形态进度推进与 stall 文案(对齐"每个安装形态都要
  验证"的历史教训);CLI 跑一次确认分片打印。
- 回滚触发:任一 GUI 面出现 pct 回退 / stall 误报(后台明明在推进却持续
  amber)/ 旧扩展兼容性报错 → revert Wave B 前端提交(Wave A 字段可加,
  无需回滚)。

## Explicitly out of scope

- 阶段级断点续跑(失败重试仍全量从阶段 1 开始)
- LLM 调用硬超时与自动重试(挂起仍靠用户取消)
- 日志导出 / 诊断包入口
- 移动 web(无初始化 UI)
- eta 常量的动态校准(维持静态常量 + calibration 注释)
