# M83 Popup Design

## Background

`extension/popup/` 当前只有两部分能力：

- `popup.js`：请求 `/api/health`，显示“已连接/未连接”
- `popup.html`：推荐区仍然是纯占位文案

而 `8.2` 已经提供了插件所需的后端接口：

- `GET /api/health`
- `GET /api/recommendations`

同时推荐链路也已经完整具备：

- `discover` 会把候选写入 `content_cache`
- `recommend` 会生成带 `expression` / `topic_label` 的推荐历史

所以 `8.3` 的关键不是继续扩 API，而是把 popup 做成一个真正可用的“状态 + 推荐列表”面板。

## Goal

实现一个最小但真正可用的 popup：

- 显示后端连接状态
- 拉取并展示最新推荐列表
- 点击推荐项时直接打开 B 站视频
- 在无推荐或后端离线时显示明确空状态

## Non-Goals

- 本轮不实现真实的喜欢/不喜欢反馈提交通道
- 不在 popup 内触发新的推荐生成
- 不在 popup 内实现分页、筛选、搜索
- 不引入新的后端 API

## Approaches Considered

### Option A: 只做文本列表

优点：

- 实现最快

缺点：

- 用户价值太低
- 仍然像半成品

### Option B: 状态 + 推荐卡片 + 打开视频（推荐）

优点：

- 直接满足 `8.3` 的主要价值
- 不把 `9.1 feedback` 提前拖进来
- 现有 API 已足够支持

缺点：

- 反馈按钮本轮只能先做 UI 占位

### Option C: 一次把反馈也接通

优点：

- 看起来更完整

缺点：

- 会把 `9.1` 的 API、数据写回和插件按钮行为一起提前实现
- 风险和变更面明显扩大

## Recommendation

采用 **Option B**：

- popup 打开时并行检查后端状态和推荐列表
- 状态区显示“已连接/未连接”
- 推荐区渲染卡片列表
- 点击卡片打开视频
- 反馈按钮仅做 UI 占位并提示“即将支持”

## UI Structure

popup 页面分 3 个区块：

1. 顶部 Header
   - 产品名
   - 简短副标题

2. 状态区
   - 连接状态圆点
   - 状态文本
   - 可选简短说明，如“已连接到本地后端”

3. 推荐区
   - 加载态：`正在获取推荐...`
   - 空状态：
     - 后端离线：提示先运行 `openbiliclaw start`
     - 无推荐：提示先运行 `init` / `discover` / `recommend`
   - 列表态：
     - 标题
     - `UP 主`
     - `topic_label`
     - `expression`
     - 轻量操作按钮区（`打开视频`、`喜欢/不喜欢` 占位）

## Data Flow

1. popup 打开
2. `popup.js` 请求 `/api/health`
3. 若后端在线，再请求 `/api/recommendations`
4. 将返回的 `items` 渲染为推荐卡片
5. 点击卡片或“打开视频”按钮：
   - 调用 `chrome.tabs.create({ url: "https://www.bilibili.com/video/<bvid>" })`

## Error Handling

### 后端离线

- 状态区显示离线
- 推荐区显示空状态
- 不抛浏览器控制台未捕获错误

### 推荐接口失败

- 若 health 成功但 recommendations 失败
- 推荐区显示“推荐暂时不可用”

### 数据缺字段

- `title` 为空时显示“未命名推荐”
- `up_name` 为空时显示“未知 UP 主”
- `topic_label` 缺失时不单独显示该 badge
- `expression` 缺失时显示简短 fallback 文案

## Testing Strategy

当前 extension 没有 DOM 测试基建，因此本轮测试分两层：

### 代码层

新增 popup 纯函数 helper，并为其写 Node 测试：

- `buildVideoUrl(bvid)`
- 推荐卡片 view model 映射
- 空状态文案判断

### 手动联调

- 启动 `openbiliclaw start`
- 准备推荐数据
- 加载扩展
- 打开 popup
- 确认状态、推荐列表和打开视频动作

## Files

- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Create: `extension/popup/popup-helpers.js`
- Create: `extension/tests/popup-helpers.test.ts`
- Modify: `docs/modules/extension.md`
- Modify: `docs/v0.1-todolist.md`
- Modify: `docs/changelog.md`
- Modify: `docs/index.md` if module status wording changes

## Acceptance

- 点击插件图标时能看到真实连接状态
- 若存在推荐记录，popup 能显示最新推荐列表
- 点击推荐项后能打开对应 B 站视频页面
- 若无推荐或后端未启动，popup 有明确提示
