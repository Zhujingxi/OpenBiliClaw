# M82 Backend API Design

## Background

`extension/` 已经有内容脚本、service worker 和 popup 骨架，其中：

- `service-worker.ts` 已经默认向 `http://localhost:8420/api/events` 发送事件
- `popup.js` 已经默认请求 `GET /api/health`

当前缺口不在浏览器插件侧是否有骨架，而在 Python 主工程尚未提供可供插件联调的 HTTP API。

## Goal

实现 `8.2 后端 API` 的最小可运行版本，优先打通插件依赖的 3 个接口：

- `POST /api/events`
- `GET /api/health`
- `GET /api/recommendations`

同时让 `openbiliclaw start` 至少能启动这个本地 API 服务。

## Non-Goals

- 不在本轮实现完整插件行为采集增强
- 不在 API 层自动触发偏好分析、画像更新或在线学习
- 不在本轮实现 Popup 完整 UI
- 不重构 extension 侧现有骨架

## Technology Choice

采用 **FastAPI** 作为 HTTP API 层。

理由：

- 路由和 schema 清晰
- 测试方便，适合 `TestClient`
- 后续插件联调和健康检查更顺
- 比内置 `http.server` 更适合继续扩展

## Endpoints

### `GET /api/health`

返回最小健康状态：

```json
{
  "status": "ok",
  "service": "openbiliclaw-api"
}
```

### `POST /api/events`

请求体：

```json
{
  "events": [
    {
      "type": "click",
      "url": "https://www.bilibili.com/video/BV1...",
      "title": "示例标题",
      "timestamp": 1710000000000,
      "context": { "pageType": "video" },
      "metadata": {}
    }
  ]
}
```

后端映射规则：

- `event_type <- type`
- `title <- title`
- `url <- url`
- `context <- context`
- `metadata <- metadata + timestamp`

最终通过 `MemoryManager.propagate_event()` 写入事件层。

### `GET /api/recommendations`

返回当前推荐列表的简化结构，供 popup 拉取：

- `id`
- `bvid`
- `title`
- `up_name`
- `expression`
- `topic_label`
- `presented`

本轮只读数据库，不在 API 层触发新的推荐生成。

## CLI Start Behavior

`openbiliclaw start` 本轮先定义为“启动本地 API 服务”，默认监听：

- `127.0.0.1:8420`

这与 extension 里现有硬编码地址保持一致，方便后续 `8.1/8.3` 直接联调。

## Testing Strategy

新增 API 测试，覆盖：

- `/api/health` 返回 200
- `/api/events` 批量事件写入成功
- `/api/recommendations` 返回列表结构

CLI 测试覆盖：

- `start` 至少走到 API 启动入口

使用 FastAPI `TestClient`，不做真实浏览器集成测试进入主门禁。

## Files

- Create: `src/openbiliclaw/api/app.py`
- Create: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/storage/database.py` if recommendation query shape needs adjustment
- Test: `tests/test_api_app.py`
- Test: `tests/test_cli.py`
- Docs: `docs/v0.1-todolist.md`
- Docs: `docs/changelog.md`
- Docs: `docs/modules/cli.md`

## Acceptance

- 本地存在可启动的 HTTP API 服务
- 插件可请求 `GET /api/health`
- 插件可向 `POST /api/events` 上报事件
- popup 可从 `GET /api/recommendations` 取到推荐数据
- `start` 命令不再是纯 stub
