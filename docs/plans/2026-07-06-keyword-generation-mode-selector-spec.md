# 搜索词生成模式选择器 Spec（配置页三档切换）

> **Status:** Reviewed — 2026-07-06 (Codex 2-round adversarial review, R1 findings applied, R2 APPROVE)。承接 Phase 1/2/2.1/2.3（同 worktree，已提交至 `51e20c8b`）。
> **Branch:** `feature/discovery-inspiration-mvp`。

## Goal

在配置页（桌面 web + 浏览器插件 popup）加一个**单一下拉选择器**，让用户在三种搜索词生成模式间
切换，不用手改 config.toml 里两个含义晦涩的布尔。

## 三档语义（后端已支持，本方案只做暴露 + UI）

| UI 档位 | 语义 | 后端 flag 组合 |
|---|---|---|
| **经典**（legacy） | 只用旧 merged planner | `inspiration_search_enabled=false` |
| **混合**（hybrid） | 新旧共存,都生成入同一池,可 cohort 对比 | `enabled=true` + `replace_merged=false` |
| **灵感**（inspiration） | 新灵感链路替换旧 planner | `enabled=true` + `replace_merged=true` |

**混合**如实说明:一次调度里旧 merged planner 与新灵感链路都跑、词都进 `discovery_keywords`
(靠 `inspiration_backend` 区分),`keyword-inspiration-report` 可对比二者准入/delight——最贵但最
安全的渐进验证档;经典最省(无轴库丰富度/explore),灵感中等(全丰富度无旧兜底)。

## 设计:派生枚举,不新增 config 字段(单一真相源仍是两个布尔)

- **不在 `DiscoveryConfig` 新增 `keyword_generation_mode` 字段**:两个布尔仍是 config.toml 的
  canonical 真相源(已被 Phase 1/2/2.3 全套测试覆盖,`_inspiration_replaces_merged_keywords()` 等
  全依赖它们)。新增第三个字段会造成三处真相、易漂移。
- **API 层做派生 + 翻译**:
  - **读**(`DiscoveryConfigOut`):新增派生只读字段 `keyword_generation_mode`,由两个布尔算出
    (`enabled=false → "legacy"`(**读容忍**:enabled=false 时无论 replace 取值都算 legacy);
    `enabled=true & replace=false → "hybrid"`;`enabled=true & replace=true → "inspiration"`)。
  - **写**(`PUT /api/config`,`update_config` handler):现 handler **不是盲目 merge**,而是对
    discovery 段**逐字段显式应用**(`if "multimodal_evaluation_enabled" in ddata: cfg.discovery
    .xxx = ...`,`app.py:~8945`)。故新增一个**同款显式块**:
    ```
    if "keyword_generation_mode" in ddata:
        mode = _validate_mode(ddata["keyword_generation_mode"])   # 非法 → HTTP 422
        cfg.discovery.inspiration_search_enabled = (mode != "legacy")
        cfg.discovery.inspiration_replace_merged_keywords = (mode == "inspiration")
    ```
    **写规范化(canonical)**:每档都**显式写两个布尔**——`legacy→{false,false}`、
    `hybrid→{true,false}`、`inspiration→{true,true}`(不留 replace 残留旧值,修 R1 legacy 非
    canonical)。`keyword_generation_mode` 本身**不写入 config.toml**(config load 会忽略未知
    discovery 键,handler 也从不 setattr 它)。
  - **校验(R1)**:`ConfigUpdateIn.discovery` 是 `dict[str, object]` 裸 dict,Pydantic **不会**
    自动校验嵌套 Literal → 必须在 handler 里**手动**用 Literal/TypeAdapter 校验 mode 值,非法
    抛 422。
  - **冲突优先级(R1)**:同一 discovery 更新里若 mode 与显式布尔键同时出现,**mode 赢**——mode
    块在两个布尔的显式应用**之后**执行(或先删除 ddata 里的 `inspiration_search_enabled` /
    `inspiration_replace_merged_keywords` 键),保证结果确定、与 UI 语义一致。
- 好处:UI 只见一个下拉;config.toml 只存两个既有布尔;零迁移、零新真相源。

## UI(两端一致)

### 桌面 web（`web/desktop/index.html` + `assets/js/app.js`）

- `index.html` 发现设置卡片(multimodal 那块附近)加
  `<select id="keywordGenerationMode">` 三 option(经典/混合/灵感)。
- `app.js` 加载:`setSelect("keywordGenerationMode", discovery.keyword_generation_mode)`。
- `app.js` 保存:discovery payload 加 `keyword_generation_mode: $("#keywordGenerationMode").value`。
- 加一句 `settings-note-inline` 说明三档区别 + 成本提示(混合最贵)。

### 插件 popup（`extension/popup/popup.js` + 对应 html）

- popup 设置区加同样的 `<select id="cfgKeywordGenerationMode">` 三 option。
- `popup.js` 加载:`setVal("cfgKeywordGenerationMode", cfg.discovery?.keyword_generation_mode)`。
- popup 保存 discovery 段加 `keyword_generation_mode`。
- 两端 option 值/顺序/文案一致。

## Non-Goals

- 不动三种模式的**后端行为**(只暴露开关;`_generate_for` / `_inspiration_replaces_merged_keywords`
  逻辑不变)。
- 不改 `inspiration_search_enabled` 的发布默认值(仍 `false`=经典;用户在 UI 里改)。
- 不在本选择器里暴露 `inspiration_breadth`(广度档)——那是独立旋钮,本期不合并进来。
- 不改 config.toml schema(不新增 `keyword_generation_mode` 字段)。

## Acceptance Criteria

1. **读派生**:后端单测——两个布尔的三种组合 → `DiscoveryConfigOut.keyword_generation_mode` 分别
   为 `legacy`/`hybrid`/`inspiration`(含 `enabled=false & replace=true` 也算 legacy 的边界)。
2. **写翻译(canonical)**:`PUT /api/config` 带 `discovery.keyword_generation_mode` → handler 显式
   把 `cfg.discovery.inspiration_search_enabled` / `inspiration_replace_merged_keywords` 设为该档
   的**两个**规范值(legacy→{false,false}、hybrid→{true,false}、inspiration→{true,true});持久化
   后 config.toml 的两布尔正确、**不含** `keyword_generation_mode` 键;三档各测一次(尤其
   inspiration→legacy 后 replace 必须回 false,不留残留)。
3. **round-trip + 校验**:写入某档 → GET 读回 `keyword_generation_mode` 等于写入档;非法档位值
   (如 `"garbage"`)→ handler 手动 Literal 校验抛 **422**(不静默、不落库)。
3b. **冲突优先级**:同时发 mode 与显式 `inspiration_*` 布尔 → mode 赢(结果 = mode 的两布尔),
   有测试。
4. **桌面 web**:加载时下拉反映当前配置;切档保存后 `PUT` payload 带正确 mode;桌面 web 测试
   (`tests/test_desktop_web_*`)覆盖加载 + 保存两路。
5. **插件 popup**:同上,`extension/tests/popup-settings.test.ts` 覆盖加载 + 保存;两端 option 一致。
6. **不回归**:后端 discovery config 既有字段(multimodal 等)读写不受影响;Phase 1/2/2.3 全后端
   测试全绿;extension `npm run test` 全绿。
7. **文档**:`docs/modules/config.md` 记三档 ↔ 两布尔映射(含 canonical 写规范化 + 读容忍)+
   两端 UI 位置;`docs/modules/extension.md` 记 popup 新增选择器(R1:popup 改了要更新模块文档);
   config API 响应/写行为变更若有对应 API 模块文档一并更新;changelog 一条。config-flow 是否需
   `docs/architecture.md` 小注由 Plan Task 5 判定(纯 config 开关、无新模块/数据流,倾向不动图)。

## Open Decisions（拟定,review 后 lock）

- **派生枚举(API 层翻译),不新增 config 字段**,两布尔仍 canonical — proposed。
- **写显式设两布尔(handler 白名单块,非 merge),legacy 也写 replace=false**(canonical) —
  locked（R1 S1+S2:handler 是逐字段应用,legacy 单设 enabled 会留 replace 残留）。
- **mode 手动 Literal 校验抛 422;mode 与显式布尔冲突时 mode 赢** — locked（R1 S2:裸 dict 不自动
  校验）。
- **三档(经典/混合/灵感),两端 UI 一致** — proposed(用户已确认)。
- **发布默认仍 legacy(enabled=false),UI 里改** — proposed。
- **不合并 `inspiration_breadth` 进本选择器** — proposed。
