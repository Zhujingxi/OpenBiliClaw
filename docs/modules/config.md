# vNext 配置

vNext 不读取旧 provider route、Soul JSON 或桌面配置文件作为产品权威。可变产品
设置存放在应用 SQLite 的 `settings` table，并通过 strict
`GET/PATCH /api/v1/settings` 读取和修改。

## 运行基础设施

| 环境变量 | 含义 |
|---|---|
| `OPENBILICLAW_DATABASE_URL` | 应用数据库；默认 `sqlite:///data/vnext/openbiliclaw.db` |
| `OPENBILICLAW_HUEY_PATH` | 独立 Huey SQLite transport 文件 |
| `OPENBILICLAW_SECRET_KEY` | 来源账户 credential 加密 root secret |
| `OPENBILICLAW_ACCESS_TOKEN` | `/api/v1` bearer access token |
| `OPENBILICLAW_WEB_PASSWORD_HASH` | 可选 Web 登录密码的 scrypt hash；不接受明文设置回读 |
| `OPENBILICLAW_SESSION_SECRET` | Web/extension session 的 HMAC signing secret |
| `OPENBILICLAW_EXTENSION_ACCESS_KEYS` | extension device key 的 `key-id:sha256-digest` JSON array；不保存完整 device key |
| `OPENBILICLAW_LITELLM_BASE_URL` | LiteLLM Proxy URL |
| `OPENBILICLAW_LITELLM_API_KEY` | LiteLLM proxy key，不是 provider key |
| `OPENBILICLAW_LITELLM_ADMIN_URL` | 可选、显式公开给浏览器的 LiteLLM Admin HTTP(S) URL；不得含 userinfo、query 或 fragment |
| `OPENBILICLAW_ALEMBIC_INI` | 可选 Alembic ini 路径 |
| `OPENBILICLAW_PROJECT_ROOT` | source installer 当前 canonical checkout；每次安装强制重绑定 |
| `OPENBILICLAW_INSTALLER_INSTANCE_ID` | source installer 私密实例 UUID；防止复制 `.env` 沿用旧实例路径 |

API 与 worker 必须收到完全相同的 database URL、Huey path、encryption secret 和
LiteLLM connection。Docker Compose 通过同一个 data named volume 保证两个
SQLite 文件可见；source installer 把绝对路径与 secret 原子写入 mode-`0600`
`.env` 并给两个进程传入同一 environment。数据库 URL、Huey path、project root 与
installer instance 是 installer-managed 字段，每次运行按当前私密 instance metadata
覆写；access/encryption secrets 与外部 LiteLLM URL/key 则保持已有非空值。
源码构建与预构建两份 Compose 都把 password hash、session signing secret、extension
digest records 和 public Admin URL 传给 `api`；worker 不接收 browser-auth material。
vNext auth 只读这些 environment values，不读取 legacy TOML auth fallback。

## 产品 settings

`UserSettings` 是 strict、secret-free、递归嵌套的完整合同；`PATCH` 是同结构的递归
partial merge，显式 `null`、未知 key、错误 strict bool、非有限数值和越界值都会让整个
事务失败：

| group | authoritative fields | mutability |
|---|---|---|
| root | `onboarding_complete` | workflow-owned read-only |
| `sources` | complete seven-source `enabled` / `weights` maps | mutable；partial maps merge into the canonical key set |
| `schedules` | `source_sync_interval_minutes` | mutable; worker schedule reads it |
| `feed` | `low_watermark`, `high_watermark`, `candidate_multiplier`, `max_batch_candidates`, `min_score`, `min_novelty`, `max_per_source`, `max_per_topic` | mutable; feed replenishment/admission reads them |
| `profile` | `minimum_evidence_confidence` | mutable; profile projection filters evidence with it |
| `tasks.<task-name>` | `model_alias`, `semantic_retry_limit`, `timeout_seconds`, `request_limit`, `total_tokens_limit` | mutable; all six typed `TaskRunner` specs are resolved from this map |
| `network` | `mode=direct\|system\|custom`, secret-free `proxy_url` | mutable; supported overseas source clients consume it; embedded credentials/query/fragment are rejected |
| `logging` | `console_level`, `file_level`, `directory` | levels mutable; `directory` is deployment/read-only |
| `access_control` | `web_password_enabled`, `trust_loopback`, `session_ttl_hours`, `extension_access_enabled`, `extension_session_ttl_hours`, `installer_bearer_configured`, `password_configured` | behavior mutable; the two `*_configured` flags are deployment/read-only facts and never reveal values |
| `jobs` | `retention_days`, `worker_concurrency` | retention mutable; concurrency is deployment/read-only (`OPENBILICLAW_WORKERS`, clamped to `1..4`) |

`tasks` must retain all six canonical task names and only the two generative aliases
`obc-interactive` / `obc-analysis`; embedding stays the fixed `obc-embedding` service alias.
Read-only fields are absent from the PATCH schema and are rejected if clients try to write them.
Settings changes use the application use case and one repository transaction, never TOML.
API startup 和每次成功 PATCH 都应用 persisted network/logging policy。独立 worker 在构造
registry、恢复任务和启动 consumer 之前读取同一 `UserSettings`，作用域内应用 network
proxy 与 OpenBiliClaw-owned handler levels，并在正常退出或启动失败时恢复进入 worker 前的
process state；host-owned handlers 和 root logger level 不会被改写。

## 来源账户

Source package 提供 Pydantic settings schema。Credential 只通过 source configure
use case 写入，使用 `OPENBILICLAW_SECRET_KEY` 加密；GET response 只投影 status，
不返回 plaintext/ciphertext。安装器不会收集平台 Cookie 或 provider key。

## AI 配置

OpenBiliClaw runtime 只引用三个稳定 alias。六个 generative task 的 alias 与 limits 位于
`tasks` group，但 alias 仍只能在以下两个生成别名中选择；embedding 别名固定：

- `obc-interactive`
- `obc-analysis`
- `obc-embedding`

Provider credentials、deployment、routing、fallback、network retry、provider budget 与
cache 只在 LiteLLM Admin 管理。`OPENBILICLAW_LITELLM_ADMIN_URL` 是独立、可选的安全
导航字段；runtime 不会从 private `OPENBILICLAW_LITELLM_BASE_URL` 猜浏览器 URL。
Docker 可显式设置 `http://127.0.0.1:4000/ui`；source install 使用部署者提供的 public URL。

Embedding vector namespace 包含 alias、dimension 与 profile version。修改 embedding
deployment 导致维度或语义空间变化时必须使用新 namespace，不能把旧向量混用。

## 安全与备份

`.env`、应用 DB、Huey DB 和备份都不得提交。`openbiliclaw db backup <destination>`
只处理应用 SQLite；Huey transport 可重建，不是业务备份权威。旧数据目录保持
只读 archive，不自动导入到 vNext。

## UI 状态

现有 static settings 页面尚待 Task 22 通过 generated API client 重接。完成前用
OpenAPI 或受保护 settings API；不要使用旧模型表单或旧配置 endpoint。
