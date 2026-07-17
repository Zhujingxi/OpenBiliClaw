# 常见问题（vNext）

## 为什么静态 Web 或扩展里还有旧设置？

后端已切换到 `/api/v1`，但现有 static Web/extension 的 generated client wiring
要在 Task 22 完成。当前页面只保持挂载和样式，不是权威配置界面。请用 OpenAPI
和受保护 API 验证后端。

## 为什么源码安装必须提供 LiteLLM？

LiteLLM 是所有支持部署的必需基础设施。OpenBiliClaw 不再保存 provider
credentials，也不再实现 routing、fallback 或 provider editor。源码部署设置
`OPENBILICLAW_LITELLM_BASE_URL` 与 `OPENBILICLAW_LITELLM_API_KEY`；Docker 使用
Compose 内的 LiteLLM 服务。

## 安装器重复运行会旋转 secret 吗？

不会。`.env` 中已有的非空 access/encryption/LiteLLM 值会复用。文件通过同目录
临时文件和原子 replace 更新，POSIX 权限为 `0600`；符号链接会被拒绝。

## 为什么必须同时有 API 和 worker？

API 负责请求与 SSE，Huey worker 负责 source sync、profile projection、feed
replenishment 和 cleanup。只启动 API 会让 durable jobs 留在 queue 中。源码
installer 管理两个 PID；Compose 管理两个 service。

## 如何判断安装成功？

Installer 必须依次通过 migration、API/worker 启动、`openbiliclaw doctor`、public readiness 和
bearer-protected settings。`GET /api/v1/system/readiness` 只说明 API 存活，不等于
整个安装契约完成。

## 为什么 queue 与应用库是两个 SQLite 文件？

`openbiliclaw.db` 是业务权威，包含 `job_runs`；`huey.db` 是 durable transport。
两者分离可以避免把 queue result 当成产品状态。API 与 worker 必须挂载相同的
data volume，并使用相同的两个绝对路径。

## Provider 在哪里配置？

Docker 打开 `http://127.0.0.1:4000/ui`。Source install 使用用户自己的 LiteLLM
Admin。需要三个稳定 alias：`obc-interactive`、`obc-analysis`、
`obc-embedding`。

## 旧数据会被删除或自动迁移吗？

不会。vNext 使用 fresh database；旧文件保持不动作为手工 archive。项目没有旧
API 或旧数据兼容层。
