# Popup 立即刷新推荐设计

**目标**

在插件 popup 的推荐 tab 增加一个“立即刷新”按钮，用户点击后可显式触发后端完整刷新一轮候选池与推荐列表，并在刷新完成后立即看到更新结果。

**问题背景**

当前 popup 只能读取现有 `/api/recommendations`。即使后端有持续补货机制，用户也缺少一个明确的“现在就更新一下”入口。对用户来说，这会让推荐更新显得被动，也不利于调试和体验验证。

**推荐方案**

采用“后端显式刷新接口 + popup loading 状态”的方案：

- 后端新增 `POST /api/recommendations/refresh`
- 接口内部调用现有 `runtime_controller` 执行一次完整刷新
- popup 点击按钮后：
  - 按钮进入 loading
  - 文案切换为“正在补货”
  - 刷新完成后重新拉取 `runtime-status` 和 `recommendations`

这样可以保证：

- 编排逻辑仍留在后端
- popup 不需要知道 discovery/recommend 的内部流程
- 用户能明确控制“现在刷新一次”

**交互设计**

- 按钮位置：推荐 tab 标题区或推荐列表上方，和当前状态提示靠近
- 默认文案：`立即刷新`
- loading 文案：`正在补货…`
- 成功后：
  - 更新推荐列表
  - 顶部 hint 显示“刚给你补了一批新的”
- 失败后：
  - 保留现有推荐列表
  - hint 显示“这次没刷新成功，稍后再试”

**错误处理**

- 未初始化：
  - 后端返回 `refreshed=false` 与 `reason="not_initialized"`
  - popup 提示先执行 `openbiliclaw init`
- 后端刷新失败：
  - 接口返回 500 或明确错误
  - popup 显示失败提示，但不清空已有推荐
- 连不上后端：
  - 按现有离线态处理

**影响范围**

- Python:
  - `src/openbiliclaw/api/models.py`
  - `src/openbiliclaw/api/app.py`
- Extension:
  - `extension/popup/popup-api.js`
  - `extension/popup/popup.js`
  - `extension/popup/popup.html`
- Tests:
  - `tests/test_api_app.py`
  - `extension/tests/popup-helpers.test.ts` 或 popup 相关测试
- Docs:
  - `docs/modules/extension.md`
  - `docs/changelog.md`
  - `docs/v0.1-todolist.md`
