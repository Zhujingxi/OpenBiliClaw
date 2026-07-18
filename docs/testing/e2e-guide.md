# E2E 测试指南

本文档说明 OpenBiliClaw 的端到端（E2E）测试体系，包括共享认证 fixture、隔离策略和运行方式。

## 共享 E2E 认证 Fixture

位置：`tests/e2e_auth_fixtures.py`

该模块为需要真实 FastAPI app + 数据库的 E2E 测试提供统一的认证绕过方案，避免每个测试文件重复实现 app 构建逻辑。

### 两种认证策略

#### 1. Loopback 绕过（默认）

适用于宿主机侧测试，无需 Chrome 插件或真实 token。

- `build_e2e_app(tmp_path, monkeypatch, ...)`：构建启用 auth 但 `trust_loopback=True` 的 app + 真实 SQLite 数据库
- `start_loopback_server(app)`：在后台线程启动 uvicorn，绑定到 `127.0.0.1` 的随机空闲端口
- `loopback_client(base_url)`：返回不带 `Origin` / `Sec-Fetch` 头的 `httpx.Client`

**原理**：`src/openbiliclaw/api/auth.py` 的 `AuthGate.is_trusted_local` 允许来自 `127.0.0.1` 且不携带跨源浏览器头的请求跳过 token 检查。fixture 的 client 刻意不发送 `Origin` / `Sec-Fetch-*`，以命中该绕过路径。

**注意**：不要给 `loopback_client` 添加 `Origin` 或 `Sec-Fetch-Site` 头——浏览器形态的跨源头会使绕过失效，请求会回落到 token 认证并返回 401。

#### 2. Extension-Token 交换（ opt-in ）

适用于后续需要在容器内加载真实 Chrome 插件的测试（浏览器不是 loopback peer）。

```python
app, db = build_e2e_app(
    tmp_path,
    monkeypatch,
    extension_access_enabled=True,
    extension_access_keys=(E2E_EXTENSION_DEVICE_KEY_RECORD,),
)
token = mint_extension_token(server.base_url)
```

- `E2E_EXTENSION_DEVICE_KEY_RECORD`：预生成的测试设备密钥记录（digest-only，配置安全）
- `mint_extension_token(base_url, device_key)`：调用 `POST /api/auth/extension-token` 换取签名 Bearer token
- 该 token 可通过 `Authorization: Bearer ***` 头用于非 loopback 请求

**注意**：Docker-compose E2E 场景仍需要此路径，因为容器内的浏览器不是宿主机 loopback peer。

### 快速开始

```python
from tests.e2e_auth_fixtures import (
    LoopbackServer,
    build_e2e_app,
    loopback_client,
    loopback_test_client,
)

def test_my_e2e(tmp_path, monkeypatch):
    # 方式 A：真实 HTTP 服务器（推荐用于真正 E2E）
    app, db = build_e2e_app(tmp_path, monkeypatch)
    with LoopbackServer(app) as server, loopback_client(server.base_url) as client:
        resp = client.get("/api/favorites/BV1E2E")
        assert resp.status_code == 200

    # 方式 B：进程内 TestClient（适用于 SQLite 跨线程限制场景）
    client = loopback_test_client(app)
    assert client.get("/api/favorites/BV1E2E").status_code == 200
```

###  fixture 自身的单元测试

`tests/test_e2e_auth_fixtures.py` 验证：

- loopback 请求无 token 时通过
- 携带 `Origin: http://evil.example` 的跨源请求返回 401
- `Sec-Fetch-Site: cross-site` 请求返回 401
- extension-token 交换 + Bearer 认证在启用时工作
- 错误的设备密钥返回 401

## 已迁移的 E2E 测试

- `tests/test_xhs_e2e_smoke.py`：小红书安全发现 pipeline 的 smoke 测试，使用 `build_e2e_app` + `loopback_test_client`

## 运行方式

```bash
# 运行 fixture 单元测试（快速，无外部依赖）
.venv/bin/pytest tests/test_e2e_auth_fixtures.py -q

# 运行 xhs smoke E2E（需要 XHS_E2E_SMOKE=1）
XHS_E2E_SMOKE=1 .venv/bin/pytest tests/test_xhs_e2e_smoke.py -q

# 运行 Bili 扩展浏览器 E2E（需要 BILI_EXTENSION_E2E=1 + Chrome）
BILI_EXTENSION_E2E=1 .venv/bin/pytest tests/test_bili_extension_browser_e2e.py -q -s
```

## 隔离的 17 个 E2E 测试文件

以下文件因需要真实浏览器/插件环境而被隔离（`skipif` 门控），默认不运行：

- `tests/test_bili_extension_browser_e2e.py`（`BILI_EXTENSION_E2E=1`）
- `tests/test_xhs_browser_e2e.py`（`XHS_BROWSER_E2E=1`）
- `tests/test_web_guided_init_e2e.py`
- `tests/test_phase7_e2e.py`
- ...（完整列表可用 `grep -rln "skipif" tests/ | grep e2e` 枚举；各文件的门控变量以其自身 `pytestmark` 为准）

迁移这些文件到共享 fixture 是渐进式的；每个文件迁移时应保持其 `skipif` 环境门控不变。
