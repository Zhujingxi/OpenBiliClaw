# 浏览器插件模块

## 模块范围

`extension/` 是 Chrome 插件子项目，负责：

- 在 B 站页面采集行为事件
- 通过 background service worker 缓冲并上报到本地后端
- 在 popup 中展示连接状态和推荐结果

当前里程碑进度：

| 子模块 | 状态 | 说明 |
|------|------|------|
| 8.1 行为采集 | ✅ | `collector.ts` + `service-worker.ts` 已接通真实事件链 |
| 8.2 后端 API | ✅ | Python 侧 `/api/events`、`/api/health`、`/api/recommendations` 已可联调 |
| 8.3 Popup | ⏳ | 连接状态骨架已在，推荐展示仍待继续完善 |

## 目录结构

```text
extension/
├── manifest.json
├── package.json
├── popup/
│   ├── popup.html
│   └── popup.js
├── src/
│   ├── background/
│   │   ├── buffer.ts
│   │   └── service-worker.ts
│   ├── content/
│   │   └── collector.ts
│   └── shared/
│       ├── behavior.ts
│       └── types.ts
└── tests/
    ├── collector-helpers.test.ts
    └── service-worker-buffer.test.ts
```

## 当前能力

### `collector.ts`

负责内容脚本侧采集：

- 点击与搜索
- 视频 `view` / `pause` / `seek`
- 页面快照 `snapshot`
- 滚动 `scroll`
- 卡片停留 `hover`
- 评论 / 点赞 / 投币 / 收藏意图事件

同时支持 B 站 SPA 导航感知，在 URL 变化时重新发送快照并重绑视频监听。

### `service-worker.ts`

负责后台缓冲与上报：

- 接收内容脚本事件
- 高频事件去重
- 强信号行为优先 flush
- `chrome.alarms` 周期性批量发送
- 发送失败时把事件回填到缓冲区

## 本地开发

在 `extension/` 目录下：

```bash
npm install
npm test
npm run typecheck
npm run build
```

## 手动联调

1. 在项目根目录启动后端：

```bash
openbiliclaw start
```

2. 在 `extension/` 目录构建插件：

```bash
npm run build
```

3. 在 Chrome 的扩展管理页加载 `extension/` 目录
4. 打开 B 站首页、搜索页、视频页，执行点击、搜索、播放、暂停、滚动等行为
5. 观察后端 `/api/events` 写入效果，或直接查看 SQLite `events` 表

## 当前限制

- 行为按钮识别基于 DOM 文本、类名和 `aria-label`，不是服务端最终结果确认
- 采集范围优先覆盖首页、搜索页和视频页，未承诺所有 B 站模板完全一致
- popup 还未完成推荐列表展示与反馈按钮闭环
