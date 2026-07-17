# Web client

FastAPI 挂载的 `/setup`、`/web` 与 `/m` 是无框架的 vNext Web client。三者共享由
`openapi/openapi.json` 确定性生成的 `src/openbiliclaw/web/js/api-client.js` 和一层很薄的
页面状态 helper；不复制 request DTO 或 endpoint 字符串。

## 认证与流

Web 通过 `/api/v1/auth/login` 建立 same-origin HttpOnly cookie。所有 unsafe request 由 client
附加 `X-OBC-Auth`，服务端同时校验 Origin。Chat、onboarding 和 job progress 使用 fetch body
stream 解析 SSE，既支持 POST，也保持 cookie/CSRF 语义。

## 页面职责

- `/setup`：readiness、登录、来源连接、bootstrap 和 onboarding progress。
- `/web`：feed/interaction、profile edit、chat/history、library、来源与系统设置。
- `/m`：feed/interaction、profile edit、chat/history、library、feed replenishment progress
  与高频设置子集的响应式入口；不提供 onboarding/source bootstrap 或完整设置面。

桌面 `/web` 设置面投影
`sources/schedules/feed/profile/tasks/network/logging/access_control/jobs` 全部可变字段；
deployment facts 只读。`/m` 只投影 feed 水位/最低分、来源同步间隔、network
与 extension access 子集。AI 界面只展示三个稳定 alias 的健康状态和后端提供的
LiteLLM Admin URL，不编辑 provider 凭据或路由。

Library 保存把 durable collection write 与 interaction signal 分成两个阶段：`201` 或
`409` 都立即呈现已保存；若 interaction 暂时失败，按钮只重试 signal，不重复发送可能冲突的
collection add。

生成检查：

```bash
node openapi/generate-client.mjs --write
node openapi/generate-client.mjs --check
```
