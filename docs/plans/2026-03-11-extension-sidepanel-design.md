# 插件侧边栏模式设计

## 背景

当前浏览器插件以 `action.default_popup` 为主入口，`popup/` 页面已经承载：

- 推荐列表
- 手动刷新推荐
- 用户画像摘要
- 聊天入口

这套能力已经超出了“瞬时弹窗”的合理复杂度。用户希望把插件改成侧边栏主入口，让推荐、画像和聊天都能在更稳定、可持续停留的容器里完成。

## 目标

- 点击扩展图标时，直接打开侧边栏，而不是 popup
- 侧边栏继续承载现有推荐 / 画像 / 聊天三 tab
- 保留手动刷新推荐、反馈提交、运行状态、通知跳转等现有能力
- 尽量复用当前 popup 页面与脚本，避免无意义的大规模重命名

## 非目标

- 这轮不重做整套视觉语言
- 这轮不把 `popup/` 目录整体迁移为 `panel/`
- 这轮不同时保留完整 popup 和完整 side panel 两套独立 UI

## 方案选择

### 方案 A：直接复用现有 popup 页面作为 side panel

做法：

- `manifest.json` 新增 `side_panel.default_path = "popup/popup.html"`
- 去掉 `action.default_popup`
- 点击扩展图标时通过 `chrome.sidePanel.open()` 打开同一页面

优点：

- 复用现有推荐/画像/聊天逻辑
- 代码改动集中
- 风险最低

缺点：

- 需要清理页面里所有“这是一个小弹窗”的尺寸和交互假设

### 方案 B：新建独立 sidepanel 页面

做法：

- 新增 `sidepanel/sidepanel.html` 和独立脚本
- popup 保留或裁剪

优点：

- 概念边界更清晰

缺点：

- 逻辑重复风险高
- 当前阶段性收益不够大

## 结论

采用 **方案 A**：

- 让 `popup/popup.html` 先承担侧边栏页面职责
- 在实现层把它从“窄 popup”调整为“侧边栏容器”
- 后续如果 side panel 成熟，再决定是否重命名目录

## 设计细节

### 1. Manifest

- 保留 `action.default_icon`
- 删除 `action.default_popup`
- 新增 `permissions: ["sidePanel"]`
- 新增 `side_panel.default_path = "popup/popup.html"`

### 2. 入口行为

- 点击扩展图标时，background/service worker 调用 `chrome.sidePanel.open(...)`
- 推荐通知、认知提醒等从后台打开插件界面的场景，统一优先落到侧边栏
- 页面首次打开时默认显示推荐 tab

### 3. 页面复用策略

- 继续使用 `extension/popup/popup.html`
- 继续使用现有 `popup.js` / `popup-api.js` / `popup-helpers.js`
- 通过样式和布局调整，让页面适配侧边栏宽高

需要特别清理的 popup 假设：

- 固定窄宽度
- 依赖弹窗关闭节奏的局促排版
- 不适合长内容阅读的内部滚动层级

### 4. 布局方向

- 顶部保留品牌与运行状态
- tab 导航保留，但更适合纵向停留
- 推荐卡片、画像内容、聊天区改成更适合侧边栏的纵向阅读节奏
- 聊天消息区和推荐列表允许更长内容停留，不再为“快速扫一眼”过度压缩

### 5. 测试与文档

需要同步更新：

- `extension/tests/manifest-assets.test.ts`
- `extension/tests/popup-layout.test.ts`
- 如有必要，补 service worker 对 `chrome.sidePanel` 的调用测试
- `docs/modules/extension.md`
- `docs/changelog.md`

## 风险

### Chrome 兼容性

`sidePanel` 是较新的扩展 API，因此实现应基于 Chrome MV3 原生能力，不引入兼容层魔法。

### UI 回归

当前页面文件名仍叫 popup，容易把旧尺寸假设带回来。测试里需要显式校验：

- manifest 不再声明 `default_popup`
- 侧边栏入口存在
- 页面布局不再依赖 popup 小尺寸

## 验收标准

- 扩展图标点击后打开侧边栏
- manifest 已切换为 side panel 主入口
- 推荐 / 画像 / 聊天 / 手动刷新在侧边栏里正常可用
- extension 测试、typecheck、build 通过
