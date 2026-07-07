# 搜索词生成模式选择器 — Implementation Plan

> **Spec:** [`2026-07-06-keyword-generation-mode-selector-spec.md`](./2026-07-06-keyword-generation-mode-selector-spec.md)
> **Status:** Reviewed — 2026-07-06 (Codex 2-round adversarial review, R1 findings applied, R2 APPROVE)。承接 Phase 1/2/2.1/2.3（`51e20c8b`）。
> **Executor:** Opus 4.8 subagent；Claude 逐 task 验收；TDD。

**Goal:** 配置页(桌面 web + 插件 popup)加单一"搜索词生成模式"下拉,三档(经典/混合/灵感),API
层派生+翻译到既有两布尔,零 config schema 改动、零后端行为改动。

**Tech Stack:** Python 3.11+/FastAPI/Pydantic(后端), 原生 JS(桌面 web + 插件 popup),
node --test(extension), pytest(后端)。Interpreter `.venv/bin/python`;extension `cd extension &&
npm run test` / `npm run typecheck`。

**Invariants:** 不新增 `DiscoveryConfig` 字段(两布尔仍 canonical);不改 `_generate_for` /
`_inspiration_replaces_merged_keywords` 行为;既有 discovery config 读写不回归;两端 UI option
值/顺序/文案一致。

---

### Task 1: 后端读派生 `keyword_generation_mode`

**Files:** `src/openbiliclaw/api/models.py`（`DiscoveryConfigOut`）、`src/openbiliclaw/api/app.py`
（`_config_to_response` 的 discovery 构造 ~8236）；Test `tests/test_api_app.py`

**Steps:**
1. 失败测试：`DiscoveryConfigOut` 新增只读 `keyword_generation_mode: Literal["legacy","hybrid",
   "inspiration"]`;`GET /api/config` 在三种布尔组合下分别返回对应档(含边界
   `enabled=false & replace=true → legacy`)。
2. 实现：`DiscoveryConfigOut` 加字段;`_config_to_response` 用纯函数
   `_derive_keyword_generation_mode(enabled, replace)` 计算传入(把该纯函数放 app.py 或 models.py,
   便于单测)。
3. Gate：`.venv/bin/python -m pytest tests/test_api_app.py -q` + ruff + mypy。

### Task 2: 后端写翻译（PUT /api/config）

**Files:** `src/openbiliclaw/api/models.py`（`ConfigUpdateIn` / discovery update DTO）、
`src/openbiliclaw/api/app.py`（`update_config` ~8536）；Test `tests/test_api_app.py`

**关键(R1 S1)**:`update_config` 的 discovery 段是**逐字段显式应用**的白名单(`if
"multimodal_evaluation_enabled" in ddata: cfg.discovery.xxx = ...`,`app.py:~8945`),**不是**盲目
merge。所以必须加一个**同款显式块**直接设两个布尔,否则 PUT 静默无效。

**Steps:**
1. 失败测试：`PUT /api/config` 带 `discovery.keyword_generation_mode` 三档 → 持久化后
   `cfg.discovery` 两布尔为**规范值**:legacy→{false,false}、hybrid→{true,false}、
   inspiration→{true,true};**尤其** 先设 inspiration 再设 legacy → replace 必须回 false(不留残留);
   写入的 config.toml **不含** `keyword_generation_mode` 键;非法值(`"garbage"`)→ **422**;
   同时发 mode + 显式布尔 → mode 赢。
2. 实现：在 `update_config` 的 discovery 应用段加显式块——若 `"keyword_generation_mode" in ddata`:
   手动 Literal 校验(非法抛 `HTTPException(422)`)→ `cfg.discovery.inspiration_search_enabled =
   (mode != "legacy")`、`cfg.discovery.inspiration_replace_merged_keywords = (mode == "inspiration")`。
   **mode 赢冲突**:该块在两个布尔的显式应用之后跑(或先从 ddata 弹出这两个布尔键)。**不新增**
   config 字段、**不 setattr** mode 到 cfg.discovery。纯函数 `_mode_to_flags(mode) -> (enabled,
   replace)` 单测三档。
3. round-trip 测试：写某档 → GET 读回 `DiscoveryConfigOut.keyword_generation_mode` 相等。
4. Gate：`.venv/bin/python -m pytest tests/test_api_app.py -q` + ruff + mypy。

### Task 3: 桌面 web 选择器

**Files:** `src/openbiliclaw/web/desktop/index.html`、`src/openbiliclaw/web/desktop/assets/js/app.js`；
Test `tests/test_desktop_web_*.py`（沿用现有桌面 web 测试风格）

**Steps:**
1. 失败测试：加载时 `#keywordGenerationMode` 反映 `config.discovery.keyword_generation_mode`;
   保存时 discovery payload 含 `keyword_generation_mode`(三档各测,或参数化)。
2. `index.html`:发现设置卡片加 `<select id="keywordGenerationMode">`(经典/混合/灵感三 option +
   `settings-note-inline` 成本说明)。
3. `app.js`:加载 `setSelect("keywordGenerationMode", discovery.keyword_generation_mode || "legacy")`;
   保存 discovery 段加 `keyword_generation_mode: $("#keywordGenerationMode").value`。
   **spread 顺序坑(R2 polish)**:该键必须写在 `...(state.config?.discovery || {})` 展开**之后**,
   否则被加载快照的旧值覆盖;保存/round-trip 测试断言选中值真的进了 payload。
4. Gate：`.venv/bin/python -m pytest tests/test_desktop_web_*.py -q` + ruff。

### Task 4: 插件 popup 选择器

**Files:** `extension/popup/popup.html`（或对应模板）、`extension/popup/popup.js`；
Test `extension/tests/popup-settings.test.ts`

**Steps:**
1. 失败测试(node --test)：popup 加载把 `cfg.discovery.keyword_generation_mode` 填进
   `#cfgKeywordGenerationMode`;保存收集该值进 discovery payload;三档 round-trip。
2. popup html 加 `<select id="cfgKeywordGenerationMode">`(与桌面 web option 值/顺序/文案一致);
   `popup.js` 加载 `setVal(...)`(~6377 附近)+ `collectForm()` 的 discovery 段(~6575)加该字段。
   **spread 顺序坑(R2 polish)**:该键写在 `...(state.runtimeConfig?.discovery || {})` 展开**之后**,
   防加载快照覆盖用户选值;save/round-trip 测试断言。
3. Gate：`cd extension && npm run typecheck && npm run test`。

### Task 5: 文档 + 完整门

**Files:** `docs/modules/config.md`、`docs/modules/extension.md`、`docs/changelog.md`

**Steps:**
1. config.md:三档 ↔ 两布尔映射表(canonical 写 + 读容忍)+ 配置页(两端)位置 + 混合成本提示;
   注明 `keyword_generation_mode` 是 UI/API 派生便利层,config.toml 仍存两布尔;非法值 422、
   mode 赢冲突规则。
2. extension.md(R1 S2):popup 设置区新增"搜索词生成模式"选择器。若仓库有 API/config 模块文档
   记录响应 schema,一并加 `DiscoveryConfigOut.keyword_generation_mode`。**架构图判定**:本改动是
   纯 config 开关(无新模块/adapter/数据流块)→ 不动 `docs/architecture.md`/`docs/spec.md` §3/
   README 图(与 Phase 2.1 同判);在报告里说明该判定。
3. changelog.md:当前版本块一条 bullet。
4. 完整门：`.venv/bin/python -m pytest tests/test_api_app.py tests/test_desktop_web_*.py -q` ;
   `ruff check src/ tests/` ; `mypy src/` ; `cd extension && npm run typecheck && npm run test`。

---

## Sequencing & risk

- Task 1/2(后端)先做,是 UI 的依赖(前端要读/写 mode 字段)。Task 3/4(两端 UI)独立,可依次做。
- 风险点(R1 S1+S3):真实 handler 是**逐字段显式应用**白名单,不是 merge——真正的坑是"加了
  校验/删了键但从没 setattr 两个布尔 → PUT 静默无效"。Task 2 的"持久化后两布尔为规范值"断言
  专防这个静默 no-op(config load 本就忽略未知 discovery 键,不会误落库)。
- 风险点:两端 UI option 不一致会让同一 config 在两处显示/写入不同——Task 4 硬要求与 Task 3 一致。
- 回滚:feature 分支未 push;纯增量(新字段 + 新 UI 控件),既有配置读写不动。

## Out of scope

- 改三档的后端行为 / 默认值。
- 把 `inspiration_breadth` 合并进本选择器。
- 新增 `DiscoveryConfig.keyword_generation_mode` 字段。
