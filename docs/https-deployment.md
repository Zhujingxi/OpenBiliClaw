# 可选 HTTPS / TLS 反代部署

> 面向可信局域网或自管网络的轻量 TLS 入口。默认关闭，不是公网生产级反向代理。

默认的 `http://127.0.0.1:8420` 和局域网 HTTP 行为不变。只有远程浏览器策略要求
HTTPS、且你愿意在客户端安装本地 CA（或提供自己的证书）时，才需要启用本组件。
公网入口应使用 Caddy、nginx、Traefik 等成熟网关并完成防火墙、限流和证书自动续期。

## 访问路径与安全边界

```text
HTTP（默认）: 客户端 ───────────────→ FastAPI :8420
HTTPS（可选）: 客户端 → TLS Proxy :8443 → FastAPI :8420（本机/Compose 内网 HTTP）
```

- Web Origin 必须是 `https://`，并与请求 `Host` 的规范化 host+port 精确一致；
  `https://evil.example:8443` 不会因为端口相同而放行。
- 扩展 Origin 只接受结构合法的 `chrome-extension://` 与 `moz-extension://`。
- 无 `Origin` 的 CLI、健康检查等非浏览器调用可继续使用。
- TLS 响应中的 cookie 会补 `Secure`；重复 `Set-Cookie` 不会被合并。
- 证书下载端点只公开 `ca.crt` 与 `ca.crl`；另有不含秘密的 `/healthz`。`ca.key` /
  `srv.key` 会在代理层直接返回 404。
- HTTP/1.1 GET、POST、HEAD 与 WebSocket Upgrade 会转发；chunked **请求体**不支持。
- TLS 不替代密码门禁。局域网暴露时仍建议配置 `openbiliclaw set-password`；远程扩展
  仍需默认关闭的 `ext-key` 设备认证。

## 非 Docker 部署

```bash
# 1. 安装可选证书依赖
uv sync --extra tls
# pip 安装也可使用: pip install "openbiliclaw[tls]"

# 2. 写入持久配置；SAN 必须是客户端实际访问的 IP/hostname
uv run openbiliclaw tls-proxy enable --san 192.168.1.20 --san openbiliclaw.lan

# 3. TLS 代理只跟随 serve-api 启动
uv run openbiliclaw serve-api
```

打开：

- 桌面 Web：`https://192.168.1.20:8443/web`
- 移动 Web：`https://192.168.1.20:8443/m/`
- 扩展后端地址：`https://192.168.1.20:8443`

`openbiliclaw start` 当前不启动 TLS 入口；使用该功能时应运行 `serve-api`。TLS 已启用时，
证书解析、SSL context 或端口绑定失败会让 `serve-api` 以非零状态退出，不会静默退回 HTTP。

## Docker Compose（源码 compose）

首次启动前设置远程客户端会使用的 SAN：

```bash
export OPENBILICLAW_TLS_SAN_NAMES="192.168.1.20,openbiliclaw.lan"
export OPENBILICLAW_TLS_PORT=8443
docker compose --profile tls up -d --build
```

Compose 把 `OPENBILICLAW_TLS_SAN_NAMES` 传为代理容器内的 `SAN_NAMES`，两者都是逗号分隔的
hostname/IP 列表。`OPENBILICLAW_TLS_PORT` 同时控制宿主机映射端口和容器监听端口。
`docker-compose.prebuilt.yml` 当前不包含该 profile；预构建部署请使用成熟外部网关或切换到
源码 `docker-compose.yml`。

查看明确的证书/SAN/绑定错误：

```bash
docker compose logs openbiliclaw-tls-proxy
```

## 证书与 SAN

自动生成必须显式开启（CLI 集成与 Compose profile 已明确开启）。首次生成包含：

- 固定本机 SAN：`localhost`、`127.0.0.1`
- 配置的 `san_names` / `SAN_NAMES`
- 本地 CA、服务器证书和 CRL；RSA 2048，有效期 3650 天

**没有远程 SAN 时，自动证书只适合 localhost。** 服务即使监听 `0.0.0.0:8443`，也不
代表证书可用于局域网 IP；客户端会正确报告 hostname mismatch。

已有 `srv.crt` + `srv.key` 时代理绝不覆盖。若配置新增 SAN，而现有证书不包含它，启动会
明确失败并列出缺失项。重签步骤：

1. 停止 TLS 代理；
2. 备份 `cert_dir`（Docker 为 `openbiliclaw_certs` volume）；
3. 自有证书：签发包含全部配置 SAN 的新 `srv.crt` / `srv.key` 后原位替换；
4. 自动生成证书：把旧的 `ca.crt`、`ca.key`、`ca.crl`、`srv.crt`、`srv.key` **移到备份目录**，
   确认活动目录不再有半套 cert/key 后重启；
5. 在客户端重新信任新 CA。

只存在 cert 或 key 其中一个时，代理会 fail loudly，不会在半残目录里补写另一半。

### 信任本地 CA

首次可用忽略校验的命令下载 CA（只下载公钥证书）：

```bash
curl --insecure https://192.168.1.20:8443/ca.crt -o openbiliclaw-ca.crt
```

- Windows：导入「受信任的根证书颁发机构」。
- macOS：钥匙串访问 → 系统 → 导入，并明确设为信任。
- Linux/Chrome：按发行版系统 CA 或 NSS 数据库流程导入。

不要复制、下载或分享 `ca.key` / `srv.key`。

## 配置与环境变量

非 Docker `Config` 的显式覆盖范围只有：

| 环境变量 | 对应配置 |
|---|---|
| `OPENBILICLAW_TLS_PROXY_ENABLED` | `[tls_proxy].enabled` |
| `OPENBILICLAW_TLS_PROXY_PORT` | `[tls_proxy].port` |
| `OPENBILICLAW_TLS_PROXY_CERT_DIR` | `[tls_proxy].cert_dir` |
| `OPENBILICLAW_TLS_SAN_NAMES` | `[tls_proxy].san_names`（逗号分隔） |

独立代理容器使用 `LISTEN_HOST`、`LISTEN_PORT`、`BACKEND_HOST`、`BACKEND_PORT`、`CERT_DIR`、
`CERT_FILE`、`KEY_FILE`、`CA_CERT_FILE`、`CRL_FILE`、`AUTO_GEN_CERTS` 与 `SAN_NAMES`。这些是
容器入口参数，不是通用 `Config` 环境变量；不要假设任意 `[tls_proxy]` 字段都能自动映射。

## 端口与客户端切换

`openbiliclaw serve-api --tls-port 9443` 可临时覆盖 TOML 端口，但只有
`[tls_proxy].enabled=true` 时才会启动。Docker 则设置 `OPENBILICLAW_TLS_PORT` 后重建/重启
profile。启用 TLS 不会自动改扩展或书签地址，也不会关闭原始 `:8420` HTTP 映射；需要
TLS-only 策略时应同时调整 Compose 端口发布或主机防火墙。把插件后端配置切到 `https`
后，手机版二维码和 loopback `/api/qr-info` 探测会沿用 HTTPS，不会向 TLS 端口发送明文 HTTP。
