# Agent 一键部署指南（vNext）

本页是 AI coding agent 的执行清单。完整安全与幂等契约见
[agent-install.md](agent-install.md)。

## 1. 选择运行方式

- 用户已有 Docker Compose v2：优先 Docker。
- 没有 Docker：使用 source / uv，并要求用户提供 LiteLLM base URL 与 key。
- 不替用户创建云 provider 账户，也不在 OpenBiliClaw 中重建 provider 编辑器。

## 2. 执行入口

macOS / Linux / WSL2：

```bash
MODE=auto bash scripts/install.sh
```

Windows PowerShell：

```powershell
& .\scripts\install.ps1 -Mode auto
```

源码路径可预先设置：

```text
OPENBILICLAW_LITELLM_BASE_URL=<user LiteLLM URL>
OPENBILICLAW_LITELLM_API_KEY=<user LiteLLM key>
```

不要把 key 拼进复制命令或用户可见日志。让用户在 installer 的隐藏输入中填写，
或由用户在当前 shell 自己设置环境变量。

## 3. 判定成功

脚本必须完成 migration、API/worker（API 与 worker）启动，并通过 public readiness
和 bearer-protected settings 检查；source 模式还必须通过 `doctor`。Docker 由一次性 `migrate` 服务串行 migration，
其成功后才能启动 API/worker；source installer 则在启动两个进程前执行 migration。
两个运行时都只读检查 schema head。仅有端口监听不算成功；只启动 API、不启动
worker 也不算成功。

Docker 成功还要求 Compose 状态中 `migrate` 为 `exited/0`，API 与 worker 均为
`running/healthy`。worker healthcheck 会确认 PID 1 是正式 worker、schema 位于 head、
Huey queue 可完整读取并能在 `BEGIN IMMEDIATE` 中执行随后回滚的真实 schema/data 写入；
pathname 在 SQLite connection 前后必须仍绑定同一 held inode。受保护 readiness 通过后
installer 会再次检查 Compose 状态；`restarting`/`exited` 会立即失败。
`SKIP_START=1` 不启动长期服务，但仍通过一次性 `docker compose run --rm migrate`
完成 migration 后才返回 prepared。

源码安装的进程状态不保存裸 PID，而是同时保存 OS 启动时间、可执行文件和命令指纹。
重跑时只有四项仍完全匹配才会发信号；先 TERM 并限时等待，身份仍相同且未退出才升级
KILL。新启动流程会轮询 API 与 worker 两个子进程；旧 queue 文件和 API HTTP readiness
都不能掩盖 worker 已退出。任一部分启动、状态写入或 readiness 失败都会终止并回收本轮
已启动的所有子进程。整个 stop → migration → launch → doctor/readiness 流程由独立、限时的
跨进程 lifecycle lock 串行；私密 installer UUID、canonical checkout root 与单调 generation
共同绑定进程状态。Lock metadata inode 由 held parent dir FD 与内嵌 root/device/inode
绑定并在临界区前后复核；lock replacement、复制 checkout 或 symlinked ancestor 会失败关闭。
复制 `.env` 时 managed root/DB/Huey/instance 字段重绑定当前 checkout，secret 与外部
LiteLLM connection 保留。复制/移动/篡改及 directory/FIFO 等非普通 state 会被拒绝。stop 与
失败清理都不按 pathname 删除 state；已停止 generation 的 ownership record 保留到下次
ownership-checked publication，旧清理因此没有删除新 generation 的窗口。
`SKIP_START=1` 同样先验证并停止旧 managed pair，再执行 migration。

成功事件：

```text
BOOTSTRAP_STATUS:{"message":"local_runtime_ready",...}
```

或：

```text
BOOTSTRAP_STATUS:{"message":"docker_runtime_ready",...}
```

失败时脚本非零退出。不要绕过失败步骤，也不要打印 `.env` 内容排查。

## 4. 后续配置

Docker 用户在 `http://127.0.0.1:4000/ui` 的 LiteLLM Admin 配 provider，并建立
`obc-interactive`、`obc-analysis`、`obc-embedding`。Source 用户在其外部
LiteLLM 部署中完成相同配置。

来源连接与首次 bootstrap 通过 `/api/v1/sources` 和 `/api/v1/onboarding`。
现有 static Web/extension 的新 API wiring 尚待 Task 22，因此当前阶段用 OpenAPI
或生成 client 验证，不要让用户操作旧页面完成设置。

## 5. 可复现检查

```bash
uv run openbiliclaw doctor
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
```

受保护端点必须由调用者从私密 `.env` 读取 bearer token 后请求；不要在文档、
聊天、命令历史或截图中展开 token。
