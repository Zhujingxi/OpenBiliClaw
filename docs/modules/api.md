# API 模块（`src/openbiliclaw/api/`）

## 概述

`api/` 承载本地 HTTP / WebAPI 服务层：`api/app.py` 的 `create_app()` 是唯一应用组装入口，负责中间件、静态壳、`/api/*` 端点与 WebSocket 的注册；`api/dependencies.py` 与 `api/routes/` 是增量式架构重构（`docs/plans/2026-07-19-incremental-architecture-refactor-plan.md`）引入的**窄路由提取边界**。

- `api/app.py` — 应用组装单入口。除已提取的试点路由外，其余端点仍以内联 `@app.get/post/...` 形式留在此处，按后续切片逐步外移。degraded-mode guard、`API Auth Gateway` 中间件、静态资源挂载与 `_IncludedRouter` 惰性包装都在这里完成。
- `api/dependencies.py` — 冻结 dataclass 形式的**窄依赖包**（如 `SystemRouteDeps(get_lan_ip=...)`、`HealthRouteDeps`）。每个路由模块只声明自己需要的最小 callable 集合，由 `create_app()` 从既有闭包构造并注入。明确**不引入** `ApiServices` 服务定位器；任何 `deps.services.<engine>` 形式的 reach-through 都会被架构棘轮（`tests/test_architecture.py`）机械拒绝。
- `api/routes/system.py` — Phase 1 试点提取：`GET /api/ping`（纯 liveness 探针，无 DB / provider 往返）与 `GET /api/qr-info`（移动扫码用的 LAN IP 查询）。`build_system_router(deps)` 工厂返回 `APIRouter`，由 `create_app()` 在原内联注册位置 `include_router`，路由匹配顺序与对外行为与旧内联实现字节级一致。
- `api/routes/health.py` — Phase 1 试点 2 提取：`GET /api/health`（readiness 探针含 embedding、profile、LAN IP 与 degraded payload）与 `GET /api/init-status`（guided-init 状态与前置检查清单）。与 `api/routes/system.py` 同一窄依赖模式，外部行为字节级一致。

## 已实现功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 试点系统路由提取 | ✅ | `/api/ping`、`/api/qr-info` 从 `api/app.py` 内联迁移到 `api/routes/system.py` 的窄 router 工厂；响应体与 content-type 由 `tests/test_api_pilot_endpoints_contract.py` 精确锁定（含序列化字节）。 |
| 试点健康/状态路由提取 | ✅ | `/api/health`、`/api/init-status` 从 `api/app.py` 内联迁移到 `api/routes/health.py`；`HealthRouteDeps` 窄依赖包包含 embedding 探针、degraded 状态、init-coordinator 等闭包。 |
| 窄依赖包注入 | ✅ | `SystemRouteDeps`、`HealthRouteDeps` 各自只暴露所需的最小 callable 集合；路由模块不 import `storage.*`、`memory.*`、`soul.*`、`bilibili.*`、`discovery.*` 或 `api.runtime_context`。 |
| 路由契约清单 | ✅ | `tests/contracts/api-route-contract.json` 锁定 134 条 `app.routes`（含 WebSocket / Mount 条目）与 121 个 OpenAPI 操作的注册顺序、参数、请求/响应 media-type 与归一化 schema；由 `scripts/generate_api_route_contract.py` 生成，重复注册检测覆盖 HTTP / WS / Mount 三类条目。 |
| 架构棘轮 | ✅ | `tests/test_architecture.py` 对 `api/routes/*` 与 `api/dependencies.py` 做 AST 强制：禁止服务定位器类型导入、广口容器模块导入、`deps.services.<x>` 访问、直接构造 `Database` / 平台客户端；自带反例自测保证棘轮可失败。 |

## 对外契约

- `GET /api/ping` → `200 application/json`，body 精确为 `{"status":"ok","service":"openbiliclaw-api"}`。
- `GET /api/qr-info` → `200 application/json`，body 精确为 `{"lan_ip":"<ipv4>"}` 或 `{"lan_ip":null}`。
- `GET /api/health` → `200 application/json`，正常态包含 `status/embedding_ready/lan_ip/profile_ready`；degraded 态包含 `status/reason/issues/embedding_ready` 并保持 200 状态码。
- `GET /api/init-status` → `200 application/json`，精确的 InitStatusOut 模型含初始化状态、前置检查清单与 last_failure 信息。
- 两条系统端点都跳过 `/api/health` 的 embedding readiness 探针，保证插件连接徽标与移动扫码抽屉不被冷 Ollama 模型加载阻塞。

其余 `/api/*` 端点的对外契约仍由 `api/app.py` 内联实现持有，并同受路由契约清单保护；后续切片外移时必须先更新契约清单再动实现。

## 公共 API

- `create_app(...)`（`api/app.py`）— 组装并返回 FastAPI 应用；无参调用构造 degraded-mode 应用（路由齐全、handler 可返回 503）。
- `SystemRouteDeps`（`api/dependencies.py`）— 系统路由窄依赖包（冻结 dataclass）。
- `HealthRouteDeps`（`api/dependencies.py`）— 健康/状态路由窄依赖包（冻结 dataclass），含 LAN-IP、embedding 探针、degraded 状态与 init-coordinator 闭包。
- `build_system_router(deps)`（`api/routes/system.py`）— 构造系统 liveness / QR-info 路由。
- `build_health_router(deps)`（`api/routes/health.py`）— 构造健康/init-status 路由。

## 相关文档

- 增量式架构重构计划：[`docs/plans/2026-07-19-incremental-architecture-refactor-plan.md`](../plans/2026-07-19-incremental-architecture-refactor-plan.md)
- API Auth Gateway：[`modules/api-auth.md`](api-auth.md)
- 架构总览：[`docs/architecture.md`](../architecture.md)
