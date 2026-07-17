# Docker 部署（vNext）

[← README](../README.md)

Docker 是推荐安装方式。Compose 运行一次性 `migrate`、`api`、独立 `worker`、
LiteLLM Proxy 和 LiteLLM PostgreSQL。OpenBiliClaw 应用数据仍是 SQLite；
PostgreSQL 只属于 LiteLLM Admin 与其配置。

## 快速开始

源码构建：

```bash
git clone https://github.com/whiteguo233/OpenBiliClaw.git
cd OpenBiliClaw
MODE=docker bash scripts/install.sh
```

预构建镜像：

```bash
mkdir -p ~/openbiliclaw/litellm
cd ~/openbiliclaw
curl -fsSLO https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/docker-compose.prebuilt.yml
curl -fsSL https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/litellm/config.yaml -o litellm/config.yaml
python3 /path/to/OpenBiliClaw/scripts/agent_bootstrap.py --project-dir "$PWD" --mode docker
```

The prebuilt bootstrap command requires the repository script to be available. For
the simplest supported path, use the source checkout command above; it still runs all
services in containers.

## 私密环境

安装器在 `.env` 中生成并幂等复用：

- `LITELLM_POSTGRES_PASSWORD`
- `LITELLM_MASTER_KEY`
- `OPENBILICLAW_SECRET_KEY`
- `OPENBILICLAW_ACCESS_TOKEN`

文件以 mode `0600` 原子写入并被 Git 忽略。不要把 provider key 写进该文件或
Compose；provider credentials 只进入 LiteLLM Admin。`OPENBILICLAW_SECRET_KEY`
加密来源账户，`OPENBILICLAW_ACCESS_TOKEN` 保护 `/api/v1` 业务接口。

要启用 Web/extension browser auth，另在私密 `.env` 或 deployment secret store 中
provision：

- `OPENBILICLAW_WEB_PASSWORD_HASH`：只放 scrypt hash，不放明文密码；
- `OPENBILICLAW_SESSION_SECRET`：独立随机 signing secret；
- `OPENBILICLAW_EXTENSION_ACCESS_KEYS`：只放 `key-id:sha256-digest` JSON array；完整
  `obc_ext_...` device key 只在生成时交付给目标 extension；
- `OPENBILICLAW_LITELLM_ADMIN_URL`：可选 public navigation URL，不是 internal proxy URL/key。

生成/录入过程必须直接写入 mode-`0600` 文件或 secret manager，不能把值放入命令参数、
shell history、installer status、Compose log、截图或文档。Web password hash、session secret、
device key/digest 各自独立，不能复用 `OPENBILICLAW_ACCESS_TOKEN`、
`OPENBILICLAW_SECRET_KEY` 或 `LITELLM_MASTER_KEY`。API settings 只回报 password/bearer 是否
已配置，不返回任何值。

源码 `docker-compose.yml` 与预构建 `docker-compose.prebuilt.yml` 都把以上四个变量
原样转发给 `api` service；`OPENBILICLAW_SESSION_SECRET` 是 Compose required value，另外
三个可为空/`[]`。Browser-auth material 不转发给 worker。vNext API 只读取这些 environment
values，不从 legacy `config.toml` 补值。部署检查应验证 Compose render，而不是输出变量值。

## 数据与 queue 一致性

API 与 worker 都挂载：

```text
openbiliclaw_data:/app/runtime/data
```

并使用完全相同的路径：

```text
OPENBILICLAW_DATABASE_URL=sqlite:////app/runtime/data/vnext/openbiliclaw.db
OPENBILICLAW_HUEY_PATH=/app/runtime/data/vnext/huey.db
```

应用 SQLite 和 Huey SQLite 是两个文件。Huey result store 是 transport 状态；
用户可见 job 状态以应用库 `job_runs` 为准。不要给 API 与 worker 配不同 volume
或 host path，否则任务会排入 worker 看不到的 queue。

## Migration 所有权

`migrate` 是 Compose 中唯一执行 `openbiliclaw db migrate` 的服务。`api` 与
`worker` 都以 `service_completed_successfully` 等待它，并在自身 startup 只读检查
应用库 revision 是否等于 Alembic head，不再竞争 SQLite DDL。migration 失败时两个
长期服务都不会启动；重复 `docker compose up` 可安全重跑幂等 migration。Source / uv
安装仍由 installer 在启动 API/worker 之前完成同一 migration。

`SKIP_START=1 MODE=docker bash scripts/install.sh` 仍会执行
`docker compose run --rm migrate`，只是不启动长期服务。因此 `*_runtime_prepared`
明确表示数据库已经迁移，而不是只生成了 `.env`。

## LiteLLM

Admin 只绑定 `127.0.0.1:${LITELLM_PORT:-4000}`。打开
`http://127.0.0.1:4000/ui`，配置 provider credentials 与 routing，并建立：

- `obc-interactive`
- `obc-analysis`
- `obc-embedding`

LiteLLM 负责 provider、fallback、cooldown、网络 retry、rate limit、budget 和
cache。OpenBiliClaw 不提供重复的 provider editor。
若要让 Web client 显示这个入口，显式设置
`OPENBILICLAW_LITELLM_ADMIN_URL=http://127.0.0.1:4000/ui`。API 不会把容器内
`http://litellm:4000` 或 master key 暴露给浏览器；远程 URL 必须由部署者先提供 TLS、
访问控制与防火墙。

## 健康检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:8420/api/v1/system/readiness
curl -fsS http://127.0.0.1:4000/health/readiness
docker compose logs worker
```

API public readiness 只表示进程可服务。Installer 还使用生成的 bearer secret
请求受保护 settings 端点，确保 access control 与数据库依赖实际可用；它不会
把 token 输出到终端。Docker installer 同时解析 `docker compose ps --all --format
json`，要求 `migrate` 已成功退出且 API、worker 都是 `running/healthy`；worker
healthcheck 不只检查文件存在，而是验证容器 PID 1 是正式 worker、应用 schema 位于
Alembic head、Huey SQLite 完整且能取得可回滚的写事务。`restarting`、`exited` 或
`unhealthy` worker 都会使安装非零失败，不能被 API readiness 掩盖。受保护 API probe
完成后 installer 会再次读取相同 Compose 状态，probe 期间转为 crash-loop 也会失败。
Queue writable probe 使用正常 queue pathname，保留 Huey 的 WAL 与 parent journal 语义；
POSIX 在 connection 前预先固定 main 与已存在 WAL/SHM identity，connection 后枚举 process
FD；全部新增普通 FD 必须属于该集合且 main 必须出现，拒绝并发 held dup 或无关普通 FD
冒充 SQLite connection，再以 `BEGIN IMMEDIATE` 执行真实 `CREATE`/`INSERT` 后 rollback。Windows 在稳定
checkout-root 边界内执行 connection 前后 pathname/held identity 校验。Source installer
在 migration 后启动 API/worker、等待 queue
文件就绪，再运行 `openbiliclaw doctor` 检查应用数据库、access token 和 LiteLLM
配置；任一步失败都会停止这两个新进程并非零退出。POSIX held checkout-root lock、稳定
root guard 与内层 lifecycle anchor 共同串行，并持久化完整 installer state、generation 和
anchor UUID/device/inode；guard 校验全部 complete history，并为每代写入相同 pending/committed
记录。取得锁、进入业务、generation 更新及退出时均精确复核，并共享一个
截止时间。POSIX 逐级 held-FD 遍历 `data/vnext`，仅在普通文件、单链接、owner、私密 mode
与 pathname identity 成立时原位重绑崩溃遗留的未绑定 anchor；native Windows 在 root guard
内仅恢复 non-reparse、普通文件、单链接且 held/path identity 一致的 orphan，不声称等价 ACL
保证。持锁后会重读绑定，已绑定 pathname
缺失/替换或 symlink/junction ancestor 都会失败关闭。

## 来源与 onboarding

七个 connector 在 composition 时显式注册，不是动态 plugin。来源默认关闭；
通过 `/api/v1/sources` 的 write-only credential form 写入账号配置，通过
`/api/v1/onboarding` 启动 bootstrap。manifest 自描述 safe settings/credential 和每项
operation schema；断开账号使用 typed idempotent DELETE，只删除 encrypted material。
浏览器辅助任务统一使用 `/api/v1/source-tasks/claim` 与 typed completion endpoint。

现有 static Web/extension 仍被挂载，但其 vNext API client wiring 在 Task 22；
在此之前用 OpenAPI/API tests 验证，不要使用旧 UI 配置来源或 AI。

## 运维

```bash
docker compose up -d --build
docker compose pull
docker compose logs -f api worker
docker compose down
```

`down` 不删除 named volumes。只有明确需要销毁 vNext 数据时才使用 `down -v`；
旧数据文件保持原样作为手工 archive，不做自动导入。
