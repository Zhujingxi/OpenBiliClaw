# TLS Proxy 模块

> 代码：`src/openbiliclaw/tls_proxy.py`；部署说明：[`docs/https-deployment.md`](../https-deployment.md)。

## 定位

TLS Proxy 是默认关闭的 LAN / self-managed HTTPS 入口。它在 `:8443` 终止 TLS，并把
HTTP/1.1 与 WebSocket 流量转发到本机或 Compose 内网的 FastAPI。它不提供公网网关所需的
ACME、WAF、通用路由、限流或多 upstream 能力。

## 已实现功能

| 能力 | 状态 | 约束 |
|---|---|---|
| 同步启动准备 | ✅ | 证书检查、SSL context、socket bind 全部成功后才启动后台线程 |
| HTTP/1.1 转发 | ✅ | GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD；请求 chunked body 拒绝 |
| WebSocket | ✅ | 真实 Upgrade 握手后双向 raw relay；隧道结束即关闭连接，不回到 HTTP keep-alive 解析 |
| Origin/Host 防护 | ✅ | HTTPS Web Origin 与 Host host+port 精确匹配；Chrome/Firefox 扩展 scheme |
| Cookie 加固 | ✅ | 保留重复 `Set-Cookie`，TLS 出口补 `Secure` |
| 本地 CA | ✅ | 显式 opt-in 自动生成；证书下载仅公开 `ca.crt` / `ca.crl` |
| 健康检查 | ✅ | `GET/HEAD /healthz` 不依赖 CA/CRL 下载文件且不返回秘密；其他方法 405 并关闭连接 |
| SAN 漂移检测 | ✅ | 现有证书缺配置 SAN 时 fail loudly，从不静默覆盖 |
| Docker profile | ✅ | `docker compose --profile tls`，SAN 与端口由明确环境变量传入 |

## 数据流

```text
TLS client
  → parse Host + Origin / reject foreign Web origin
  → terminate TLS
  → rewrite validated Web Origin https→http for backend same-origin contract
  → forward to connectable API host (0.0.0.0→127.0.0.1, ::→::1)
  → strip hop-by-hop response headers / preserve duplicates / Secure cookies
  → TLS client
```

## 公开 API

| API | 说明 |
|---|---|
| `create_tls_proxy_server(...) -> ThreadingHTTPServer` | 同步校验证书、加载 TLS 并绑定 socket；不进入 serving loop |
| `start_tls_proxy(...) -> ThreadingHTTPServer` | 在调用线程执行 `serve_forever()`，适合独立容器入口 |
| `backend_connect_host(api_host) -> str` | 把 wildcard bind host 转成可连接 loopback |
| `ProxyHandler` | HTTP/1.1 / WebSocket handler；通常不由外部直接实例化 |

证书、Origin 和 header helper 以下划线开头，仅供模块实现与测试使用，不是兼容性 API。

## 失败语义

- TLS 配置为 enabled 时，缺少 `cryptography`、cert/key 半残、SAN 不匹配、证书不可解析、
  key 不匹配、SSL 初始化或端口绑定失败都会阻止 `serve-api` 启动。
- 已存在的证书/私钥永不被自动覆盖。
- 没有配置远程 SAN 的自动证书仅承诺 localhost，不承诺远程 IP/hostname 可用。
- 代理错误不会使未启用 TLS 的默认 HTTP 路径发生变化。

## 测试

`tests/test_tls_proxy.py` 覆盖配置 round-trip/CLI、IPv4/IPv6 Origin、证书生成与 SAN 漂移、
同步启动失败、真实 HTTPS GET/POST/HEAD、重复 Secure cookie、私钥拒绝和 WebSocket 双向帧中继。
