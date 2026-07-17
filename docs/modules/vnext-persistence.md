# vNext 持久化与系统设置

> Runtime update: the vNext API and operational CLI now use this database as
> authority. `openbiliclaw db migrate` and `openbiliclaw db backup` are supported;
> legacy data remains
> untouched and is not imported.

## 状态与边界

本模块是权威 vNext 后端的持久化层，已经实现 SQLAlchemy 2.x 映射、repository、同步 Unit of Work、Alembic 基线迁移、类型化系统设置和 Fernet 凭据密文适配。它使用新的 `data/vnext/openbiliclaw.db`，不读取、迁移或替换 `storage/database.py` 管理的历史 v0.3 数据库。

`/api/v1`、独立 worker、运维 CLI 和 Web/extension generated clients 共同使用这套基础；应用数据库是设置、来源账户、活动、画像、Feed、library、chat、source task、job 与 AI run 的业务权威。Docker 与 source installer 都生成并复用来源加密 secret，先完成唯一 migration owner 的 schema 写入，再让 API/worker 执行只读 head gate。历史数据仅保留为不导入的手工 archive。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 独立数据库配置 | ✅ | `DatabaseSettings` 默认指向 `sqlite:///data/vnext/openbiliclaw.db`；URL、echo 与有限 SQLite busy timeout 可通过 `OPENBILICLAW_DATABASE_URL` / `OPENBILICLAW_DATABASE_ECHO` / `OPENBILICLAW_DATABASE_BUSY_TIMEOUT_SECONDS` 覆盖 |
| SQLAlchemy schema | ✅ | 17 张 vNext 业务表覆盖设置、非秘密 auth revocation state、来源账户、活动、画像与独立 consumed-evidence ledger、内容、Feed、集合、聊天、来源任务、后台任务和 AI run |
| Alembic revisions | ✅ | `0001_vnext_baseline` 创建业务基线并预置 `favorites` / `watch_later`；`0002_auth_state` 创建 `auth_state` 并 seed `session_epoch=0`；`0003_job_run_leases` 增加 worker fencing/lease；`0004_job_success_continuations` 增加成功 continuation durable acknowledgement，支持逐级 upgrade/downgrade |
| runtime schema gate | ✅ | installer 或 Compose `migrate` 独占 Alembic 写入；API/worker startup 只读验证当前 revision 精确等于 head |
| worker runtime health | ✅ | Compose probe 要求 PID 1 为正式 worker、schema 位于 head；独立 Huey SQLite 通过 integrity check，并保持生产 WAL。POSIX 正常 pathname connect 前固定 main 与已存在 WAL/SHM identity；connect 后新增的全部普通 FD 必须属于该集合且包含 main，拒绝并发无关 FD/dup 冒充。Windows 在稳定 root 边界内验证 connection 前后 pathname/held identity。随后执行 `BEGIN IMMEDIATE`、真实 `CREATE`/`INSERT`、`ROLLBACK`，不修改 journal mode 或留下 probe artifact |
| 安全在线备份 | ✅ | `db backup` 仅在 Linux/macOS 支持：main 与已存在的 WAL/SHM/journal 先以 `O_NOFOLLOW` FD 固定；SQLite read connection 打开前记录 process FD set，连接后与 `sqlite3_backup` 后都要求新增的全部 regular FD 只属于这些 pinned identities 且包含 main，同时复核原 pathname/sidecar identities。source snapshot 不创建 named hard-link、staging directory 或 cleanup pathname。macOS payload 每次以不可预测名称 `O_EXCL` 新建，在任何 snapshot bytes 写入前核对 held/path identity、立即 unlink 并验证 link count 为零；从不打开或复用既有 payload，因此预先持有旧文件 read FD 的进程不能观察 snapshot，再以 held FD `fclonefileat` 原子 no-replace 发布并验证 exact bytes 与 SQLite integrity。Linux 使用 unlinked `O_TMPFILE` + `linkat(AT_EMPTY_PATH)`，capability policy 拒绝时仅在 `/proc/self/fd` 复核 held inode 后 fallback。最终名称仍在 directory sync 后复核 pathname/held FD，且任何 late failure 都不 pathname-delete 已发布 backup。Windows/其他缺少安全 primitive 的平台在 destination reservation 前失败关闭 |
| Repository + UoW | ✅ | 领域对象经同步 repository 持久化；`UnitOfWork` 只在显式 `commit()` 时提交，退出时统一 rollback 并关闭 session。`activity_events.id` 与 `favorites` / `watch_later` membership 都使用 SQLite conflict-safe insert；并发扩展重试与重复 collection save 返回幂等成功，不产生 `IntegrityError` / HTTP 500。同一 event ID 的冲突 payload 重新读取首次持久化行并只投影其 profile signal，避免未入库内容形成 phantom evidence |
| 画像并发保护 | ✅ | `ProfileRepository.append()` 使用 expected revision 检查，拒绝陈旧修订和画像 ID 漂移；`profile_consumed_evidence` 与 revision 在同一事务提交/回滚 |
| 类型化用户设置 | ✅ | `SettingsService` 先合并默认值并严格校验完整 `UserSettings`，然后用 conflict-safe insert 补齐缺失默认 row、仅 upsert PATCH 涉及的 top-level key；onboarding completion 只单调更新自己的 key，不覆盖并发设置 |
| Session revocation | ✅ | `SQLAlchemyAuthStateRepository` 维护 non-secret `session_epoch` 与 keyed password fingerprint/state；首次 enable 记录 fingerprint 不 bump，rotation、removal (`disabled` sentinel) 与 re-enable 在同一事务更新 state 并 increment epoch，使旧 Web/extension session 原子失效且不能复活；表内不保存 password/hash、cookie、bearer、device key 或 signing secret |
| 来源 settings | ✅ | 七平台 strict settings 复用现有 `settings` table 的 `source-config:<source_id>` namespaced row；global settings replace 保留这些 rows。API 先通过 schema-head gate 再构造 settings-backed registry；只有 Douyin `mode` 是有 runtime consumer 的 per-source 字段，其它六个平台 schema 为空 |
| 凭据密文 | ✅ | `CredentialCipher` 从 `OPENBILICLAW_SECRET_KEY` 派生上下文隔离的 Fernet key；`source_accounts` repository 只接受 cipher 签发的 opaque `EncryptedCredential`，伪造 token 前缀会被拒绝 |
| worker 接线 | ✅ | 独立 Huey worker 使用同一 UoW 执行 activity/profile/feed/job 用例；`job_runs` 是产品任务状态权威 |
| 生产切换 | ✅ | `/api/v1`、worker、运维 CLI、安装器 secret 生命周期、fresh vNext database 与 Web/extension clients 已是权威 |

## Schema

| 数据域 | 表 |
|--------|----|
| 系统、鉴权与来源 | `settings`, `auth_state`, `source_accounts` |
| 活动与画像 | `activity_events`, `profile_revisions`, `profile_evidence`, `profile_consumed_evidence` |
| 内容与推荐 | `content_items`, `candidate_assessments`, `feed_entries`, `interactions` |
| 本地集合与聊天 | `collections`, `collection_items`, `chat_turns` |
| 执行与审计 | `source_tasks`, `job_runs`, `ai_runs` |

内容使用 `(source_id, external_id)` 作为跨源唯一身份；library adapter 用一次 join
读取 collection membership 与 normalized content，并按 `added_at,id` 确定性排序，避免
N+1。chat history 按 `conversation_id` 隔离并按 `created_at,id` 升序分页，公开投影不含
`ai_run_id`。候选评估绑定 profile revision；画像 facet evidence 与独立 consumed ledger
都以外键关联活动证据，后者不受后续 facet 删除影响。Feed repository 以同一 seen 定义读取
unseen entry 的 source 与 assessment topics，补池 diversity cap 因此覆盖跨批次既有卡片，而不只
统计当前 candidate batch。显式 profile edit 的 override
event、revision、evidence association 与 consumed ledger 在同一 UoW 提交；每个显式 revision
取得新的 aware UTC `created_at`，即使注入 clock 没推进，也至少比上一 revision 新 1 微秒。
`job_runs.dispatched_at` 是 DB→Huey 成功 handoff marker，但不是“消息仍在 queue”的证明：
worker startup 会重新发布全部 pending row。`continuation_completed_at` 只在成功 row 的全部注册
callback 完成后写入；startup 与 live lifecycle sweep 都重放未确认 continuation，确认后停止重放。
retention cleanup 用同一条条件 DELETE 仅删除已确认的 succeeded row，因此 cleanup 与 acknowledgement
按 SQLite write order 串行时，不会在 replay effect 与确认之间删除权威 row；failed/cancelled 仍按原期限删除。
progress 只允许单调前进。来源账户凭据列只
保存 `encrypted_credentials`；disconnect 物理删除该 account row，重复调用成功且标记
idempotent。`source_tasks.request_deadline_at` 保存绝对请求截止时间，使过期任务即使清理
延迟也不可再 claim。SQLite engine 会打开 foreign keys、把 `busy_timeout_seconds` 同时
配置到 driver timeout 与 `PRAGMA busy_timeout`，并在文件型 URL 下自动创建父目录。

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

`UnitOfWork` 暴露 `settings`、`source_accounts`、`activities`、`profiles`、`content`、`assessments`、`feed`、`interactions`、`collections`、`chat`、`source_tasks`、`job_runs` 和 `ai_runs` repository。`activities` 支持幂等导入，profile repository 提供独立 consumed ledger，assessment adapter 可查询同 revision 已评估与历史 admitted/interacted 内容，`job_runs` 提供幂等 schedule、全部/未发布 pending 查询、dispatch reconciliation、原子 claim、条件 running transaction guard、运行中取消、progress checkpoint、重启恢复和 terminal cleanup。四个 worker handler 在 feature 写事务内先执行 guard；cancel/checkpoint/terminal-or-retry/recovery 也都以条件 UPDATE 开始，不经过 SELECT→ORM flush 的 SQLite lock upgrade。两个竞争事务按 write order 与有限 busy timeout 串行：cancel 先则 feature 无 effect，guard 先则 feature commit 先于 cancelled state；timeout exhaustion 保持显式失败。worker 中的 `TaskRunner` 会写 `ai_runs`，但该表只记录 task、model alias、状态、时间、usage 与错误分类；ORM、Alembic 基线和 repository API 均不含 input/output payload，不依赖启发式脱敏，避免应用数据库成为内容或 provider credential 的旁路持久化通道。`source_tasks` 由 queued browser transport 持久化，并由 extension 的 generic `/api/v1/source-tasks` claim/complete dispatcher 消费；它们不再承担平台专用 endpoint 的兼容职责。

`UserSettings` 的当前完整契约是 nested strict groups，而不是旧 flat keys：

| group | persisted mutable state | deployment/read-only projection |
|---|---|---|
| `sources` | `enabled`, `weights` | — |
| `schedules` | source sync/profile projection/feed replenishment/cleanup interval minutes | — |
| `feed` | watermarks, candidate/batch limits, score/novelty thresholds, per-source/topic caps | — |
| `profile` | `minimum_evidence_confidence` | — |
| `tasks` | per-task alias, semantic retries, timeout, request/token limits | — |
| `network` | direct/system/custom mode and credential-free proxy URL | — |
| `logging` | console/file levels | `directory` |
| `access_control` | Web/loopback/extension behavior and TTLs | bearer/password configured booleans |
| `jobs` | `retention_days` | `worker_concurrency` |

完整字段、默认值和 bounds 见 [vNext 配置](config.md)。repository 以 top-level key 为原子更新单位；
service 先递归 merge partial patch、拒绝 read-only path、再校验完整 model，只把 patch 涉及的 key
写回。缺失默认值使用 `ON CONFLICT DO NOTHING` 补齐，因而两个 session 的 disjoint settings PATCH
以及 onboarding completion 不会互相替换；deployment facts 在每次 GET overlay，不落入可写 product state。

## 迁移与安全约束

开发者可在仓库根目录执行 `alembic upgrade head` 创建 vNext 空库并依次应用 `0001`
至 `0004`；迁移环境会先为 file-backed SQLite URL 创建缺失的父目录。API/worker 要求
revision 精确位于 head，不能在运行时隐式创建 `auth_state`。API container construction 的
source registry holder 是 zero-I/O；startup 先执行该 gate，再读取 `source-config:*` 并安装
registry，因此 stale schema 不会被提前的 settings read 掩盖。该命令只操作 `alembic.ini`
指向的 vNext URL；不得把 legacy 数据库 URL 传给这套迁移，也不得用
`Base.metadata.create_all()` 代替版本化迁移。

凭据加解密要求进程提供非空 `OPENBILICLAW_SECRET_KEY`。数据库、日志、设置表和 AI run 均不得写入 plaintext credential 或派生 key；丢失或更换 secret 后，旧密文不可解密。Docker 与 source installer 在私密 `.env` 中生成并幂等复用该 secret，以 mode `0600`、symlink 拒绝、同目录临时文件、`fsync` 和原子替换保护它；安装输出与 OpenAPI 都不暴露值。
