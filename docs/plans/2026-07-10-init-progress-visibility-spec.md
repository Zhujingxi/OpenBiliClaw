# Init Progress Visibility Spec — 初始化过程中用户始终能区分"正常慢"与"真卡死"

**Created:** 2026-07-10
**Scope:** 引导式初始化(guided init)的运行中过程可见性:阶段内细粒度进度、
心跳活性指示、预期管理文案;以及安装脚本复用 cookie 的即时校验。涉及
`runtime/init_coordinator.py`、`cli.py` (`run_guided_init`)、
`soul/preference_analyzer.py` / `soul/engine.py`、`api/app.py` / `api/models.py`、
三个 GUI 进度面(extension popup / desktop web / setup 向导)与 CLI 输出、
`scripts/agent_bootstrap.py` + `scripts/install.sh`。
**Out of scope:** 阶段级断点续跑、LLM 调用硬超时与自动重试、日志导出/诊断包、
移动 web(无初始化 UI,init 只能在本机发起,`local_only` 已拦)、
进度语义之外的任何 prompt / LLM 行为改动。

## Goal

**现状成本**:初始化全程 2–5 分钟里,GUI 进度条只会停在 5 个静止刻度
(13% / 38% / 63% / 88% / 100%),因为百分比是前端按"已完成阶段数 + 运行中
半格"折算的(每阶段 25%)。其中 38% = 阶段 2「分析偏好」运行中——一次分片
LLM 批处理(eta ~180s),期间无任何状态更新。用户无法区分"正常慢"和
"真卡死",产生大量"卡在 38%"的困惑反馈。另有一个静默失败:安装脚本
`--reuse-from` 复用旧 B 站 cookie 不做校验(`scripts/install.sh:432-443` 自己
承认),过期 cookie 要等到 init 跑到阶段 1 才报 `empty_history`。

**目标结果**:

1. 初始化运行期间,`/api/init-status` 暴露的进度信号(阶段子进度或心跳时间戳)
   **每 ≤60s 至少推进一次**;前端进度条在阶段 2(≥2 个分片时)每完成一个分片
   前进一次,不再整段静止。
2. 三个 GUI 面(popup / desktop web / setup 向导)在进度停滞 >90s 时显示
   明确的"仍在进行中/耗时超出预期"活性文案,而不是静止的百分比。
3. 复用的 B 站 cookie 若已失效,安装脚本状态块显式报告
   `bilibili.cookie (stale)`,而不是 `complete`。

**验证命令**:
`pytest tests/test_init_coordinator.py tests/test_preference_analyzer.py tests/test_web_guided_init.py`;
`cd extension && npm run test`(init-control 用例);
手动:一次真实 `openbiliclaw init` / setup 向导观察进度推进与活性行。

## Design invariants (MUST hold in every phase)

1. **单写者不破**:所有新增进度信号(阶段子进度、心跳)必须经
   `InitCoordinator._write`(持 `_write_lock`,`sequence` 严格递增)落库;
   任何其他组件不得直接写 `init_runs`。验证:`tests/test_init_coordinator.py`
   断言并发 `stage_progress` / `touch` 下 sequence 单调。
2. **进度单调不回退**:同一 `run_id` 内,前端渲染的百分比永不下降(以
   run_id 为 key 做 client 端 clamp)。验证:`extension/tests/init-control.test.ts`
   喂乱序/回退的 status 序列断言 pct 非降。
3. **向后兼容(可加性)**:`stages[]` 新增字段(`progress`、`eta_seconds`)与
   status 新增字段(`last_activity`)全部 optional;旧版扩展 popup 读新后端、
   新前端读缺字段的旧 status 都不得报错。验证:前端单测喂无新字段的
   status;`api/models.py` 字段带默认值。
4. **Prompt-cache 约定不破**:进度回调只观测分片完成,不改变任何 prompt
   内容、分片方式或 `json.dumps` 序列化。验证:
   `tests/test_llm_prompts.py::test_prompt_builder_system_messages_are_call_invariant`
   照常通过,`soul/prompts` 无 diff。
5. **心跳不淹没 SSE**:`touch()` 只更新 DB(`sequence` + `updated_at`),
   **不**发布 `init_progress` 事件——活性由前端已有的 3s 轮询读出,SSE 只留给
   真实阶段/子进度变化。验证:coordinator 单测断言 touch 不调用 event_hub。
6. **四面契约**:进度可见性改动必须同时覆盖 extension popup、desktop web、
   setup 向导、CLI 四个初始化面;移动 web 无初始化 UI,本 spec 显式排除。

## Current diagnosis

### D1. 百分比是前端阶段刻度折算,90% 的时间静止

后端 coordinator 只写 4 个阶段状态(`runtime/init_coordinator.py:28-29`
`_TOTAL_STAGES=4`,`_STAGE_LABELS`),不产出百分比。两处前端用同一公式
`((doneCount + (running ? 0.5 : 0)) / total) * 100`:
`web/desktop/assets/js/app.js:944`、`extension/popup/popup-init-control.js:233-235`。
映射:阶段 1 运行中=13%,阶段 2 运行中=38%,阶段 3+4 并发=63%,仅阶段 4=88%。
阶段 2 是一次 `soul_engine.analyze_events(events, event_chunk_size=…)` 长调用
(`cli.py:6136-6143`,eta 180s),期间无任何 stage 写入 → 38% 静止数分钟。
阶段 1 内部逐平台等扩展采集(串行,单平台最长 300s,`cli.py:5851-6029`)同样
钉在 13%。已确认为事实(代码直读)。

### D2. 分片分析内部有天然进度点,但没有暴露

`PreferenceAnalyzer._analyze_events_chunked`
(`soul/preference_analyzer.py:370-412`)把事件切成 N 个独立分片并发调 LLM,
每个分片完成就是一个天然进度事件,但当前无回调口。上层链路:
`SoulEngine.analyze_events`(`soul/engine.py:266`)→
`PreferenceAnalyzer.analyze_events`(`preference_analyzer.py:117`)。
现有测试:`tests/test_preference_analyzer.py` 覆盖分片行为,无进度断言。

### D3. 无活性信号,前端无停滞检测

`init_runs` 表已有 `updated_at`(`storage/database.py:7169`,每次
`update_init_run` 刷新,`database.py:7215`),但 `get_status()`
(`init_coordinator.py:252-279`)不吐出;三个 GUI 面(popup
`extension/popup/popup.js:954` 3s 轮询、desktop `app.js:1189` 3s 轮询 + SSE、
setup 向导 `web/setup/index.html:879-905`)都没有"某阶段长时间不推进"的
告警或"仍在进行中"提示。CLI 反而有:`_run_with_progress`(`cli.py:372-399`)
周期性打印 eta 倒数——GUI 没有等价物。已确认为事实。

### D4. 复用 cookie 不校验,过期后静默到 init 才炸

`scripts/agent_bootstrap.py:1930-1979` `reuse_config_secrets` 按文件存在性
复制 cookie / key;"missing" 判定只看存在性(`agent_bootstrap.py:3017,3049`)。
install.sh 输出块只能打免责声明(`install.sh:432-443`)。而后端起来后
`/api/init-status` 的 `prerequisites.bilibili_check` 本来就做**真实**
`validate_cookie` 探测(`runtime/init_prereqs.py:152-207`)——校验能力已存在,
只是 bootstrap 状态汇总没有消费它。已确认为事实。

### D5. 预期管理文案只有 CLI 有

CLI 开场打印"预计 2–5 分钟"+逐步 eta;GUI 三面在 idle 清单和运行态都没有
总时长预期或阶段典型耗时。阶段 eta 数值散落在 `cli.py`(180/70/300)硬编码,
无单一来源。已确认为事实。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | coordinator 子进度/心跳/eta 写入口 + status/模型透出 | **MUST** | 一切前端改动的数据前提;守护单写者与兼容性两条不变量 |
| 1 | 阶段 2 分片进度 + 阶段 1 逐源进度(生产者) | **MUST** | 直接消灭"卡在 38%/13%"的主诉 |
| 2 | 三 GUI 面进度公式升级 + 活性/停滞文案 + 预期文案 | **MUST** | 用户可见的全部收益在这一层兑现 |
| 3 | 复用 cookie 即时校验(bootstrap + install.sh) | RECOMMENDED | 堵最坑的静默失败;独立可 ship |
| 4 | CLI 分片进度打印复用同一回调 | RECOMMENDED | 顺手对齐第四面,改动极小 |

依赖:1 依赖 0;2 依赖 0(可与 1 并行开发,联调需 1);3、4 独立。
**Wave A** = Phase 0+1(后端一个 PR 可 ship,GUI 未升级也不受影响——字段可加);
**Wave B** = Phase 2+4(前端 + CLI);**Wave C** = Phase 3(安装链路,独立 PR)。
任一 Wave 结束都是安全停点。

## Phase designs

### Phase 0 — Coordinator 子进度 / 心跳 / eta 数据面

**接口(全部走 `_write`,遵守不变量 1/3/5):**

- `_initial_stages()` 每个 stage 增加 `"eta_seconds"`:
  `{1: 90, 2: 180, 3: 70, 4: 120}`(模块级常量 `_STAGE_ETAS`;1/4 取典型值,
  2/3 迁移自 `cli.py:6142,6198` 的现值——calibration 来源:现网 init 实测的
  CLI eta 常量,provider 换代后需复核)。
- `async def stage_progress(run_id, stage, *, done: int, total: int, note: str | None)`:
  向对应 stage dict 写 `"progress": {"done": d, "total": t, "note": s}`,
  clamp `0 ≤ done ≤ total`,`total ≤ 0` 时忽略写入;发布 `init_progress` 事件
  (event_extra 携带 progress)。同 stage 的 `stage_done` 清掉 progress。
- `async def touch(run_id)`:空 `_write`(只 bump sequence + `updated_at`),
  **不带 event_type**(不变量 5)。
- `get_status()` 增加 `"last_activity"`:取 `run["updated_at"]`(无 run 时 `""`)。
- `api/models.py`:`InitStageOut` 增加 optional `progress`(嵌套模型
  `InitStageProgressOut {done:int,total:int,note:str}`)与 `eta_seconds:int|None`;
  `InitStatusOut` 增加 `last_activity: str = ""`。
- `api/app.py` `_run_guided_init_wrapper`(`app.py:2267-2342`):启动一个
  30s 周期的 heartbeat task(`coordinator.touch(run_id)`),`finally` 里取消。
  这保证即使阶段 2 单个 LLM 调用挂 3 分钟,`last_activity` 也 ≤30s 新鲜
  (Goal 指标 1 的兜底)。

**错误行为**:progress 写入失败不影响 init 主流程(coordinator `_write`
已有 suppress 语义的事件发布;progress 本身随 `_write` 落库,失败即随
`update_init_run` 异常冒泡——与现状一致)。heartbeat task 内部异常吞掉并
log WARNING(心跳挂了不能杀 init)。

**测试**:`tests/test_init_coordinator.py` 新增——stage_progress 落库/清除、
clamp、touch 不发事件、sequence 单调、get_status 透出 last_activity/eta。

**验收门**:上述单测全绿;`InitStatusOut` 对旧字段零改动
(`pytest tests/test_web_guided_init.py` 回归通过)。

### Phase 1 — 进度生产者(阶段 2 分片 + 阶段 1 逐源)

- **回调线程化**:`PreferenceAnalyzer.analyze_events` /
  `_analyze_events_chunked` 增加
  `progress_callback: Callable[[int, int], Awaitable[None]] | None = None`,
  每个分片完成(含重试/降级路径计入完成)后 `await progress_callback(done, total)`;
  回调异常吞掉并 log WARNING(观测者不得杀分析)。`SoulEngine.analyze_events`
  (`engine.py:266`)透传同名参数。**不触碰 prompt 构造**(不变量 4)。
- **阶段 2 接线**:`run_guided_init`(`cli.py:6130-6144`)传入回调,映射为
  `coordinator.stage_progress(run_id, 2, done=d, total=t, note=f"第 {d}/{t} 批")`;
  CLI 路径(coordinator 为 None)见 Phase 4。
- **阶段 1 逐源进度**:`run_guided_init` 阶段 1 内,每个数据源采集
  开始前调 `stage_progress(1, done=已完成源数, total=选中源数, note="正在采集 <平台名>")`
  (B 站拉取算第一个源;源边界即 `cli.py:5831/5851/5884/5919/5952/5985/6004`
  各 collect 调用处)。
- 阶段 3/4 不加生产者(单次 LLM 调用无天然进度点),靠 eta + 心跳。

**测试**:`tests/test_preference_analyzer.py` 断言回调按分片次数被调、
(done,total) 序列正确、回调抛异常不影响结果;`tests/test_web_guided_init.py`
用 stub 引擎断言阶段 2 期间 status 里出现 progress 且 done 递增、阶段 1
期间 note 随源切换。

**验收门**:stub 化 init 全程中,status 序列里阶段 2 的 `progress.done`
从 0 递增到 total,阶段转换外无 sequence 回退。复现:
`pytest tests/test_web_guided_init.py -k progress`。

### Phase 2 — 三 GUI 面:公式升级 + 活性/停滞 + 预期文案

对 `extension/popup/popup-init-control.js`、
`web/desktop/assets/js/app.js`、`web/setup/index.html` 三处的进度视图做同构升级
(共享逻辑无法下沉——三面技术栈不同,以 popup-init-control 的纯函数为
参考实现,其余两面镜像;这是 CLAUDE.md 四面契约允许的例外,需在 PR 里注明):

- **阶段内分数**:运行中 stage 的贡献从固定 0.5 改为
  `fraction = progress.total > 0 ? min(0.95, progress.done / progress.total)
  : min(0.95, 1 - exp(-elapsed / eta_seconds))`,其中 `elapsed` 为该 stage
  首次被观察到 running 的 client 时刻起算(per run_id 记录),`eta_seconds`
  读 stage 字段、缺省回退 0.5 常量(不变量 3)。
  `pct = round(((doneCount + running ? fraction : 0) / total) * 100)`,
  并发 3+4 时 fraction 取两 running stage 的均值。
- **单调 clamp**:per run_id 记录 `maxPctSeen`,渲染值 = max(计算值, maxPctSeen)
  (不变量 2)。
- **子进度文案**:运行行显示 note:`2/4 分析偏好 · 第 3/8 批(46%)`。
- **活性/停滞**:由 `last_activity` 算 `stale_seconds`;`>90s` 时进度行下方
  追加 amber 提示:「后台已 N 分钟没有新进展,可能是 AI 服务响应缓慢——
  可以继续等待,或取消后重试。」;`≤90s` 常态显示「● 进行中」。90s 阈值
  依据:心跳周期 30s × 3 次容错(calibration 来源:本 spec Phase 0 设计值,
  改心跳周期须同步改此阈值)。
- **预期管理**:idle 清单尾部与开始按钮附近加一行
  「整个过程通常需要 2–5 分钟,期间可离开此页面,进度会保留。」;运行中
  stage 行加「本阶段通常约 X 分钟」(X 由 eta_seconds 折算,向上取整到 0.5 分钟)。
- 保留现有两个覆盖态(`pct:95` 等首轮池、embedding 下载借位)不动。

**测试**:`extension/tests/init-control.test.ts` 新增——有/无 progress 字段的
pct 计算、单调 clamp、stall 文案阈值、旧 status(无新字段)不炸;
desktop/setup 由现有 python 端 web 测试(`tests/test_desktop_web_*.py` 模式)
加字符串级断言(预期文案、stall 文案存在)。

**验收门**:`cd extension && npm run test` 全绿;喂 20 步模拟 status 序列
(含乱序、字段缺失、stage 2 分片推进)断言 pct 严格非降且终值 100。

### Phase 3 — 复用 cookie 即时校验(独立可 ship)

- `scripts/agent_bootstrap.py`:在后端健康检查通过、且本次 run 复用了
  `bilibili.cookie` / `data/bilibili_cookie.json`(`reuse_config_secrets`
  summary 里有记录)时,请求 `/api/init-status` 并读
  `prerequisites.bilibili_check`;`not ok` → 状态汇总的 `missing` 追加
  `bilibili.cookie (stale — reused cookie failed live validation)`,
  最终 status 从 `complete` 降级为 `needs_secrets`。探测复用现成
  prereq(不变量:不新增第二条校验路径)。
- `scripts/install.sh` / `install.ps1` 状态块:`missing` 含 stale cookie 时,
  打印明确文案「复用的 B 站 Cookie 已失效,请重新登录 B 站后由扩展同步,
  或按下述步骤手动更新」,替换现在的泛化免责声明分支(`install.sh:432-443`
  保留给"后端未起、无法校验"的场景)。

**测试**:bootstrap 侧对 init-status 响应打桩,断言 stale → needs_secrets;
install.sh 无测试基建,靠 `bash -n` + 人工过一遍三种状态输出。

**验收门**:打桩场景下 `needs_secrets` + missing 条目精确匹配;
真实 `REUSE_FROM=<过期 cookie 的旧安装> ./install.sh` 手测一次,记录输出。

### Phase 4 — CLI 分片进度打印

`run_guided_init` CLI 路径(coordinator 为 None)给 `analyze_events` 传一个
console 回调:`console.print(f"  [dim]分析偏好:第 {d}/{t} 批完成[/dim]")`,
与 `_run_with_progress` 的 eta 倒数并存。零新接口,复用 Phase 1 回调。

**测试**:随 Phase 1 单测覆盖回调机制;CLI 输出不做快照测试(与现有惯例一致)。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 0+1 | 阶段 2 期间 status 进度信号推进间隔从 ~180s(0 次更新)降到每分片一次(典型 8 分片 → ~20s 一次);心跳保证任意时刻 `last_activity` 新鲜度 ≤30s |
| Phase 2 | "卡在 38%"类困惑的直接消除:38% 变为随分片推进的活值 + 停滞>90s 有明确文案;三面同步 |
| Phase 3 | 复用过期 cookie 从"init 跑 30s 后 empty_history"提前到安装完成时即报 `needs_secrets` |

## Documentation obligations

- `docs/modules/init.md` — 阶段子进度/心跳/eta 字段、`/api/init-status` 响应
  变化(implemented features 表 + public API)
- `docs/modules/soul.md`(若存在 analyzer 公共 API 章节)— `analyze_events`
  新增 `progress_callback` 参数
- `docs/changelog.md` — 当前版本块下加 bullet(过程可见性 + cookie 校验各一条)
- `docs/agent-install.md` — Phase 3 的 stale-cookie 状态语义(HARD RULE 4 关联段)
- `scripts/install.sh` post-install 输出 — Phase 3 文案(即代码本身,列此提醒同步 ps1)
- 架构图 / spec §3 / README 图:**不触发**(无跨模块新依赖、无新模块);
  README highlights:**不触发**(随下一次发版再议,本改动非 4-bullet 级)
- CLI / config 文档:**不触发**(无新命令、无新 config 字段)
