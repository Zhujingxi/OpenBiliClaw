# 平台来源接入契约统一 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-18-source-auth-contract-spec.md`](./2026-07-18-source-auth-contract-spec.md)
> **Status:** Rev 1，未评审。基线 `main@5d8d8889`，工作分支 `refactor/source-auth-contract`。
> **Execution order:** Task 1 → 2 →(3 → 4 → 5)→ {6 → 7} → {8 → 9} → 10 → 11 → {12, 13}。
> Wave A = Task 1–7（零破坏，可独立交付并停止）；Wave B = Task 8–11；Wave C = Task 12–13。
> **Tech:** Python 3.12.12，解释器在**主仓** `/Users/white/workspace/OpenBiliClaw/.venv/bin/python`
> （`python` / `python3` 无依赖也无 pytest）。**worktree 里没有 `.venv`**，且该 venv 是 editable 安装、
> `.pth` 指向主仓 src —— 在 worktree 跑任何命令都必须 `export PYTHONPATH=$PWD/src`，否则测的是主仓代码。
> 测试 `.venv/bin/python -m pytest <file> -x -q`；lint `.venv/bin/python -m ruff check src/ tests/`；
> 格式 `.venv/bin/python -m ruff format src/ tests/`；类型 `.venv/bin/python -m mypy src/`；
> 扩展 `cd extension && npm run typecheck && npm run test`。

**Invariants that MUST hold — re-read before each task:**

- **I1 单一真相源**：任一平台「凭据在不在」只由一个 resolver 回答；`api/` 与 `runtime/` 内不得旁路直读 `cfg.<platform>.cookie`。
- **I2 语义正交**：`enabled` / `auth_required` / `credential` / `verification` 四维互不覆盖，任一维取值变化不改变其他三者。
- **I3 证据强度必须诚实**：`verify_method` 如实反映结论来源；`live_probe` 仅在真的出网时使用；**禁止给无法离线验证的平台（douyin）编造验证结果**。
- **I4 前端零 per-platform 分支**：三端渲染不得出现 `key === "<platform>"` 形式的展示分支。
- **I5 写入即校验**：能校验的必须校验并拒绝落盘；不能的必须显式声明未校验；`POST` 与 `PUT /api/config` 两条路径强度一致。
- **I6 四端契约**：插件 popup / 桌面 Web / 移动 Web / **setup 引导页**四端一致，共享逻辑在后端；有意排除需写进 PR 描述。
- **I7 语义属性不得用语法代理判定**：不许靠变量名 / 路径词根 / 路由名 / 目录位置判断"是不是凭据写入""验不验证"。门可以用代理，结论不行——spec 初版四条诊断都栽在这里。

---

## Wave A — 零破坏，做完可停

### Task 1: 指标脚本（量化门）

**Files:** 新增 `scripts/source_contract_metrics.py`。

**Interfaces:** Consumes: 仓库源码。Produces: stdout 六项指标 + `--check` 模式非零退出。

**Steps:**

- [ ] 实现六项统计：`sources_status()` 函数体行数、桌面 `app.js` per-platform 相等判断数、前端状态 map 副本数、有 verify 端点的平台数、凭据写入端点命名形态数、承载平台源设置的前端数。
- [ ] 运行 `.venv/bin/python scripts/source_contract_metrics.py`，确认输出 `424 / 1 / 2 / 0 / 4 / 2`（与 spec Goal 表左列一致）。
- [ ] 加 `--check` 模式：读取脚本内的目标阈值表，超标退出码 1。
- [ ] 运行 lint 与 format。

**Acceptance:**

- 数值门：六项输出与 spec Goal 表左列**逐项相等**；任一项不符说明基线已漂移，必须先更新 spec 再继续。
- 复现：`.venv/bin/python scripts/source_contract_metrics.py`，结果记入 PR。

### Task 2: 契约冻结测试（安全网）

**Files:** 新增 `tests/test_source_auth_contract.py`。

**Interfaces:** Consumes: `create_app()` 测试客户端 + 各平台凭据前置状态夹具。Produces: 锁定当前 `(state, logged_in)` 输出的参数化断言。

**Steps:**

- [ ] 为 7 平台各建 ≥3 个前置状态夹具：cookie 有 / 无 / 不全；心跳新鲜 / 过期 / 缺失；`x_source_health` 各态；rdt 文件 present / expired / missing。
- [ ] 写参数化测试断言**当前** `/api/sources/status` 的 `(state, logged_in)`（记录现状，不是记录理想值）。
- [ ] 运行 `.venv/bin/python -m pytest tests/test_source_auth_contract.py -x -q`，确认 ≥21 个 case 全绿。
- [ ] 故意把 `app.py:8774` 的 `_xhs_login_fresh_hours` 改成 1，确认测试变红，再改回——证明网有效。

**Acceptance:**

- 数值门：≥21 case（7 平台 × ≥3 状态），全绿；篡改 TTL 常量后至少 1 个 case 失败。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -q`。

### Task 3: `SourceAuthContract` 模型与旧字段派生

**Files:** 修改 `src/openbiliclaw/api/models.py`；新增 `src/openbiliclaw/api/source_auth/__init__.py`、`src/openbiliclaw/api/source_auth/derive.py`；测试 `tests/test_source_auth_contract.py`。

**Interfaces:** Consumes: 无。Produces: `SourceAuthContract` Pydantic 模型 + `check_legacy_consistency(platform, contract) -> list[str]`。

> **已完成（2026-07-18）**，实现见 `src/openbiliclaw/api/source_auth/{contract,legacy}.py`。
> 原计划的 `derive_legacy_state()` 经证明不可实现，改为 legacy 值原样承袭 + 一致性校验，
> 理由与反证见 spec Phase 1「设计修正」及 `legacy.py` 模块 docstring。

**Steps:**

- [x] 按 spec Phase 1 定义 `SourceAuthContract`（正交字段 + `legacy_*` 兼容字段）。
- [x] 实现 `check_legacy_consistency()`：断言两套视图互不矛盾，含 I3 诚实性守卫（有结论必须有方法）。
- [x] `verified_at` 用 `str` 而非 `datetime`，与 `api/models.py` 其余时间戳字段保持一致（该文件无任何 datetime 字段）。
- [ ] 加 `test_auth_dimensions_are_orthogonal`：遍历 `auth_required` × `credential` × `verification` × `enabled` 全组合，断言改任一维不影响其他三维的取值。
- [ ] 在 Task 2 的每个夹具上跑 `check_legacy_consistency`，断言零矛盾。

**Acceptance:**

- 数值门：Task 2 全部 ≥21 个 case 的 `check_legacy_consistency` 返回空列表（0 处矛盾）；正交性测试覆盖 6×3×4×2 = 144 组合全绿。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -q && .venv/bin/python -m mypy src/`。

### Task 4: 聚合器拆分为每平台 provider

**Files:** 新增 `src/openbiliclaw/api/source_auth/providers.py`（7 个 `_auth_<slug>()` 纯函数）；修改 `src/openbiliclaw/api/app.py`（`sources_status` 瘦身为遍历 + 派生）。

**Interfaces:** Consumes: `RuntimeContext`。Produces: 每平台 `SourceAuthContract`；`SourceStatusItem` 新增 `auth` 子对象，**旧字段行为不变**。

**Steps:**

- [ ] 逐平台把 `app.py:8789-9212` 的判定逻辑搬进 `_auth_<slug>(ctx) -> SourceAuthContract`，按 spec Phase 1 的映射表填 `verify_method` / `verify_ttl_seconds`。
- [ ] douyin 填 `verify_method="live_probe"`，探针为 `/aweme/v1/web/user/profile/self/`：`status_code==0` 且 `user.uid` 非空 → 已登录；`status_code==8`（"用户未登录"）→ 未登录。**不得**沿用 `app.py:3577` docstring 里"没有稳定 nav 端点"的旧结论（spec D11 已用剥离对照实验推翻）。
- [ ] 同时更正 `app.py:3577-3583` 的 docstring，注明结论已被 D11 推翻及新探针端点，避免后人再次以讹传讹。
- [ ] douyin 探针复用 B 站的 TTL 缓存节奏（60s ok / 10s fail），避免每次状态轮询都出网。
- [ ] zhihu 的 `task_history` 回落路径必须标 `verify_method="task_history"`，不得冒充 `browser_heartbeat`（I3）。
- [ ] `sources_status()` 改为遍历 provider + `derive_legacy_state`。
- [ ] 加 `test_verify_method_matches_actual_io`：用 `httpx` mock 断言声明 `live_probe` 的平台确实发起请求、声明 `local_file` / `browser_heartbeat` 的平台确实**没有**出网。
- [ ] 跑 Task 2 全部 case，确认输出**零变化**。
- [ ] 跑 `.venv/bin/python scripts/source_contract_metrics.py`，确认第一项已降到 ≤140。

**Acceptance:**

- 数值门：Task 2 的 ≥21 case 输出零变化；`sources_status()` ≤ 140 行（当前 424）；I3 测试中 `live_probe` 平台出网次数 ≥1、`local_file` 平台出网次数 == 0。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -q && .venv/bin/python scripts/source_contract_metrics.py`。

### Task 5: 修 D3 — B 站凭据单一读取路径

**Files:** 修改 `src/openbiliclaw/runtime/init_prereqs.py`（约 :192）；测试 `tests/test_source_auth_contract.py`。

**Interfaces:** Consumes: `resolve_runtime_cookie(data_dir, configured_cookie)`。Produces: `bilibili_check()` 与 `sources_status` 结论一致。

**Steps:**

- [ ] 先写失败测试 `test_cli_login_visible_to_both_init_and_sources`：只写 `data/bilibili_cookie.json`、不写 config.toml，断言 `/api/init-status` 与 `/api/sources/status` 对 B 站结论一致。
- [ ] 运行确认 FAIL（当前 init 报 failed、sources 报 ready）。
- [ ] 把 `init_prereqs.py:192` 的 config 直读换成 `resolve_runtime_cookie()`。
- [ ] 重跑确认 PASS。
- [ ] 加 AST 扫描测试 `test_single_credential_resolver_per_platform`（I1）：断言 `api/` 与 `runtime/` 内无旁路 `getattr(cfg.<platform>, "cookie")` 直读。
- [ ] 跑 `tests/test_init_prereqs*.py` 等受影响回归测试 + mypy + ruff。

**Acceptance:**

- 数值门：CLI-only 登录场景下两端点结论一致（此前不一致）；I1 AST 扫描命中数 == 0。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -k "cli_login or single_credential" -q`。

### Task 6: `POST /api/sources/{slug}/verify`

**Files:** 新增 `src/openbiliclaw/api/source_auth/verify.py`；修改 `src/openbiliclaw/api/app.py`（注册路由）、`src/openbiliclaw/api/models.py`（响应模型）；测试同上。

**Interfaces:** Consumes: `slug`。Produces: `SourceAuthContract + {changed: bool, message: str}`。

**Steps:**

- [ ] 先写失败测试：7 平台各调一次 verify，断言 200 且 `verify_method` 与 Task 4 声明一致。
- [ ] 运行确认 FAIL（端点不存在）。
- [ ] 按 `verify_method` 实现分派：`live_probe` 绕 TTL 真出网；`passive_health` 返回最近真实请求结论 + 时间；`browser_heartbeat` 经 WS 发 `*_sync_requested` 并等待至多 5s；`local_file` 重读文件；`none` 立即返回 `unverified` 并在 `message` 说明原因。
- [ ] **B 站缓存一致性（要求已修订）**。原要求是"必须复用 `InitPrereqs.bilibili_check()` 的 TTL 缓存"。Task 4 实施时给出了成立的反驳并另建了 `source_auth/probe_cache.py`：`init_prereqs` 的缓存只暴露 `peek_bilibili() -> str`，**没有任何时间戳**，而 `check_legacy_consistency` 要求每个带 TTL 的 `verified` 必须有 `verified_at`；为一个可能几分钟前的结论合成"现在"正是 I3 禁止的编造证据。采纳该设计。

  但我原本担心的风险仍未解除，Task 6 必须处理：**现在 B 站有两条各自缓存的活体验证路径**（`init_prereqs` 喂 `/api/init-status`，`LiveProbeCache` 喂 `/api/sources/status`），两者互不知晓 —— 这就是 D3 的形状。二选一：
  - (a) 给 `init_prereqs` 的缓存补上时间戳，两处合并为一个（更彻底，符合 I1）；
  - (b) 保留两个缓存，但 verify 动作 `record` 时**同时**失效/刷新 `init_prereqs` 的条目，并加测试锁死"两个端点对同一份 B 站凭据不会给出相反结论"。

  当前尚未触发用户可见的不一致，只因 `legacy_state` 是承袭的、且 `LiveProbeCache` 还没有写入方；**Wave B 前端切到正交字段后这就会变成真 bug**。
- [ ] 顺带收敛一个设计异味：`GET /api/init-status` 是只读 GET 却会触发出网（`app.py:2600`）。验证收进显式 POST 后，评估该 GET 是否改为只读缓存。
- [ ] 加每平台 10s 去抖，重复调用返回缓存且 `changed=false`。
- [ ] 重跑确认 PASS；补 `test_verify_debounce`。
- [ ] 补 `test_douyin_probe_distinguishes_login`：用 mock 分别返回 `status_code=0`+uid 与 `status_code=8`，断言映射到 `verified` / `failed`。**回归意义**：这是 spec D11 实验结论的固化，防止将来有人看到 `unverified` 又把探针删掉。
- [ ] 跑 mypy + ruff。

**Acceptance:**

- 数值门：7/7 平台返回 200；douyin `verify_method == "live_probe"` 且在真实登录 cookie 下 `verification == "verified"`；10s 内二次调用 `changed == false` 且未产生新的出网请求（mock 计数 == 1）。
- 真机门：本机当前抖音 cookie 下点击验证，UI 从「状态待验证」变为「已验证」（当前状态属误报，见 spec D11）。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -k verify -q`。

### Task 7: 三端「测试连接」按钮

**Files:** 修改 `src/openbiliclaw/web/desktop/index.html`、`src/openbiliclaw/web/desktop/assets/js/app.js`、`extension/popup/popup.html`、`extension/popup/popup.js`、`extension/popup/popup-api.js`。

**Interfaces:** Consumes: `POST /api/sources/{slug}/verify`。Produces: 每平台一个按钮 + 结果渲染。

**范本已调研（2026-07-18）**：桌面「模型」tab 的 `#probeLlm` 按钮模式在 `app.js:7524` `runLlmConfigProbe()`——按钮 disable → `renderProbePending(el, label)` → await → `renderProbeResult(el, result)` → finally 恢复。**直接复用 `renderProbePending` / `renderProbeResult`（`app.js:7509`）**，不要另写一套渲染，否则又是一份会漂移的副本。

⚠️ **但 tone 需要扩展**：`renderProbeResult` 只有二元 tone（`result.ok ? "success" : "error"`），而 verify 是**三态**——`verified` / `failed` / `indeterminate`（抖音探测超时、小红书插件没回、YouTube 无需验证都属第三类）。把 indeterminate 渲染成红色 error 会让用户以为凭据坏了。CSS 已有 `pending` tone（`renderProbePending` 在用），复用它或新增 `neutral`。

**Steps:**

- [ ] 桌面「平台源」tab 的每个来源状态行加「测试连接」按钮，样式对齐「模型」tab 的 `测试 LLM`。
- [ ] 扩展 `renderProbeResult` 支持三态 tone（或新增 `renderVerifyResult` 复用同一套 DOM 约定），确保 indeterminate 不渲染成 error。
- [ ] 插件 popup 同步添加。
- [ ] 结果文案**全部来自后端 `message`**，前端不得硬编码平台专属文案（I4）。
- [ ] 跑 `cd extension && npm run typecheck && npm run test`。
- [ ] 真机验证：启动 serve-api，7 平台逐个点击，截图记录。**重点看抖音**：应从「状态待验证」翻转为「已验证」（spec D11 已实测该机 cookie 处于登录态）。

**Acceptance:**

- 数值门：7/7 平台按钮可点并渲染后端返回；前端新增平台专属文案分支 == 0（指标脚本第二项不上升）。
- 复现：`.venv/bin/python scripts/source_contract_metrics.py` + 真机截图附 PR。

---

## Wave B — 触及写入与渲染

### Task 8: 统一凭据写入端点

**Files:** 新增 `src/openbiliclaw/api/source_auth/write.py`；修改 `src/openbiliclaw/api/app.py`、`src/openbiliclaw/api/models.py`；测试同上。

**Interfaces:** Consumes: `{kind, value, source}`。Produces: `{accepted, error_code, auth}`，落盘后立即 verify 并回传。

**Steps:**

- [ ] 先写失败测试：对每平台用无效凭据调 `POST /api/sources/{slug}/credential`，断言 `accepted=false` 且带 `error_code`（douyin 除外，需断言其显式声明未校验）。
- [ ] 运行确认 FAIL。
- [ ] 实现统一流程：结构校验 → 落盘 → 广播 → 调用 Task 6 的 verify → 合并返回。
- [ ] 把 6 个老端点改为内部转发，**响应结构保持不变**，并按 Task 1 的指标契约标 `deprecated=True`（否则指标 5 过不了门）。
- [ ] **必须一并纳入 `PUT /api/config`**（`app.py:10864`）——它的路径叶子是 `config`，按词根扫描看不见，但它一条路由写四个平台的凭据（bilibili `:10947` / douyin `:11012` / twitter `:11060` / reddit `:11144`），**且设置页手工粘贴走的正是这条路**。漏掉它等于留了一条绕过统一校验的主路径（spec D5 修正）。
- [ ] 补老端点契约测试，断言响应结构逐字段未变。
- [ ] 跑全量 `tests/test_api*.py` 回归 + mypy + ruff。

**Acceptance:**

- 数值门：6 个老端点响应结构零变化；无效凭据在全部 7 平台均 `accepted=false` 且不落盘（断言文件/DB 未被写）——**含抖音**，其活体探针（Task 6）使写入即验证成为可能，不再是 D5 里的"无脑存"；指标脚本第五项 4 → 1。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py tests/test_api_sources*.py -q`。

### Task 9: 修 D4 — `PUT /api/config` 委托同一校验

**Files:** 修改 `src/openbiliclaw/api/app.py`（约 :10938 bilibili、:10992 douyin、:11045 twitter、:11136 reddit）。

**Interfaces:** Consumes: Task 8 的校验函数。Produces: 两条写入路径强度一致。

**Steps:**

- [ ] 先写失败测试 `test_write_paths_have_equal_validation`：同一份无效 cookie 分别经 `POST /api/sources/{slug}/credential` 与 `PUT /api/config`，断言 `error_code` 相同。
- [ ] 运行确认 FAIL（B 站 PUT 路径当前完全不校验）。
- [ ] 把四处凭据写入改为委托 Task 8 的校验函数，保留掩码回显拦截与空值不清除语义。
- [ ] 重跑确认 PASS；跑 `tests/test_config*.py` 回归。

**Acceptance:**

- 数值门：4 个平台两条路径 `error_code` 逐一相同（当前 B 站不同）；掩码回显与空值保护的既有测试全绿。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -k equal_validation -q`。

### Task 10: 表单描述符下发

**Files:** 修改 `src/openbiliclaw/api/models.py`（`CredentialFormSpec`）、`src/openbiliclaw/api/app.py`（`sources_credentials`）。

**Interfaces:** Produces: 每平台 `form: {kind, label, placeholder, env_var, required_keys, actions, help_text}`。

**Steps:**

- [ ] 定义 `CredentialFormSpec`，按 spec Phase 4 字段表实现。
- [ ] 为 7 平台填写描述符：bilibili 含 `required_keys=["SESSDATA","bili_jct","DedeUserID"]` 与 `open_login_window` action；xhs/zhihu 为 `extension_only`；youtube 为 `none`。
- [ ] 补测试断言每平台 `form.kind` 与其实际写入能力一致（`extension_only` 的平台不得暴露可写文本框）。
- [ ] 跑 mypy + ruff。

**Acceptance:**

- 数值门：7/7 平台有 `form` 描述符；`extension_only` 平台的 `actions` 不含写入类动作。
- 复现：`.venv/bin/python -m pytest tests/test_source_auth_contract.py -k form -q`。

### Task 11: 前端共享渲染模块

**Files:** 新增 `src/openbiliclaw/web/shared/source-status.js`；修改 `src/openbiliclaw/web/desktop/assets/js/app.js`（删 `SOURCE_ACCESS_STATE`）、`extension/popup/popup.js`（删 `SOURCE_STATUS_DOT` / `SOURCE_STATUS_LABEL`）、`extension/scripts/build.mjs`（打包共享模块）。

**Interfaces:** Consumes: `auth` 契约 + `form` 描述符。Produces: 单一渲染实现。

**Steps:**

- [ ] 抽共享模块：状态文案与色调由后端契约驱动。**优先修两个已确认可达的分歧**（spec D6 修正）：(a) popup 把 `no_auth` 与 `unverified` 都渲染成 `#9aa0a6`（`popup.js:7081-7082`），而 desktop 区分 `public` / `pending` tone —— 两个状态都真的会发出来；(b) 未知状态兜底 desktop 显示"状态未知"、popup 显示**空字符串**（`popup.js:7133`）。原诊断关注的 `syncing`/`expired` 键差异是**死键**，后端根本发不出，不必优先。
- [ ] **共享模块必须覆盖第三个前端**：setup 引导页（`web/setup/index.html`）也有平台源设置（`INIT_SOURCE_OPTIONS:265` / `checkBili:433` / `INIT_REASON_TEXT:274`），漏掉它统一完仍剩一份手抄副本。
- [ ] 后端还有第 4 份状态→文案映射 `_x_state_detail`（`app.py:8779-8787`），Phase 1 拆 provider 后应并入契约的 `detail`，不要留在端点里。
- [ ] 表单改由 `form` 描述符驱动渲染，删除所有 per-platform 展示分支。
- [ ] 删除两份漂移的 map（D6 根除）。
- [ ] 跑 `cd extension && npm run typecheck && npm run test && npm run build`。
- [ ] 跑指标脚本，确认第二项 6 → 0、第三项 2 → 1。
- [ ] 真机三端截图对照同一状态。

**Acceptance:**

- 数值门：per-platform 分支 == 0；map 副本 == 1；三端同一状态渲染文案与色调一致（截图比对）。
- 复现：`.venv/bin/python scripts/source_contract_metrics.py --check`。

---

## Wave C — 破坏性，可独立延后

### Task 12: B 站 cookie 迁出 config.toml

**Files:** 修改 `src/openbiliclaw/config.py`、`src/openbiliclaw/bilibili/auth.py`、`src/openbiliclaw/api/app.py`；新增迁移逻辑与回滚开关。

**Steps:**

- [ ] 先写测试：旧 config 含明文 cookie 时启动自动迁移到 `data/bilibili_cookie.json` 并从 config 抹除；`OPENBILICLAW_KEEP_CONFIG_COOKIE=1` 时跳过迁移（回滚开关）。
- [ ] 实现迁移，config 侧保留 `cookie_env` 与 `auth_method`。
- [ ] 跑 `tests/test_config*.py` + `tests/test_bilibili_auth*.py` 全量回归。
- [ ] 三种安装形态验证（git / Docker / 桌面包，CLAUDE.md pitfall #6）。

**Acceptance:**

- 数值门：迁移后 `config.toml` 明文凭据出现次数 == 0；回滚开关生效；三种安装形态均通过。

### Task 13: 移动 Web 平台源视图

**Files:** 修改 `src/openbiliclaw/web/js/app.js`（`openMobileSettings`）、`src/openbiliclaw/web/index.html`。

**比预想的轻**（spec D10 修正）：移动端**已经**接通了 `PUT /api/config`（`web/js/api.js:134` 导出 `updateConfig`），即 D5 里那条写四平台凭据的路由——不需要新端点，只需要 UI。而且移动端**已经在渲染 per-platform 登录态**了（`views/saved.js:32` 把 `login_required` 映射成"需要登录"），只是信号来自 saved-sync 任务结果。**当前的真实体验是：手机上看得到"该平台需要登录"，却在同一个 App 里没有任何地方能去修它**——这才是要补的缺口。

**Steps:**

- [ ] 复用 Task 11 的共享模块，补接入状态只读视图 + 凭据表单 + 测试连接按钮。
- [ ] 让 `views/saved.js` 的 `login_required` 提示可跳转到新的来源设置视图，闭合"看得到修不了"的断点。
- [ ] 真机移动端截图，与桌面/插件对照。

**Acceptance:**

- 数值门：指标脚本第六项 2 → 3；三端截图一致。

---

## Verification after merge

- **观察对象**：本地 8420 的 `/api/sources/status`，7 平台的 `auth.verify_method` 与 `verification` 是否与实际行为吻合。
- **命令**：`curl -s --noproxy '*' localhost:8420/api/sources/status | python3 -m json.tool`，连续观察 72 小时（覆盖 xhs/zhihu 的 72h 心跳窗口与 reddit 的 7 天 TTL 边界）。
- **Owner**：@whiteguo233。**Duration**：Wave A 合并后 72 小时。
- **回滚触发**：任一平台的 `state` / `logged_in` 相对合并前发生变化（Wave A 承诺零变化），或 verify 动作触发平台风控（表现为该平台 discovery 成功率下降 >10%）。回滚方式：Wave A 为纯新增，`git revert` 即可，无数据迁移。

## Explicitly out of scope

- 各平台取数逻辑与 discover 策略调整。
- X / Reddit 的代理脱管修复（spec D11，另开 issue）。
- `saved_sync` 逐条目 `login_required` 与来源级登录态的合并（语义确实不同，见 spec D10）。
- 新增平台；Bangumi 分支的合并本身（其正交化适配随 Task 4 的契约自然完成）。
- `auth_method="qrcode"` 死配置的清理（独立小 PR）。
