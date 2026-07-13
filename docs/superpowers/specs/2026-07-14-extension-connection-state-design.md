# 扩展连接状态去抖设计

**日期：** 2026-07-14
**范围：** 浏览器扩展 popup / side panel 连接徽标，不修改后端接口

## 问题

popup 当前把两个不同信号写进同一个布尔状态：HTTP `/api/ping` 成功会显示“已连接”，而已经建立的 `/api/runtime-stream` WebSocket 一旦关闭就立即显示“未连接”。离线轮询随后每秒再次请求 `/api/ping`，于是 HTTP 可达但 WebSocket 暂时中断时，徽标会在“已连接 / 未连接”之间反复跳动。

## 目标与非目标

目标：

- HTTP 探活决定后端是否可达。
- WebSocket 断开但 HTTP 仍可达时显示“重连中”，不渲染“未连接”。
- 只有 `/api/ping` 失败或抛错时才进入离线状态并启动离线轮询。
- WebSocket 已经重连时，迟到的旧探活结果不得覆盖新的在线状态。
- Chrome 与 Firefox 继续共用同一套 popup 资源。

非目标：

- 不修改 `/api/ping`、`/api/health` 或 `/api/runtime-stream` 协议。
- 不处理后端进程退出、代理绕过或容器端口发布。
- 不改变 service worker 工具栏 badge 的后端可达 / 未初始化决策表。

## 方案

在现有 `popup-connection-poller.js` 增加一个小型连接协调器，维护三态：

- `online`：WebSocket 已连接。
- `reconnecting`：HTTP 已确认可达，但 WebSocket 尚未连接或刚断开。
- `offline`：HTTP 探活失败。

协调器暴露三个同步入口和一个异步入口：HTTP 可达、明确离线、WebSocket 已连接、WebSocket 已断开。断开入口先发布 `reconnecting`，再执行注入的 `checkBackendStatus()`。每次入口都会递增 revision；异步探活完成时只有 revision 仍匹配才允许提交结果，从而避免“重连已成功，旧失败探活随后把状态改回离线”的竞态。

`state.online` 保持现有业务含义，但改为表示“HTTP 可达”：`online` 与 `reconnecting` 都为 `true`，只有 `offline` 为 `false`。这样推荐、配置和画像 API 在实时流重连期间仍可继续使用。离线轮询只在 `offline` 启动；HTTP 恢复后先进入 `reconnecting`，由 WebSocket 的既有 1 秒重连机制最终推进到 `online`。

## UI 行为

头部徽标新增第三种投影：

| 状态 | 文案 | 色调 |
|---|---|---|
| `online` | 已连接 | 绿色 |
| `reconnecting` | 重连中 | 琥珀色 |
| `offline` | 未连接 | 红色 |

`getConnectionBadgeState()` 接收明确状态字符串，`setStatus()` 同步更新 badge `data-tone`、圆点 class 和文本。HTML 初始值继续为离线，避免脚本尚未运行时伪报在线。

## 错误与竞态处理

- `/api/ping` 返回 false 或抛错均归为 `offline`。
- WebSocket 在探活完成前重连时，`markStreamConnected()` 递增 revision，旧探活结果被忽略。
- 地址切换时主动关闭旧 socket 不触发故障断线回调；其他异步探活仍由更新 revision 防止覆盖新连接。
- HTTP 可达但 WebSocket 长期被代理阻断时保持“重连中”，不伪报后端离线。

## 测试

Node 单测覆盖：

1. 三态徽标文案和 tone。
2. WebSocket 断开、HTTP 成功时保持 `reconnecting`。
3. WebSocket 断开、HTTP 失败或抛错时进入 `offline`。
4. 断开探活未完成时重新连接，迟到失败结果不覆盖 `online`。
5. HTTP 从离线恢复后进入 `reconnecting`，既有离线轮询停止。
6. 主动关闭旧 WebSocket 不触发断线通知，首次 stream 打开不重复刷新、后续重连仍刷新。

最后运行扩展定向测试、完整 `npm test`、`npm run typecheck` 和 Chrome / Firefox build，确保共享 popup 资源在两种产物中一致。
