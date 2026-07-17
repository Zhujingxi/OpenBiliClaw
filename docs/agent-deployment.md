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

安装器在 source 与 Docker 路径进入 migration/Compose 前生成独立 random
session secret、Web password/hash 与 extension key/digest，只把 signing secret、scrypt hash
和 `key-id:sha256-digest` records 写入私密 `.env`（POSIX mode `0600`；Windows
当前用户独占 DACL），重复执行保留非空原值。
仅在首次创建时，专用 `BOOTSTRAP_STATUS first_run_access` event 一次性交付 password 与完整
extension key；rerun 不可恢复或重印。除该 event 外，不要在命令参数、shell history、状态事件、
日志、截图或对话中展开任何值，也不要复用 installer bearer、
来源 encryption secret 或 LiteLLM master key。可选
`OPENBILICLAW_LITELLM_ADMIN_URL` 只接受无 credential/query/fragment 的 absolute HTTP(S)
public navigation URL；不要公开 internal base URL/key。
两份 Compose 都只向 `api` 转发 `OPENBILICLAW_WEB_PASSWORD_HASH`、required
`OPENBILICLAW_SESSION_SECRET`、digest-only `OPENBILICLAW_EXTENSION_ACCESS_KEYS` 与
`OPENBILICLAW_LITELLM_ADMIN_URL`；worker 不接收 browser-auth material。vNext 不从 legacy config
补齐这些值。验证时检查 Compose render/key presence，不打印 value。
Docker 默认 Admin navigation 为 `http://127.0.0.1:4000/ui`。installer 的 `HOST`/`PORT`
会同步进入 Compose API command、port mapping、healthcheck 与 protected probe。source CLI
自动读取 checkout `.env`，但不覆盖 agent 已显式设置的 process environment。

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
POSIX 先锁定 held checkout root directory，再取得持久 root guard 与内层 lifecycle lock；
native Windows 使用 root guard file。完整 installer UUID、canonical checkout root、单调
generation 和 anchor UUID/device/inode 形成同一持久 lease。等待结束、进入业务前、generation
更新和退出均精确复核，所有等待共用同一截止时间。首次 metadata 通过 held temp FD 同步并在
POSIX 上 `fchmod` 后 hard-link no-replace 发布。POSIX 逐级持有并校验 `data/vnext` parent FD；
崩溃遗留的未绑定 inode 仅在普通文件、单链接、owner、私密 mode 与 pathname identity
全部成立时原位重绑。native Windows 在稳定 root guard 内仅接受 non-reparse、普通文件、
单链接且 held/path identity 一致的 orphan；这不等价于 POSIX mode/owner 的 ACL 保证。
已绑定 pathname 缺失/换 inode、复制 checkout 或
symlink/junction ancestor 会失败关闭。
复制 `.env` 时 managed root/DB/Huey/instance 字段重绑定当前 checkout，secret 与外部
LiteLLM connection 保留。复制/移动/篡改及 directory/FIFO 等非普通 state 会被拒绝。stop 与
失败清理都不按 pathname 删除 state；已停止 generation 的 ownership record 保留到下次
ownership-checked publication，旧清理因此没有删除新 generation 的窗口。
`SKIP_START=1` 同样先验证并停止旧 managed pair，再执行 migration。

canonical checkout root 是信任边界：覆盖正常并发、崩溃、managed leaf 篡改与链接重定向，
不承诺抵御恶意 same-UID 替换整个 root，或同时替换全部 Windows coordination objects。

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

Schema head 包含 `0002_auth_state`；它只保存 session epoch，负责撤销既有 Web/extension
session，不保存 cookie、bearer 或 device key。installer bearer 继续只供 operation/API client。

来源连接、typed idempotent disconnect 与首次 bootstrap 通过 `/api/v1/sources` 和
`/api/v1/onboarding`；generic browser task 使用 typed `/api/v1/source-tasks` contract。
现有 Web/extension 已通过 generated client 接入这些接口，可完成来源连接与 bootstrap；
provider credential 与 routing 仍只在 LiteLLM Admin 配置。

## 5. 可复现检查

```bash
uv run openbiliclaw doctor
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
```

受保护端点必须由调用者从私密 `.env` 读取 bearer token 后请求；不要在文档、
聊天、命令历史或截图中展开 token。
