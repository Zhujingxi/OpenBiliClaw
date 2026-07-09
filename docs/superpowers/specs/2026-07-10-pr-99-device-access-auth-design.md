# PR 99 Device Access Authentication Design

## Goal

接管 PR #99，把原来依赖可伪造 Extension Origin / manifest ID 的“密钥认证”改为真正的设备访问密钥，并让浏览器扩展在 Docker、NAS、局域网或 HTTPS 远程后端上安全换取短期会话、自动刷新和连接实时流。

该能力必须显式开启且默认关闭。现有本机扩展 loopback 免登录、Web Cookie 登录和允许列表内跨源 Bearer 登录保持兼容。

## Non-Goals

- 不把浏览器提供的 `chrome-extension://` / `moz-extension://` Origin 当作身份凭证。
- 不实现硬件密钥、WebAuthn 或每次请求双因子挑战。
- 不保存远程后端的主密码，也不把主密码用于自动登录。
- 不自动信任 Docker 默认网关 IP。
- 不让长期设备密钥出现在 URL、WebSocket URL、图片 URL、日志或 API 响应中。

## Configuration

`ApiAuthConfig` 新增三项：

```toml
[api.auth]
extension_access_enabled = false
extension_access_keys = []
extension_token_ttl_hours = 24
```

- `extension_access_enabled` 是远程扩展设备认证总开关，默认 `false`。
- `extension_access_keys` 保存 `key_id:sha256(secret)` 字符串，不保存明文设备密钥。
- `extension_token_ttl_hours` 只控制设备密钥换取的短期 session token，范围为 `1..168`，默认 `24`，不受 Web Cookie 的 `session_ttl_hours=0` 影响。
- 当开关关闭时，设备 token 交换端点返回 `403 extension_access_disabled`；本机扩展、Web 登录和其他现有门禁行为不变。
- `extension_access_keys` 视为敏感配置：不从 `GET /api/config` 返回，不进入普通诊断输出。

PR #99 尚未合入，因此删除其 `allowed_extension_ids`、`verify_extension_id`、RSA manifest key 和 Docker 默认网关信任实现，不提供兼容迁移。

## Device Key Format And Storage

CLI 使用 `secrets.token_urlsafe(32)` 生成至少 256-bit 随机 secret，并生成 12 个十六进制字符的 `key_id`。用户得到的完整设备密钥格式为：

```text
obc_ext_<key_id>.<secret>
```

后端只持久化：

```text
<key_id>:<sha256(secret)>
```

校验流程先严格解析前缀、ID 和 secret，再按 ID 找到 digest，最后使用 `hmac.compare_digest()` 比较。设备密钥只有在 CLI 生成时显示一次。

SHA-256 在这里用于高熵随机 secret，不用于用户密码。用户密码继续使用现有 scrypt 路径。

## CLI Contract

`openbiliclaw ext-key` 提供：

- `generate`：生成并持久化一个设备密钥，终端只显示一次完整 secret；不自动开启总开关。
- `enable`：仅当至少存在一个设备密钥时设置 `extension_access_enabled=true`。
- `disable`：关闭新的 token 交换，但保留已保存密钥，便于之后重新开启。
- `list`：只显示 key ID，不显示 digest 或 secret。
- `revoke <key_id>`：删除该 key，并提升全局 `auth_epoch`，使此前签发的全部 session token 立即失效；输出明确说明这是全局会话撤销。

所有写命令沿用现有 auth 配置的 env-managed / `config.local.toml` 遮蔽保护。修改配置后要求重启后端；`revoke` 的 epoch 提升即时生效。

## Backend Authentication Flow

新增公开但限流的端点：

```http
POST /api/auth/extension-token
Content-Type: application/json

{"key":"obc_ext_<key_id>.<secret>"}
```

成功响应：

```json
{
  "ok": true,
  "token": "<short-lived HMAC session token>",
  "expires_at": 1234567890
}
```

端点执行顺序：

1. `auth.enabled=false` 返回 `400 auth_disabled`。
2. `extension_access_enabled=false` 返回 `403 extension_access_disabled`。
3. 按解析后的真实客户端 IP 执行现有登录限流。
4. 解析和常量时间校验设备密钥；失败返回统一的 `401 invalid_device_key`，不区分 ID 是否存在。
5. 使用 `extension_token_ttl_hours` 和当前 `auth_epoch` 签发有限期 HMAC session token。

现有 `/api/auth/login` 恢复为 Web 登录语义，不新增 Extension Origin 分支。

HTTP 中间件的 token 提取规则改为：

- Cookie：保持现有同源 Web 逻辑。
- `Authorization: Bearer`：允许浏览器扩展 Origin，或现有 `allowed_bearer_origins` 中的 Web Origin。
- `?token=`：只允许 `/api/image-proxy`。
- 其他 API 即使带 Extension Origin，也拒绝 query token。

WebSocket 鉴权允许扩展 Origin 携带短期 session token 的 query 参数；验证 token 后仍不把 Origin 当作身份因子。普通 Web Cookie WebSocket 继续执行现有同源 / allow-list 检查。

## Local And Proxy Trust

`is_trusted_local()` 只接受现有 loopback 与明确配置的可信代理解析结果。删除读取 `/proc/net/route` 并自动加入默认网关的逻辑。

Docker、NAS 和反向代理部署若需要传递真实客户端 IP，必须显式设置 `trusted_proxies`。未设置或 Forwarded 头来自非可信 peer 时继续 fail closed，不授予本机免登录。

## Extension Endpoint Permissions

Chrome 和 Firefox manifest 增加 HTTP / HTTPS 的 `optional_host_permissions`，但不默认授予全网访问权。

用户保存后端 endpoint 时，扩展根据 scheme、host、port 计算精确 Origin，并在用户手势内调用 `chrome.permissions.request({origins: [origin + "/*"]})`。拒绝授权时保持旧 endpoint，并显示 `backend_permission_denied`。

endpoint 配置增加 `scheme = http | https`：

- loopback 和 RFC1918 私有 IPv4 可使用 HTTP。
- 公网 IP 或公共 hostname 必须使用 HTTPS；HTTP 保存被拒绝并提示 `https_required`。
- WebSocket scheme 从 endpoint 派生：HTTP -> WS，HTTPS -> WSS。

默认 loopback 权限继续保留，升级后本机用户无需额外授权。

## Extension Credential And Session Flow

扩展持久化两个值：

- `obc_extension_device_key`：长期设备访问密钥。
- `obc_auth_session`：`{token, expires_at}` 短期 session。

不再写入 `obc_auth_password`。升级时主动删除历史 `obc_auth_password` 和 PR #99 早期的裸 `obc_auth_token`。

设备配对流程：

1. 用户保存并授权远程 backend endpoint。
2. 用户在设置页粘贴设备密钥。
3. popup 调用 `/api/auth/extension-token`。
4. 成功后保存设备密钥和短期 session，清空输入框并重新连接运行时流。

普通 HTTP 请求统一通过 authenticated fetch helper：

1. 确保 session 已加载。
2. session 在 60 秒内过期时，使用设备密钥交换新 token。
3. 使用 `Authorization: Bearer <session>` 发请求。
4. 收到 401 时只刷新并重放一次，禁止递归重试。
5. 刷新失败时清除 session，保留设备密钥，UI 显示重新配对 / 检查后端状态。

只有以下场景把短期 session 放入 query：

- `/api/runtime-stream` WebSocket URL。
- `/api/image-proxy` 图片 URL。

设备密钥永远不进入 query。endpoint 变化时清除短期 session并为新 endpoint 重新交换，避免把旧后端 session 发送到新后端。

## Error Handling And Secret Hygiene

- 错误消息只包含稳定错误码，不回显设备密钥、session token 或 digest。
- 后端访问日志不得主动记录认证 JSON body 或 Authorization 内容。
- 扩展 console 不记录认证请求 body、完整 URL query 或 storage 中的凭证。
- 无效 key、未知 key ID 和错误 secret 统一返回 `invalid_device_key`。
- endpoint permission、HTTPS 限制、交换失败、session 过期和后端离线在 UI 中分别显示，不把所有错误折叠为“密码错误”。
- refresh 采用单飞 Promise，同一上下文中的并发 401 只触发一次 token 交换。

## Automated Testing

后端测试覆盖：

- 设备密钥生成、严格解析、hash 存储、常量时间校验。
- 配置默认关闭、load/save、TTL 边界、敏感字段脱敏和覆盖层保护。
- token 交换端点的 disabled、auth-disabled、invalid、valid、rate-limit 和有限 TTL。
- 伪造 Extension Origin 不能绕过设备密钥。
- 普通 API 拒绝 query token，接受扩展 Bearer Header；图片代理和 WebSocket 接受短期 query token。
- Docker 默认网关不再自动视为可信本机。
- CLI generate/enable/disable/list/revoke，以及 revoke 后旧 token 失效。

扩展测试覆盖：

- 设备密钥和结构化 session 存储，不保存主密码。
- 普通请求使用 Authorization Header，URL 无 token。
- session 预刷新、401 单次刷新重放、并发刷新单飞和刷新失败状态。
- WebSocket / 图片代理只使用短期 session query。
- endpoint 精确权限请求、拒绝回滚、HTTP 私网允许和公网 HTTPS 强制。
- popup 登录错误分类及现有 `popup-api` 测试不挂起。
- Chrome / Firefox typecheck、全量扩展测试和生产构建。

## Real Environment E2E

在本机执行真实链路：

1. 后端绑定 `0.0.0.0`，通过实际 LAN IP `192.168.31.98` 访问，并设置 `trust_loopback=false`、`auth.enabled=true`、`extension_access_enabled=true`。
2. 使用 CLI 生成真实设备密钥并启动真实 OpenBiliClaw API，不使用 mock server。
3. 构建 Chrome unpacked extension，使用 Playwright 启动真实 Chromium persistent context 并加载该扩展。
4. 在扩展设置页授权实际 LAN Origin、输入设备密钥并完成配对。
5. 验证真实 API 数据加载、HTTP Authorization Header、普通 API URL 无 token、图片代理成功、runtime WebSocket 建连。
6. 人为使 session 失效，验证扩展自动交换并只重放一次。
7. CLI 撤销设备 key，验证现有 session 被拒绝且扩展无法继续刷新；生成新 key 后重新配对恢复。
8. 捕获浏览器截图、网络请求摘要和后端日志摘要，确认没有明文设备密钥或 session 泄露。

当前机器没有 Docker daemon，因此不宣称真实 Docker 容器 E2E。Docker 风险通过删除自动网关信任、LAN 非 loopback 实链路和自动化回归测试覆盖。

## Documentation

实现同步更新：

- `docs/modules/api-auth.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/architecture.md`
- `docs/spec.md` 的系统架构图
- `README.md` / `README_EN.md` 顶部架构图
- `docs/changelog.md`
- `config.example.toml`
- 扩展远程 endpoint 权限与配对说明

文档明确区分长期设备密钥和短期 session token，不再使用“Extension ID 密钥”或“Origin 双因子”表述。
