# vNext 持久化与系统设置

## 状态与边界

本模块是 backend-first vNext 的独立持久化基础，已经实现 SQLAlchemy 2.x 映射、repository、同步 Unit of Work、Alembic 基线迁移、类型化系统设置和 Fernet 凭据密文适配。它使用新的 `data/vnext/openbiliclaw.db`，不读取、迁移或替换当前 `storage/database.py` 管理的 legacy 数据库。

**独立 vNext worker 已构造并调用这套基础，但当前生产 API、legacy runtime、CLI 和前端尚未切换。** 因此它已经是 vNext 后台任务的业务状态来源，却仍不是现有用户请求与 legacy 数据的权威来源。后续任务必须显式完成 HTTP 接线、安装器 secret 生命周期、数据迁移和切换验证，才能改变这一状态。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 独立数据库配置 | ✅ | `DatabaseSettings` 默认指向 `sqlite:///data/vnext/openbiliclaw.db`；URL、echo 与有限 SQLite busy timeout 可通过 `OPENBILICLAW_DATABASE_URL` / `OPENBILICLAW_DATABASE_ECHO` / `OPENBILICLAW_DATABASE_BUSY_TIMEOUT_SECONDS` 覆盖 |
| SQLAlchemy schema | ✅ | 16 张 vNext 业务表覆盖设置、来源账户、活动、画像与独立 consumed-evidence ledger、内容、Feed、集合、聊天、来源任务、后台任务和 AI run |
| Alembic 基线 | ✅ | `0001_vnext_baseline` 支持从空库 upgrade、downgrade 后重建，并预置 `favorites` / `watch_later` 两个本地集合 |
| Repository + UoW | ✅ | 领域对象经同步 repository 持久化；`UnitOfWork` 只在显式 `commit()` 时提交，退出时统一 rollback 并关闭 session |
| 画像并发保护 | ✅ | `ProfileRepository.append()` 使用 expected revision 检查，拒绝陈旧修订和画像 ID 漂移；`profile_consumed_evidence` 与 revision 在同一事务提交/回滚 |
| 类型化用户设置 | ✅ | `SettingsService` 先合并默认值、严格校验完整 `UserSettings`，再在同一事务中替换设置 |
| 凭据密文 | ✅ | `CredentialCipher` 从 `OPENBILICLAW_SECRET_KEY` 派生上下文隔离的 Fernet key；`source_accounts` repository 只接受 cipher 签发的 opaque `EncryptedCredential`，伪造 token 前缀会被拒绝 |
| worker 接线 | ✅ | 独立 Huey worker 使用同一 UoW 执行 activity/profile/feed/job 用例；`job_runs` 是产品任务状态权威 |
| 完整生产切换 | 🚧 | legacy storage/runtime 仍是公开请求权威；vNext API、安装器 secret 交付和数据迁移尚未实现 |

## Schema

| 数据域 | 表 |
|--------|----|
| 系统与来源 | `settings`, `source_accounts` |
| 活动与画像 | `activity_events`, `profile_revisions`, `profile_evidence`, `profile_consumed_evidence` |
| 内容与推荐 | `content_items`, `candidate_assessments`, `feed_entries`, `interactions` |
| 本地集合与聊天 | `collections`, `collection_items`, `chat_turns` |
| 后续执行基础 | `source_tasks`, `job_runs`, `ai_runs` |

内容使用 `(source_id, external_id)` 作为跨源唯一身份；候选评估绑定 profile revision；画像 facet evidence 与独立 consumed ledger 都以外键关联活动证据，后者不受后续 facet 删除影响。`job_runs.dispatched_at` 是 DB→Huey 成功 handoff marker，但不是“消息仍在 queue”的证明：worker startup 会重新发布全部 pending row。progress 只允许单调前进。来源账户凭据列只保存 `encrypted_credentials`。`source_tasks.request_deadline_at` 保存绝对请求截止时间，使过期任务即使清理延迟也不可再 claim。SQLite engine 会打开 foreign keys、把 `busy_timeout_seconds` 同时配置到 driver timeout 与 `PRAGMA busy_timeout`，并在文件型 URL 下自动创建父目录。

## 公开 API

| 模块 | 公开契约 / 入口 |
|------|-----------------|
| `features.system` | `DatabaseSettings`, `UserSettings`, `SettingsService` |
| `infrastructure.database` | `create_engine_and_session()`, `UnitOfWork` |
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

凭据加解密要求进程提供非空 `OPENBILICLAW_SECRET_KEY`。数据库、日志、设置表和 AI run 均不得写入 plaintext credential 或派生 key；丢失或更换 secret 后，旧密文不可解密。当前安装器尚未接线该 secret，因此本模块不能被视为已经具备可升级的生产密钥生命周期。
