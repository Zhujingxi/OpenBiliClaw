# vNext 持久化与系统设置

## 状态与边界

本模块是 backend-first vNext 的独立持久化基础，已经实现 SQLAlchemy 2.x 映射、repository、同步 Unit of Work、Alembic 基线迁移、类型化系统设置和 Fernet 凭据密文适配。它使用新的 `data/vnext/openbiliclaw.db`，不读取、迁移或替换当前 `storage/database.py` 管理的 legacy 数据库。

**当前生产 API、runtime、CLI、安装流程和前端都尚未构造或调用这套基础。** 因此它不是运行时权威来源；现有 legacy storage/runtime 继续承载真实用户数据与业务请求。后续任务必须显式完成 use case、启动接线、数据迁移和切换验证，才能改变这一状态。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 独立数据库配置 | ✅ | `DatabaseSettings` 默认指向 `sqlite:///data/vnext/openbiliclaw.db`，可通过 `OPENBILICLAW_DATABASE_URL` / `OPENBILICLAW_DATABASE_ECHO` 覆盖 |
| SQLAlchemy schema | ✅ | 15 张 vNext 业务表覆盖设置、来源账户、活动、画像、内容、Feed、集合、聊天、来源任务、后台任务和 AI run |
| Alembic 基线 | ✅ | `0001_vnext_baseline` 支持从空库 upgrade、downgrade 后重建，并预置 `favorites` / `watch_later` 两个本地集合 |
| Repository + UoW | ✅ | 领域对象经同步 repository 持久化；`UnitOfWork` 只在显式 `commit()` 时提交，退出时统一 rollback 并关闭 session |
| 画像并发保护 | ✅ | `ProfileRepository.append()` 使用 expected revision 检查，拒绝陈旧修订和画像 ID 漂移 |
| 类型化用户设置 | ✅ | `SettingsService` 先合并默认值、严格校验完整 `UserSettings`，再在同一事务中替换设置 |
| 凭据密文 | ✅ | `CredentialCipher` 从 `OPENBILICLAW_SECRET_KEY` 派生上下文隔离的 Fernet key；`source_accounts` repository 只接受 cipher 签发的 opaque `EncryptedCredential`，伪造 token 前缀会被拒绝 |
| 生产接线 | 🚧 | legacy storage/runtime 仍是权威路径；vNext API、任务服务、安装器 secret 交付和数据切换尚未实现 |

## Schema

| 数据域 | 表 |
|--------|----|
| 系统与来源 | `settings`, `source_accounts` |
| 活动与画像 | `activity_events`, `profile_revisions`, `profile_evidence` |
| 内容与推荐 | `content_items`, `candidate_assessments`, `feed_entries`, `interactions` |
| 本地集合与聊天 | `collections`, `collection_items`, `chat_turns` |
| 后续执行基础 | `source_tasks`, `job_runs`, `ai_runs` |

内容使用 `(source_id, external_id)` 作为跨源唯一身份；候选评估绑定 profile revision；画像 evidence 以外键关联活动证据；来源账户凭据列只保存 `encrypted_credentials`。SQLite engine 会打开 foreign keys，并在文件型 URL 下自动创建父目录。

## 公开 API

| 模块 | 公开契约 / 入口 |
|------|-----------------|
| `features.system` | `DatabaseSettings`, `UserSettings`, `SettingsService` |
| `infrastructure.database` | `create_engine_and_session()`, `UnitOfWork` |
| `infrastructure.database.repositories` | repository Protocol、SQLAlchemy adapter、`CollectionRecord`, `ProfileRevisionConflict` |
| `infrastructure.security` | `CredentialCipher`, `EncryptedCredential` |
| `infrastructure.security.credentials` | `MissingCredentialKeyError`, `SECRET_KEY_ENV` |

`UnitOfWork` 暴露 `settings`、`source_accounts`、`activities`、`profiles`、`content`、`assessments`、`feed`、`interactions`、`collections`、`chat`、`source_tasks`、`job_runs` 和 `ai_runs` repository。`source_tasks`、`job_runs`、`ai_runs` 当前只提供供后续任务继续构建的低层新增入口，不代表对应 worker 或 AI runner 已经上线。

`UserSettings` 的当前完整契约如下：

| 字段 | 默认值 | 约束 |
|------|--------|------|
| `onboarding_complete` | `false` | 严格 boolean |
| `feed_low_watermark` | `20` | `0..1000`，且不得高于 high watermark |
| `feed_high_watermark` | `50` | `1..2000` |
| `source_sync_interval_minutes` | `30` | `1..10080` |

## 迁移与安全约束

开发者可在仓库根目录执行 `alembic upgrade head` 创建 vNext 空库；迁移环境会先为 file-backed SQLite URL 创建缺失的父目录。该命令只操作 `alembic.ini` 指向的 vNext URL；不得把 legacy 数据库 URL 传给这套迁移，也不得用 `Base.metadata.create_all()` 代替版本化迁移。

凭据加解密要求进程提供非空 `OPENBILICLAW_SECRET_KEY`。数据库、日志、设置表和 AI run 均不得写入 plaintext credential 或派生 key；丢失或更换 secret 后，旧密文不可解密。当前安装器尚未接线该 secret，因此本模块不能被视为已经具备可升级的生产密钥生命周期。
