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
| `OPENBILICLAW_LITELLM_BASE_URL` | LiteLLM Proxy URL |
| `OPENBILICLAW_LITELLM_API_KEY` | LiteLLM proxy key，不是 provider key |
| `OPENBILICLAW_ALEMBIC_INI` | 可选 Alembic ini 路径 |

API 与 worker 必须收到完全相同的 database URL、Huey path、encryption secret 和
LiteLLM connection。Docker Compose 通过同一个 data named volume 保证两个
SQLite 文件可见；source installer 把绝对路径与 secret 原子写入 mode-`0600`
`.env` 并给两个进程传入同一 environment。

## 产品 settings

当前 strict schema 包含：

- 七来源 `source_enabled` 与 `source_weights` map；partial map 与完整 built-in key set 合并；
- `source_sync_interval_minutes`；
- `feed_low_watermark` 与 `feed_high_watermark`；
- onboarding completion（workflow-owned，只读，不能由普通 PATCH 伪造）。

未知 key、非有限/负 weight、错误 bool 类型和越界数值会让整个 patch 原子失败。
Settings change 使用 application use case 与 repository transaction，不直接写 TOML。
Task model alias/limits、proxy、logging 和 access token 当前属于固定 task contract 或
environment-backed infrastructure，不会伪装成已持久化的 `UserSettings` 字段。新增
用户设置必须先扩展 strict backend schema 与 API tests，再由 Task 22 的 client 呈现。

## 来源账户

Source package 提供 Pydantic settings schema。Credential 只通过 source configure
use case 写入，使用 `OPENBILICLAW_SECRET_KEY` 加密；GET response 只投影 status，
不返回 plaintext/ciphertext。安装器不会收集平台 Cookie 或 provider key。

## AI 配置

OpenBiliClaw runtime 只引用三个固定稳定 alias；它们来自 `TaskSpec`，当前不是可变
`UserSettings` 字段：

- `obc-interactive`
- `obc-analysis`
- `obc-embedding`

Provider credentials、deployment、routing、fallback、cooldown、retry、budget 与
cache 只在 LiteLLM Admin 管理。Docker Admin 默认
`http://127.0.0.1:4000/ui`；source install 使用用户提供的 LiteLLM deployment。

Embedding vector namespace 包含 alias、dimension 与 profile version。修改 embedding
deployment 导致维度或语义空间变化时必须使用新 namespace，不能把旧向量混用。

## 安全与备份

`.env`、应用 DB、Huey DB 和备份都不得提交。`openbiliclaw db backup <destination>`
只处理应用 SQLite；Huey transport 可重建，不是业务备份权威。旧数据目录保持
只读 archive，不自动导入到 vNext。

## UI 状态

现有 static settings 页面尚待 Task 22 通过 generated API client 重接。完成前用
OpenAPI 或受保护 settings API；不要使用旧模型表单或旧配置 endpoint。
