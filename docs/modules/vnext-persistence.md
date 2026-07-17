# vNext 持久化与系统设置

> Runtime update: the vNext API and operational CLI now use this database as
> authority. `openbiliclaw db migrate` and `openbiliclaw db backup` are supported;
> legacy data remains
> untouched and is not imported.

## 状态与边界

本模块是权威 vNext 后端的持久化层，已经实现 SQLAlchemy 2.x 映射、repository、同步 Unit of Work、Alembic 基线迁移、类型化系统设置和 Fernet 凭据密文适配。它使用新的 `data/vnext/openbiliclaw.db`，不读取、迁移或替换 `storage/database.py` 管理的历史 v0.3 数据库。

`/api/v1`、独立 worker 和运维 CLI 共同使用这套基础；应用数据库是设置、来源账户、活动、画像、Feed、library、chat、source task、job 与 AI run 的业务权威。Docker 与 source installer 都生成并复用来源加密 secret，先完成唯一 migration owner 的 schema 写入，再让 API/worker 执行只读 head gate。现有 static Web/extension 的 client 接线留给 Task 22；历史数据仅保留为不导入的手工 archive。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 独立数据库配置 | ✅ | `DatabaseSettings` 默认指向 `sqlite:///data/vnext/openbiliclaw.db`；URL、echo 与有限 SQLite busy timeout 可通过 `OPENBILICLAW_DATABASE_URL` / `OPENBILICLAW_DATABASE_ECHO` / `OPENBILICLAW_DATABASE_BUSY_TIMEOUT_SECONDS` 覆盖 |
| SQLAlchemy schema | ✅ | 16 张 vNext 业务表覆盖设置、来源账户、活动、画像与独立 consumed-evidence ledger、内容、Feed、集合、聊天、来源任务、后台任务和 AI run |
| Alembic 基线 | ✅ | `0001_vnext_baseline` 支持从空库 upgrade、downgrade 后重建，并预置 `favorites` / `watch_later` 两个本地集合 |
| runtime schema gate | ✅ | installer 或 Compose `migrate` 独占 Alembic 写入；API/worker startup 只读验证当前 revision 精确等于 head |
| worker runtime health | ✅ | Compose probe 要求 PID 1 为正式 worker、schema 位于 head；独立 Huey SQLite 通过 integrity check，并在 pathname 与 held descriptor 前后同 inode 的前提下执行 `BEGIN IMMEDIATE`、真实 `CREATE`/`INSERT`、`ROLLBACK`，不留下 probe artifact |
| 安全在线备份 | ✅ | `db backup` 以 no-follow FD 固定 source，并用私有 hard-link set 保留 main/WAL/SHM/journal snapshot 语义；完整 SQLite snapshot 先写入 held、已 unlink 的 `0600` payload FD，目标名称在 payload 完整同步前始终不存在。macOS 用 `fclonefileat(payload_fd, dirfd, target)`、Linux 用 `O_TMPFILE` + `linkat(AT_EMPTY_PATH)` 原子 no-replace 发布；directory sync 后、返回前再次核对 held final FD 与 pathname identity。发布后异常不 pathname-unlink final，避免删除并发替换。Windows/缺少安全 primitive 的平台在 destination reservation 前失败关闭 |
| Repository + UoW | ✅ | 领域对象经同步 repository 持久化；`UnitOfWork` 只在显式 `commit()` 时提交，退出时统一 rollback 并关闭 session |
| 画像并发保护 | ✅ | `ProfileRepository.append()` 使用 expected revision 检查，拒绝陈旧修订和画像 ID 漂移；`profile_consumed_evidence` 与 revision 在同一事务提交/回滚 |
| 类型化用户设置 | ✅ | `SettingsService` 先合并默认值、严格校验完整 `UserSettings`，再在同一事务中替换设置 |
| 凭据密文 | ✅ | `CredentialCipher` 从 `OPENBILICLAW_SECRET_KEY` 派生上下文隔离的 Fernet key；`source_accounts` repository 只接受 cipher 签发的 opaque `EncryptedCredential`，伪造 token 前缀会被拒绝 |
| worker 接线 | ✅ | 独立 Huey worker 使用同一 UoW 执行 activity/profile/feed/job 用例；`job_runs` 是产品任务状态权威 |
| 后端生产切换 | ✅ | `/api/v1`、worker、运维 CLI、安装器 secret 生命周期与 fresh vNext database 已是权威；只剩 Task 22 的现有 Web/extension client 接线 |

## Schema

| 数据域 | 表 |
|--------|----|
| 系统与来源 | `settings`, `source_accounts` |
| 活动与画像 | `activity_events`, `profile_revisions`, `profile_evidence`, `profile_consumed_evidence` |
| 内容与推荐 | `content_items`, `candidate_assessments`, `feed_entries`, `interactions` |
| 本地集合与聊天 | `collections`, `collection_items`, `chat_turns` |
| 执行与审计 | `source_tasks`, `job_runs`, `ai_runs` |

内容使用 `(source_id, external_id)` 作为跨源唯一身份；候选评估绑定 profile revision；画像 facet evidence 与独立 consumed ledger 都以外键关联活动证据，后者不受后续 facet 删除影响。`job_runs.dispatched_at` 是 DB→Huey 成功 handoff marker，但不是“消息仍在 queue”的证明：worker startup 会重新发布全部 pending row。progress 只允许单调前进。来源账户凭据列只保存 `encrypted_credentials`。`source_tasks.request_deadline_at` 保存绝对请求截止时间，使过期任务即使清理延迟也不可再 claim。SQLite engine 会打开 foreign keys、把 `busy_timeout_seconds` 同时配置到 driver timeout 与 `PRAGMA busy_timeout`，并在文件型 URL 下自动创建父目录。

## 公开 API

| 模块 | 公开契约 / 入口 |
|------|-----------------|
| `features.system` | `DatabaseSettings`, `UserSettings`, `SettingsService` |
| `infrastructure.database` | `create_engine_and_session()`, `UnitOfWork` |
| `infrastructure.database.operations` | `SQLiteOperationalStore.diagnose()` 同时报告 app DB migration/integrity 与 queue integrity/write access；`require_schema_at_head()` 提供只读 startup gate |
| `infrastructure.jobs.health` | `worker_health_ready()` / module healthcheck entrypoint |
| `infrastructure.database.repositories` | repository Protocol、SQLAlchemy adapter、`CollectionRecord`, `ProfileRevisionConflict` |
| `infrastructure.security` | `CredentialCipher`, `EncryptedCredential` |
| `infrastructure.security.credentials` | `MissingCredentialKeyError`, `SECRET_KEY_ENV` |

`UnitOfWork` 暴露 `settings`、`source_accounts`、`activities`、`profiles`、`content`、`assessments`、`feed`、`interactions`、`collections`、`chat`、`source_tasks`、`job_runs` 和 `ai_runs` repository。`activities` 支持幂等导入，profile repository 提供独立 consumed ledger，assessment adapter 可查询同 revision 已评估与历史 admitted/interacted 内容，`job_runs` 提供幂等 schedule、全部/未发布 pending 查询、dispatch reconciliation、原子 claim、条件 running transaction guard、运行中取消、progress checkpoint、重启恢复和 terminal cleanup。四个 worker handler 在 feature 写事务内先执行 guard；cancel/checkpoint/terminal-or-retry/recovery 也都以条件 UPDATE 开始，不经过 SELECT→ORM flush 的 SQLite lock upgrade。两个竞争事务按 write order 与有限 busy timeout 串行：cancel 先则 feature 无 effect，guard 先则 feature commit 先于 cancelled state；timeout exhaustion 保持显式失败。worker 中的 `TaskRunner` 会写 `ai_runs`，但该表只记录 task、model alias、状态、时间、usage 与错误分类；ORM、Alembic 基线和 repository API 均不含 input/output payload，不依赖启发式脱敏，避免应用数据库成为内容或 provider credential 的旁路持久化通道。`source_tasks` 已由 queued browser transport 使用，但现有浏览器扩展 dispatcher 尚未切到 generic claim/complete。

`UserSettings` 的当前完整契约如下：

| 字段 | 默认值 | 约束 |
|------|--------|------|
| `onboarding_complete` | `false` | 严格 boolean |
| `feed_low_watermark` | `20` | `0..1000`，且不得高于 high watermark |
| `feed_high_watermark` | `50` | `1..2000` |
| `source_sync_interval_minutes` | `30` | `1..10080` |
| `source_weights` | 七个平台均为 `1.0` | key 必须是 canonical SourceId；值必须有限且非负，零权重不参与 feed 配额 |
| `source_enabled` | 七个平台均为 `false` | key 必须是 canonical SourceId；只有显式启用的来源会被 worker 调用 |

## 迁移与安全约束

开发者可在仓库根目录执行 `alembic upgrade head` 创建 vNext 空库；迁移环境会先为 file-backed SQLite URL 创建缺失的父目录。该命令只操作 `alembic.ini` 指向的 vNext URL；不得把 legacy 数据库 URL 传给这套迁移，也不得用 `Base.metadata.create_all()` 代替版本化迁移。

凭据加解密要求进程提供非空 `OPENBILICLAW_SECRET_KEY`。数据库、日志、设置表和 AI run 均不得写入 plaintext credential 或派生 key；丢失或更换 secret 后，旧密文不可解密。Docker 与 source installer 在私密 `.env` 中生成并幂等复用该 secret，以 mode `0600`、symlink 拒绝、同目录临时文件、`fsync` 和原子替换保护它；安装输出与 OpenAPI 都不暴露值。
