# Web/移动端密码登录 — Spec & Plan

> 状态：草案（待评审）· 作者：Claude Code · 日期：2026-05-30
> 关联：扩展 [`docs/mobile-web-spec.md`](../mobile-web-spec.md) 的「鉴权: 不做鉴权」基线

> **评审修订 v2（2026-05-30，据 Codex 对抗式 review）**：
> - 【信任边界】修复「同机反向代理使所有远程请求显示为 127.0.0.1 → 静默绕过密码门」的高危缺陷：loopback 仅在**无转发头**时可信（fail-closed），并新增显式 `trusted_proxies` 解析（§4.1）。
> - 【token 存储】放弃 localStorage 镜像（它让 HttpOnly 形同虚设）：**默认纯 Cookie**，同源即覆盖 fetch/img/WS；Bearer/`?token=` 降级为「跨源 + 强制限时」的显式逃生通道（§4.3、§7）。
> - 【配置一致性】修正 `_build_config` 片段：TTL 默认 `0`（对齐永不过期）、显式读取全部多词环境变量（§5.2）。
>
> **评审修订 v3（2026-05-30，据 Codex 第 2 轮 review）**：
> - 【信任边界·XFF 伪造】反代真实 IP 改为**从右向左穿越受信代理链**取第一个非受信 IP，并拒绝客户端伪造的 `X-Forwarded-For: 127.0.0.1`——修复「代理 append 模式下最左 IP 仍可被攻击者控制」的绕过（§4.1）。
> - 【凭证·登录不回传 token】登录响应改为**模式感知**：默认同源 Cookie 模式**body 不含 token**（只回状态），仅跨源限时 Bearer 模式且 `ttl>0` 才回 token——堵住「永久 token 在登录时暴露给页面 JS」（§4.3、§6）。
> - 【撤销语义】明确「清 Cookie 只是本地登出，复制/嗅探到的无状态 token 不会因此失效」；引入**无状态撤销纪元**实现真正的「登出所有设备」与改密即时失效（§4.4）。
>
> **评审修订 v4（2026-05-30，据 Codex 第 3 轮 review）**：
> - 【撤销机制重做】撤销纪元从「config.toml 里的 `revoked_before` 时间戳」改为「独立原子状态文件 `data/auth_state.json` 里的**单调计数 `auth_epoch`**」：① 不再污染 config、不与 `/api/config` 写入互相覆盖、多 worker 一致（验签时按 mtime 读最新）；② token 携带 `ep`，校验 `ep >= auth_epoch`——**消除秒级时间戳同秒未撤销与时钟回拨问题**（§4.4、§4.7）。
> - 【堵未授权全局撤销】`POST /api/auth/logout` 移出公开白名单：全局撤销 `?all=true` **必须已登录**，否则任意 LAN 页面都能 DoS 强制全员重登（§4.2、§6）。
> - 【XFF 解析规范化】给出单一 fail-closed 解析器契约：仅用 `X-Forwarded-For` 解析真实 IP（缺失/异常→401 而非 500），`ipaddress` 归一化，覆盖 IPv6/端口/RFC7239/重复头/空跳（§6）。
>
> **评审修订 v5（2026-05-30，据 Codex 第 4 轮 review）**：
> - 【撤销纪元改存 SQLite】`auth_epoch` 从 JSON 文件改为复用现有 SQLite 的单行 `auth_state` 表：跨进程**事务原子自增**、验签**每次实时读**（不靠 mtime 缓存）、明确定义缺失=0/损坏=fail-closed——根除多 worker 丢自增与粗粒度 mtime 问题（§4.7）。
> - 【CSRF 强制化】对 **Cookie 鉴权的非安全方法**（POST/PUT/PATCH/DELETE）强制 `Origin==Host` 校验 + SPA 自定义头 `X-OBC-Auth`；不再把 SameSite 当 origin 防护；Bearer/可信本机豁免，定义缺 Origin 规则（§4.8、§6）。
> - 【Bearer 模式服务端裁定】是否签发 body token 由**服务端**按 `Origin` 跨源 + 允许列表 + `ttl>0` 决定，**同源/缺 Origin 一律 cookie-only 不回 token**——把「不暴露 token」变成后端不变量而非前端自觉（§4.3）。
> - 【本地登出可用】明文登出端点 `POST /api/auth/logout`（不带 all）改为**公开幂等仅清 Cookie**（不动 `auth_epoch`），并令「无效 cookie 的 401」也带 `Set-Cookie` 清除——解决 HttpOnly cookie 在 JS 侧删不掉、token 失效后清不掉的死角；`?all=true` 仍需鉴权 + CSRF（§4.2、§4.4、§6）。
> - 【传输契约对齐】默认 API base 用**相对 `/api`**（保证同源）；精确定义 `Set-Cookie`（host-only、`Path=/`、`SameSite=Lax`、`HttpOnly`、`Secure` 仅当对外有效协议为 HTTPS，经受信代理 `X-Forwarded-Proto` 判定）（§4.3、§7）。
>
> **评审修订 v6（2026-05-30，据 Codex 第 5 轮 review）**：
> - 【秘密绝不经配置接口泄露】`session_secret`/`password_hash` **永不**出现在 `GET /api/config`（即便 `reveal_keys=true`）/任何响应里——否则 LAN 调用者读到签名密钥即可伪造 token，整套门禁失效。并要求 **P1+P2 原子合入**，避免「配置层先落地密钥、鉴权层尚未就绪」的真空窗口（§5.4、§6、§10）。
> - 【改密必撤销·全通道】把「改密 → 旧 token 失效」做成**与写入通道无关的不变量**：在 `auth_state` 存 `password_fingerprint`，**启动/重载时一旦发现 `password_hash` 变化即 `auth_epoch += 1`**，覆盖 CLI / init / 直接改 TOML / env / `PUT /api/config` 所有路径（§4.7、§4.4）。
> - 【统一 effective origin 规范】新增 §4.9 `effective_origin(request)`：仅经 `trusted_proxies` 采信 `X-Forwarded-Proto/Host`，归一化 scheme+host+有效端口+IPv6+大小写+默认端口省略；CSRF 的 `Origin==Host`、WS Origin、Bearer Origin 裁定、`Set-Cookie Secure` **全部复用同一实现**，杜绝各处各写导致的误判/不一致。
>
> **评审修订 v7（2026-05-30，据 Codex 第 6 轮 review）**：
> - 【指纹算法钉死，杜绝误撤销】修复 v6 致命矛盾：scrypt 含随机盐，同一明文每次启动 `password_hash` 都不同——若指纹取自 `password_hash`，则 env/Docker 每次重启都会误判「改密」→ 误撤销所有会话、违背「记住登录」。改为**指纹取自稳定凭据材料**：有明文时 `HMAC(session_secret,"pw:"+明文)`，仅有 hash 时 `HMAC(session_secret,"ph:"+hash)`；在 `session_secret` 解析后计算；**首次启用（无旧指纹）只写不 bump**；比较+bump+写指纹在单条 `BEGIN IMMEDIATE` 事务内完成（§4.7、§5.2、§12）。
>
> **实现验收修订 v8（2026-05-30，据 Codex 对实现代码的对抗式验收）**：
> - 【WebSocket 也必须挡】高危：`@app.middleware("http")` 不覆盖 WebSocket scope，初版只挡 HTTP、`/api/runtime-stream` 远程未鉴权可订阅。新增 `authorize_websocket()` 在 `accept()` 前校验（CSWSH：Origin==Host 或允许的 Bearer origin + token），并在流循环里**每次发送前 + 15s 看门狗**重读 `auth_epoch`，撤销后**关闭已建立的连接**（不只是挡新连接）。
> - 【撤销失败 fail-closed】高危：`reconcile_password_fingerprint` 失败时只记日志、带新密码 hash 但旧 epoch 继续跑 = 改密未撤销。新增 `AuthGate.reconcile_ok`，失败即对所有非本机 token 鉴权 fail-closed，直至下次成功 reconcile。
> - 【损坏 epoch fail-closed】`get_auth_epoch` 对「行存在但非整数」改为**抛错**（中间件 fail-closed），仅「行缺失」=0；`bump`/`reconcile` 同理不再把损坏值重置为 0。
> - 【TOML 明文密码指纹稳定】`get_auth_plain_password()` 除 env 外也读 `config.toml [api.auth].password`，使该路径指纹同样稳定、不误撤销。
> - 【桌面跨源 Bearer 落地】桌面端补齐文档承诺的跨源模式：检测绝对跨源 base → 登录存 `sessionStorage` token、fetch 带 `Authorization`、WS/image 用 `?token=`、401 清 token。
>
> **实现验收修订 v9（2026-05-30，据 Codex 第 2 轮验收 review）**：
> - 【CLI 改密即时撤销】`set-password` 设密码后**立即 `_bump_auth_epoch`**：运行中后端实时读 SQLite epoch，旧 cookie 立刻 401，不再等下次重启 reconcile。提示如实说明「现有登录态已失效；新密码须重启后端生效」（运行中进程仍持旧 hash，重启前勿依赖新密码——与 `--rotate-secret` 同一 restart 约束）。
> - 【变状态 GET 也要 CSRF】`/api/sources/{xhs,dy,yt}/next-task` 是会 claim+lock 任务的 GET，原 CSRF 只覆盖非安全方法 → 跨源 cookie GET 可被 CSRF 滥用。中间件对这些 GET 在 cookie 鉴权下同样强制 CSRF。合法调用者是 loopback 扩展（gate 豁免），不破坏。
>
> **实现验收修订 v10（2026-05-30，据 Codex 第 3 轮验收 review）**：
> - 【撤销失败要显式报错】`set-password` / `--rotate-secret` 之前忽略 `_bump_auth_epoch` 返回值，DB 不可写时仍谎称「已立即失效」。改为**校验返回值**：失败则打印明确错误并 `exit 1`，不再给出虚假的紧急轮换保证。
> - 【变状态 GET 审计补全】除 next-task 外，`GET /api/recommendations`（`serve()` 写历史行）与 `GET /api/chat/turns/{id}`（调度 pending turn 完成）也有副作用。改用**统一的自定义头 CSRF 防护**：前端对**所有 fetch**（含 GET）发 `X-OBC-Auth`（凭证化跨源请求在 `allow_origins=["*"]` 下无法设此头，预检即被拒，故自定义头本身即完整 CSRF 防护）；中间件对 cookie 鉴权的「非安全方法 + 变状态 GET 集合（next-task×3 / recommendations / chat-turns/{id}）」要求该头（非安全方法另叠加 `Origin==Host`）。`<img>`/WS 不经 fetch、不在该集合内，不受影响。
>
> **实现验收修订 v11（2026-05-30，据 Codex 第 4 轮验收 review）**：
> - 【logout-all 失败也要非零退出】`set-password --logout-all` 之前 bump 失败仅打印错误却 `return`（exit 0）。改为失败时 `raise typer.Exit(1)`，与 set-password/`--rotate-secret` 一致，避免脚本把失败的全局撤销当成功。
> - 【跨源桌面封面图】跨源 Bearer 模式下 `<img>` 的 `?token=` 因无 Origin 头被后端忽略 → 封面 401。桌面端在 `isCrossOriginBase()` 时给封面 `<img>`/预热 `Image()`/惊喜封面加 `crossorigin="anonymous"`，使浏览器发送 Origin（且不带 cookie），命中 `allowed_bearer_origins` + `?token=` 通过；同源默认模式不加该属性，cookie 照常携带。
>
> **实现验收修订 v12（2026-05-30，据 Codex 第 5 轮验收 review）**：
> - 【移动端登录后 DOM 复原】`ensureView` 缓存视图节点且仅首建时 append，而登录视图用 `$app.innerHTML=""` 清空会把缓存节点从 DOM 摘下（仍留在 `views` map）→ 登录/重登后 `startApp` 拿到游离节点、停在登录页或白屏。改为**登录成功后 `location.reload()`**：cookie 已种，重新 boot 时 `/api/auth/status` 返回 authenticated，从干净 DOM 全新启动，无残留登录视图、无游离缓存（与桌面遮罩同策略）。Codex 第 5 轮确认未再发现高/中危安全边界缺口，此为唯一功能阻断项。
>
> **实现验收修订 v13（2026-05-30，据 Codex 第 6 轮验收 review）**：
> - 【CLI 改密遇 env 覆盖须拒绝】`load_config` 中 `OPENBILICLAW_API_AUTH_*` 环境变量**优先于 config.toml**，故 Docker/env 部署里 `set-password`/`--disable`/`--rotate-secret` 改写文件重启后仍用旧 env 值——会把紧急改密谎称成功。改为：这三条写 config 的子命令在检测到 `OPENBILICLAW_API_AUTH_{PASSWORD,PASSWORD_HASH,ENABLED,SESSION_SECRET}` 时**拒绝并 exit 1**，提示改环境变量；`--logout-all`（仅写 SQLite、与配置来源无关）不受限、仍可立即撤销。
>
> **实现验收修订 v14（2026-05-30，据 Codex 第 7 轮验收 review）**：
> - 【堵 localhost-CSRF / 混淆代理高危】仅凭 `request.client.host` 是 loopback 就免登录，会被**浏览器里的恶意网页**利用：任意网站可让用户浏览器向 `http://127.0.0.1:8420` 发请求，对端即 loopback → 绕过密码门 + 通配 CORS 还能读响应。**收紧 loopback 免登录条件**：仅当请求**非跨源浏览器请求**时才放行——无 `Origin`（CLI/curl/非浏览器）、同源（本机 Web UI 自身）、浏览器扩展源（`chrome-extension://`/`moz-extension://`）、或显式 `allowed_bearer_origins`；带跨源 web `Origin`（如 `http://evil.example`，含不透明 `null`）即使来自 loopback 也**不**免登录、走正常 token 鉴权。HTTP 中间件、WebSocket 握手、`/api/auth/status` 经 `is_trusted_local` 统一生效。保留扩展 / 本机 UI / CLI 的「本机免登录」，关掉恶意网页这条路。
>
> **实现验收修订 v15（2026-05-30，据 Codex 第 8 轮验收 review）**：
> - 【堵 DNS rebinding 高危】v14 的同源/无 Origin 豁免用 `Host` 头推导 effective host，而 `Host` 由客户端控制：攻击页置于 `http://evil.example:8420`、把 `evil.example` rebind 到 127.0.0.1 后，浏览器同源请求落到本机后端（对端 loopback、`Host=evil.example`）仍被当「同源本机」放行。**新增 canonical-host 校验**：仅当**对端本身是 loopback（直连）**时，无 Origin / 同源豁免额外要求 effective `Host` 是规范 loopback 名（`localhost`/`127.0.0.1`/`::1`，`auth_core.is_loopback_host`）；经受信代理解析的客户端（对端是代理、非 loopback）不强制该项（代理配置即信任锚，rebinding 不适用）。回归测试：loopback + `Host: evil.example:8420`（带/不带同源 Origin）→ 401，status authenticated=false；本机 `127.0.0.1`/`localhost` Host → 放行。
>
> **实现验收修订 v16（2026-05-30，据 Codex 第 9 轮验收 review）**：
> - 【堵无 Origin 跨站子资源】「无 Origin = 本机放行」仍被 no-cors 子资源利用：`<img src="http://127.0.0.1:8420/api/sources/xhs/next-task">` 无 Origin、Host 规范 loopback、对端 loopback → 命中无 Origin 豁免 → claim 任务（15 分钟锁 / 队列 DoS）。**用 Fetch Metadata 收口**：`is_trusted_local` 在扩展源分支之后、canonical-host 之前，对 `Sec-Fetch-Site: cross-site|same-site`（浏览器揭示的跨源意图，即便省略 Origin）**拒绝本机豁免**；CLI/curl 不发 `Sec-Fetch-*`（不受影响），扩展走 `chrome-extension://` Origin 分支（不受影响），本机 UI 同源 `Sec-Fetch-Site: same-origin` 放行。回归测试：`Sec-Fetch-Site: cross-site` 的 next-task/favorites → 401；`same-origin` → 放行。

## 1. 背景

OpenBiliClaw 支持局域网访问：`openbiliclaw start --host 0.0.0.0`（或 init 时选「允许局域网设备访问」）后，
同一 Wi-Fi 下的手机可打开移动端 Web（`http://<局域网IP>:8420/m/`），其他电脑可打开桌面端 Web（`/web/`）。

但当前**所有 `/api/*` 接口对局域网完全开放、零鉴权**（见 `mobile-web-spec.md:17` 「鉴权: 不做鉴权」）。
唯一的访问控制是网络层绑定（`127.0.0.1` vs `0.0.0.0`）。一旦开了局域网，同网段任何设备都能：
读取你的完整画像/对话历史、触发推荐刷新、甚至 `POST /api/profile/edit` 改写人格画像。
在公司网络、合租 Wi-Fi、不受信 VPN 下这是真实的隐私泄露面。

本特性为局域网访问加一道**可开关的密码门**，并让登录态在页面刷新/重开后仍保持（「记住登录」）。

## 2. 现状摸底（关键约束）

| 关注点 | 现状 | 出处 |
|--------|------|------|
| 配置体系 | **Python `@dataclass` + TOML**（非 Pydantic）。`ApiConfig{host, port}` | `config.py:370-382` |
| 配置落盘 | `save_config()` → `_render_config_toml()` 手写每行 TOML | `config.py:1009-1026` |
| 环境变量覆盖 | `OPENBILICLAW_A_B_C` 朴素按 `_` 切分成嵌套 dict | `config.py:475-490` |
| App 工厂 | `create_app()` 先挂 GZip→CORS(`allow_origins=["*"]`)，再 `@app.middleware("http")` | `api/app.py:637-784` |
| 已有全局中间件 | `_degraded_mode_guard`：按 `request.url.path` 白名单放行，否则 503 | `api/app.py:769-784` |
| 静态站点 | `/m`→`web/`（移动 SPA）、`/web`→`web/desktop/`（桌面 SPA）、`/`→重定向 `/web`，均 `html=True` | `api/app.py:5590-5611` |
| 移动端 API 客户端 | `api.js` 同源 `location.host/api`，`requestJson()` 不带任何鉴权头 | `web/js/api.js:6,42-60` |
| 移动端持久化 | `state.js` 纯内存；localStorage 仅存 UI 偏好，无 token | `web/js/state.js:9-26` |
| 桌面端 API 客户端 | `getApiBase()` 默认 `127.0.0.1:8420`，可经 localStorage 覆盖 backendHost/port | `web/desktop/assets/js/app.js:242-250` |
| WebSocket | `/api/runtime-stream` 立即 `accept()`，无鉴权；浏览器 WS **无法自定义请求头** | `api/app.py:1363-1365` |
| 图片代理 | `<img src="/api/image-proxy?url=...">`，由浏览器发起，**无法带 Authorization 头** | `mobile-web-spec.md:181` |
| 扩展 popup | 始终访问本机 `127.0.0.1:8420`（同机、可信） | `docs/modules/extension.md` |
| 健康检查 | `/api/health` 是 CLI/扩展探活入口，必须保持公开 | `api/app.py:1051` |
| 加密依赖 | `pyproject.toml` 无 bcrypt/passlib/argon2/itsdangerous/jwt — **只能用标准库** | `pyproject.toml:35` |

**两条会咬人的约束**（设计必须正面处理）：

1. **浏览器 `<img>` 和 WebSocket 都无法携带 `Authorization` 头。** 任何「只认 Bearer 头」的方案都会让封面图全挂、实时流连不上。→ 必须支持 **Cookie**（同源自动携带，覆盖 img/ws）+ 可选 `?token=` 查询参数（跨源兜底）。
2. **环境变量覆盖按 `_` 朴素切分**：`OPENBILICLAW_API_AUTH_PASSWORD_HASH` 会被切成 `api.auth.password.hash`，对不上字段 `password_hash`。→ 多词 auth 字段（`session_secret`、`password_hash`…）不能走通用覆盖，需在 `_build_config` 里**显式**读取专用环境变量。

## 3. 目标 / 非目标

### 目标
- 移动端（`/m/`）与桌面端（`/web/`）支持**密码登录**门禁。
- 通过配置**一键开关**：`[api.auth].enabled = true/false`。
- 登录态**持久化**：刷新页面、关掉浏览器重开、重启后端，在 TTL 内都免重新登录（「记住登录」）。
- **不破坏本机扩展**：localhost 上的浏览器扩展、CLI 继续零摩擦工作。
- **零新增第三方依赖**：仅用 Python 标准库实现哈希与签名。

### 非目标（本期不做）
- 多用户 / 多账号 / 角色权限 —— OpenBiliClaw 是严格单用户产品，单一共享密码即可。
- 找回密码 / 邮箱验证 / 2FA —— 忘记密码走 CLI 重置或改 config。
- HTTPS / TLS 证书自动化 —— LAN HTTP 场景；HTTPS 由用户自行在反代层处理。
- 扩展 popup 的登录流（loopback 默认免登录，见 §4.1）。
- 审计日志的可视化（仅留最小化登录失败计数用于限流）。

## 4. 核心设计

### 4.1 信任模型：默认「本机可信，外部设备需登录」

密码门**仅对非「可信本机」请求生效**。「可信本机」= `request.client.host ∈ {127.0.0.1, ::1}` **且**请求**不带任何代理转发头**（`X-Forwarded-For` / `X-Real-IP` / `Forwarded`）。后半条是关键安全约束，理由见下方「反向代理陷阱」。

| 访问方 | 来源 | `enabled=true` 时 |
|--------|------|------------------|
| 浏览器扩展 popup | `127.0.0.1` | 免登录（同今天） |
| 本机打开桌面 Web `127.0.0.1:8420/web/` | loopback | 免登录 |
| CLI / 健康探活 | loopback | 免登录 |
| **手机** `192.168.x.x:8420/m/` | LAN | **需登录** |
| **另一台电脑** `192.168.x.x:8420/web/` | LAN | **需登录** |

理由：① 用户本就要求的是「手机端和 web 端（远程访问）要密码」；② 本机即已掌握全部数据，再加密码是徒增摩擦；③ **这样扩展完全不用改**。
该行为由 `trust_loopback`（默认 `true`）控制；设 `false` 则连本机也要登录（共享电脑场景），届时扩展需另行适配——本期标注为已知限制。

#### ⚠️ 反向代理陷阱（必须正面处理，否则密码门形同虚设）

§9 推荐用反向代理（nginx/Caddy）上 HTTPS。但若代理与后端**同机**，所有远程请求到达 ASGI 时 `request.client.host` 都是 `127.0.0.1`——若简单按 loopback 放行，则 `enabled=true` 也会把手机/别的电脑当本机**全部放行**，密码门彻底失效。这是 v1 草案的高危漏洞，v2 修复如下：

- **默认 fail-closed**：loopback 连接**一旦带任何转发头**（`X-Forwarded-For`/`X-Real-IP`/`Forwarded`），就**不**视为可信本机，按远程处理→要求登录。真正的本机客户端（扩展/CLI/本机浏览器直连）不会带这些头，故不受影响。
- **显式信任代理 + 从右向左解析**：新增 `trusted_proxies: list[str]`（默认 `[]`）。仅当**直接对端 IP** 命中该列表时才采信转发头，且**必须从右向左**穿越 `X-Forwarded-For` 链——逐个跳过命中 `trusted_proxies` 的跳数，取**第一个非受信 IP** 作为真实客户端。
  - ⚠️ **不可用最左值**：nginx 默认 `proxy_add_x_forwarded_for` 是 *append*，攻击者可发 `X-Forwarded-For: 127.0.0.1`，代理把真实 IP 追加在后变成 `127.0.0.1, <远程IP>`，最左仍是攻击者控制值 → 若按最左判定会被当本机放行（完整绕过）。故按右向左解析，且**任何客户端自带的 loopback/私网值都不得使请求降级为本机**。
  - 若解析出的真实客户端落在 `trusted_proxies` 内（链路异常/配置错误）→ 视为不可信，要求登录。
- **启动告警**：`enabled=true && trust_loopback=true && trusted_proxies==[]` 时，`start` 打印一行提醒：「如部署在同机反代后，请配置 `trusted_proxies`（并确保代理覆盖而非透传客户端转发头），或让代理自行鉴权，否则远程请求可能被误判为本机」。
- **不支持的组合**：同机反代 + 不配 `trusted_proxies` + 期望后端鉴权 = 明确不支持；文档要求此场景由代理层做鉴权。

### 4.2 门禁范围：只挡 `/api/*` 数据接口，静态壳放行

静态 SPA 外壳（`/m`、`/web`、`/`、`favicon`、manifest、图标、JS/CSS）**保持公开**——它们不含用户数据，且登录页本身就是这些静态资源渲染的。前端 SPA 拿到 `/api/*` 的 401 后自行渲染登录视图。这是标准 SPA 做法，避免在静态挂载上做重定向的复杂度。

**始终公开的白名单**（即使 `enabled=true`）：
- 任意 `OPTIONS`（CORS 预检）
- `GET /api/health`（CLI/扩展探活）
- `GET /api/auth/status`、`POST /api/auth/login`（登录入口自身）
- `POST /api/auth/logout`（**不带 `all`**）：公开、幂等、**只 `Set-Cookie` 清 `obc_session`、不改任何服务端状态**。公开是必要的——HttpOnly cookie JS 删不掉，token 一旦过期/被撤销，必须有个无需有效凭证就能清 cookie 的端点，否则陈旧 cookie 会一直挂在每个请求上（review r4#4）。
- 全部静态路径：`/`、`/m...`、`/web...`、`/favicon.ico`

⚠️ **`POST /api/auth/logout?all=true`（全局撤销）不在白名单**：它 `auth_epoch += 1` 影响所有设备，必须**已登录 + 过 CSRF 校验**（§4.8），否则任意未鉴权 LAN 页面都能强制全员重登（DoS，review r3#1）。
此外，**任何因 cookie 无效而返回的 401 也附带 `Set-Cookie` 清除 `obc_session`**，让失效凭证自动脱落。

其余 `/api/*`（含 `/api/runtime-stream` WS、`/api/image-proxy`）一律需要有效凭证（或来自可信本机）。

### 4.3 凭证：默认纯 Cookie（同源即覆盖 img/ws），跨源才用受限 Bearer

> v1 曾设计「Cookie + localStorage token 镜像」三通道。Codex review 指出：把同一个 token 也塞进 localStorage，等于让 `HttpOnly` 形同虚设——任何 XSS 都能读出**默认永不过期**的 token 长期盗用。故 v2 砍掉镜像，**默认根本不在 JS 可读处存 token**。

**默认（同源 `/m` 与 `/web`，占绝大多数场景）= 纯 Cookie：**

- 前端 API base **用相对路径 `/api`**（不拼绝对 `host:port`），从根本上保证同源——cookie/WS 行为才成立（见 §7、review r4#5）。
- 登录成功下发精确 `Set-Cookie`（见下方属性表），**响应 body 不含 token**（只回 `{ok:true}`）。
- 同源 `fetch` / `<img>`（封面图、`/api/image-proxy`）/ WebSocket 握手都会**自动携带** Cookie——「img/ws 带不了 Authorization 头」的问题在同源下根本不存在，无需 `?token=`。
- **前端永不持有/读取 token**：SPA 靠 `GET /api/auth/status`（凭 Cookie）得知是否已登录，据此决定是否显示登录页。token 始终 `HttpOnly`，XSS 偷不到。

**`Set-Cookie` 精确契约**（避免实现各异）：
`obc_session=<token>; HttpOnly; Path=/; SameSite=Lax; Max-Age=<ttl秒，或 ttl=0 时给超长如 10 年>`，**host-only（不设 `Domain`）**；**`Secure` 仅当对外有效协议为 HTTPS**——经受信代理时读 `X-Forwarded-Proto`（仅当直接对端 ∈ `trusted_proxies` 才采信，否则看 `request.url.scheme`）。LAN 纯 HTTP 下不能加 `Secure`（否则浏览器丢弃 cookie），此局限写进文档。

**Bearer 模式由服务端裁定（不是客户端说了算，review r4#3）：**

- 「是否在 body 返回 token」**完全由后端依据请求 `Origin` 决定**，前端不能自行索取：
  - `Origin` 缺失（同源 fetch 常无 Origin）或 `Origin` 的 host == 请求 Host（同源）→ **cookie-only，body 永不含 token**；
  - `Origin` 为**跨源且在 `allowed_bearer_origins` 列表**（新增配置，默认 `[]`）→ 才允许 Bearer；
  - 其它跨源 Origin → 拒绝（`403`）。
- Bearer 进一步**强制 `session_ttl_hours > 0`**，否则 `400`（永不过期 token 禁止进 body / 走 query）。
- 命中 Bearer 时：body 返回 `{ok:true, token, expires_at}`，前端存 `sessionStorage`（关页即清）；用 `Authorization: Bearer`（fetch）/ `?token=`（img、WS）。
- 这样「默认 cookie 模式绝不把 token 暴露给 JS」是**后端不变量**，不依赖前端自律。属窄场景高级用法。

**中间件取 token 顺序**：`Cookie obc_session` → （仅当请求来自允许的 Bearer origin）`Authorization: Bearer` → `?token=`。

### 4.4 无状态签名 token（不建会话表）

单用户 + 局域网，无需 SQLite 会话表（省去建表/清理/重启失效）。token 自包含、用服务端密钥 HMAC 签名：

```
token = b64url(payload) + "." + b64url( HMAC_SHA256(session_secret, b64url(payload)) )
payload = {"v":1, "iat":<签发unix秒>, "ep":<签发时 auth_epoch>}              # 永不过期（默认，ttl=0，不写 exp）
payload = {"v":1, "iat":..., "ep":..., "exp":<过期unix秒>}                  # 限时（ttl>0）
```

- 校验三步：① 重算 HMAC，`hmac.compare_digest` 常量时间比对；② payload 含 `exp` 时查 `exp > now`（无 `exp` 则不看过期）；③ **查 `ep >= 当前 auth_epoch`**（撤销纪元，见 §4.7）。三者皆过才放行。
- **「记住登录」= 默认永不过期**（D3）：`session_ttl_hours = 0`（默认）→ token 不带 `exp`、Cookie 用超长 `Max-Age`（如 10 年）持久化，关浏览器/重启后端都不失效。用户可把 `session_ttl_hours` 设为 >0 改成限时登录。
- 单用户无需 `sub`/用户名；密码只在登录那一刻校验，之后只认 token。

**撤销语义（无状态但可真正失效）**——这里要诚实，避免虚假安全感：

- **本地登出**（`POST /api/auth/logout`，**公开幂等**）：只 `Set-Cookie` 置空 + `Max-Age=0`，**仅删本机这一份 Cookie、不改服务端状态**。公开是为了让 token 已过期/被撤销时前端仍能清掉 HttpOnly cookie（JS 删不掉，见 §4.2、review r4#4）。⚠️ 它**不能**让已被复制/嗅探走的同一 token 失效——无状态 token 服务端没有逐会话记录可删。文档与 UI 须如实表述为「本机登出」。
- **登出所有设备 / 真正撤销**：靠**单调计数 `auth_epoch`**（见 §4.7，存 SQLite 单行，非 config）。token 携带签发时的 `ep`，仅当 `ep >= 当前 auth_epoch` 有效。`auth_epoch += 1` 即让此前签发的**全部 token（含被复制/嗅探的）立刻失效**。触发点：
  - `POST /api/auth/logout?all=true`（**须已登录 + 过 CSRF**，§4.8）/ CLI `set-password --logout-all`；
  - **改密码自动 +1**——且是**全通道不变量**：无论改密走 CLI、init、直接改 TOML、env、还是 `PUT /api/config`，启动/重载时按 `password_fingerprint` 变化检测并 bump（§4.7），旧设备一律失效；
  - `set-password --rotate-secret` 额外更换 `session_secret`（更强：连签名都换；**需重启才对运行中进程生效**，见 §4.5）。
- **为什么用单调计数而非时间戳**：秒级 `iat >= revoked_before` 有「同一秒签发的 token 不被撤销」的洞，且受时钟回拨影响；单调 `ep` 计数无此问题，且与挂钟无关。
- 不做 per-session 单独吊销（denylist/会话表）：单用户「登出=全部登出」符合直觉，保持近无状态、零新表。需要细粒度时再演进。
- ⚠️ **LAN HTTP 明文仍可被嗅探**（见 §9）：`auth_epoch` 解决「主动撤销」，但不替代信道加密；介意者上 HTTPS 或设有限 TTL。

### 4.5 密码与密钥存储（标准库）

- **密码**：`hashlib.scrypt(pwd, salt, n=2**14, r=8, p=1, dklen=32)`，落盘格式 `scrypt$16384$8$1$<b64salt>$<b64dk>`。校验时按存储参数重算 + 常量时间比对。**绝不存明文密码**。
- **session_secret**：`secrets.token_urlsafe(32)`，首次启用时若为空则**自动生成并写回 config.toml**（与 host 选择落盘同套路），保证重启后登录态不失效。支持环境变量覆盖（多进程/Docker 用同一密钥）。它是**启动期配置**（与 `host` 同级，启动时读入内存）：`--rotate-secret` 改它之后**需重启进程**才对运行中的 worker 生效；要「立刻」全局失效请用 `auth_epoch`（§4.7），它在验签时实时读取。
- 密码 hash 与 session_secret 存 `config.toml [api.auth]`（与现状一致：B 站 cookie、LLM key 也存 config，但密码是 hash 而非明文）。**频繁变动的撤销纪元 `auth_epoch` 不进 config**，见 §4.7。

### 4.6 登录失败限流（防暴力）

进程内内存计数器，key = 真实客户端 IP（按 §6 解析，非直接对端）：同一 IP 15 分钟内失败 ≥ 5 次 → 锁定 15 分钟，`POST /api/auth/login` 返回 `429`。无持久化（重启清零，可接受）。可信本机不计入。

### 4.7 撤销纪元存 SQLite 单行（跨进程事务原子，验签实时读）

撤销是运行时可变状态，**不能塞进 config.toml**（启动时一次性读入；`save_config` 重写整份 TOML 会与 `/api/config` PUT 互相覆盖；其他 worker 看不到）。**JSON 文件 + 进程内锁 + mtime 缓存也不够**（两个 worker 各读 N 各写 N+1 会丢一次自增；NFS/bind mount/粗粒度 mtime 下缓存可能看不到更新；缺失/损坏行为未定义 → review r4#1）。改为**复用现有 SQLite 数据库**（`data/openbiliclaw.db`，`ctx.database` 已就绪）里的一行：

- 表 `auth_state(key TEXT PRIMARY KEY, value TEXT)`，至少两行：`('auth_epoch', <int>)` 与 `('password_fingerprint', <hash 指纹>)`。这不是「会话表」（无逐会话记录），只是全局计数 + 指纹，仍近无状态。
- **改密必撤销（全通道不变量，review r5#2）**：「改密 → 旧 token 失效」不能只靠 CLI 主动 bump，否则用户改 TOML / 设 `OPENBILICLAW_API_AUTH_PASSWORD` / `PUT /api/config` 改密时旧 cookie 仍有效。做法：**启动与每次配置重载时**比对「密码指纹」并按需 `auth_epoch += 1`。CLI 的显式 bump 只是其特例。
- **密码指纹算法（钉死，review r6 致命修复）**：⚠️ **绝不能用 `password_hash` 本身当指纹**——scrypt 含随机盐，同一明文每次启动 hash 都不同，会让 env/Docker 每次重启误判改密、误撤销全部会话。改用**稳定凭据材料**：
  - 有明文（来自 `OPENBILICLAW_API_AUTH_PASSWORD` / init / CLI / `auth_raw.password`）→ `fingerprint = HMAC_SHA256(session_secret, "pw:" + 明文)`；
  - 仅有预生成 hash（hash-only 部署）→ `fingerprint = HMAC_SHA256(session_secret, "ph:" + password_hash)`（用户不改这串 hash 就稳定）。
  - 必须在 `session_secret` **解析/自动生成之后**计算（首次启用：先定 secret，再写指纹）。
  - **首次启用**（`auth_state` 无 `password_fingerprint` 行）→ **只插入、不 bump**（没有「旧密码」可言）。
  - 比较 → 不一致才 `auth_epoch += 1` → 写新指纹，三步在**单条 `BEGIN IMMEDIATE` 事务**内 CAS 完成（防并发重复 bump）。
  - 结论：同一密码无论重启多少次、走哪条通道，指纹稳定 → **不误撤销**（满足「记住登录」）；真正改密 → 指纹变 → 撤销。
  - 注：`--rotate-secret` 换了 `session_secret` 会令指纹基线变化并触发一次 bump，但 rotate 本就意在全局失效，符合预期（实现时 rotate 后用新 secret 重算并写回指纹，避免下次再 bump）。
  - 已知小限制：同一部署从「给明文」切到「只给等价 hash」（或反之）会因 `pw:`/`ph:` 前缀不同触发一次 bump——切换凭据供给方式属罕见运维操作，文档注明。
- **写（撤销）**：单条事务 `UPDATE ... SET value = value + 1`（或 `INSERT OR IGNORE` 初始化后自增）。SQLite 行级事务跨进程原子，**不会丢自增**，并发 logout-all 互不覆盖；与 config / `/api/config` 完全无关。
- **读（验签）**：每次验签 `SELECT value` 取当前纪元——单行主键查在本地 SQLite 上是微秒级，单用户 QPS 下成本可忽略，**不用 mtime 缓存**（如需，仅可加 ≤1s 有界 TTL 缓存，不靠 mtime）。任意 worker 自增后，其它 worker 下次验签立即可见。
- **缺失/损坏定义**：行不存在（首次）→ 视为 `auth_epoch=0` 并惰性插入；读取/解析异常或 DB 不可用 → **fail-closed**（拒绝带 token 的请求、要求重登），绝不回退成 0 而复活旧 token。
- 重启不丢：值持久在 DB；ACID 保证「要么旧值要么新值」。单 worker / 多 worker / Docker 多副本（共享同一 DB 文件）一致。

### 4.8 CSRF 防护（强制，不靠 SameSite 当 origin 防护）

`SameSite=Lax` 是**站点**级（site = eTLD+1，端口/协议不区分站点）而非**源**级。LAN 上 `http://192.168.1.5:8420` 与同主机另一端口/服务属同站点，Lax cookie 仍会带上；加之现状 CORS `allow_origins=["*"]`、`allow_methods=["*"]`，跨源页面虽读不到响应，但**能携带 cookie 发出**改状态请求（logout-all / profile 编辑 / config 写）。故 SameSite 不足以防 CSRF（review r4#2）。

**强制规则**——对**用 Cookie 鉴权**的**非安全方法**（`POST/PUT/PATCH/DELETE`）：

1. 校验 `Origin` 头：其 host[:port] 必须 == 请求 `Host`（同源）；不一致 → `403`。
2. 要求 SPA 自定义头 `X-OBC-Auth: 1`（跨站 `<form>`/简单请求加不了自定义头；浏览器对带自定义头的跨源请求会触发预检，预检在 CORS 层即被挡）。缺失 → `403`。
3. **缺 `Origin`** 的非安全 Cookie 请求：视为不可信 → `403`（正常同源浏览器在 unsafe 方法上会带 Origin）。
4. **豁免**：① 可信本机（§4.1）请求；② **Bearer 模式**请求（带 `Authorization`/`?token=`，不依赖 cookie，天然免疫 CSRF）；③ 安全方法（GET/HEAD）与白名单端点。
5. **WebSocket 握手**：校验 `Origin` == Host（跨源且非允许 Bearer origin → 拒绝握手）。

这样把「改状态」严格限定在同源 SPA + 显式自定义头，杜绝跨源 cookie 滥用。

### 4.9 统一 `effective_origin` / `effective_scheme` 规范（一处实现，四处复用）

`Origin==Host` 比较是 CSRF/CSWSH 的核心守卫，`Secure` cookie 又依赖「对外协议」，二者必须用**同一套受信判定与归一化**，否则反代/IPv6/默认端口下各处行为漂移（review r5#3）。定义单一 helper：

```
def effective_scheme_host(request) -> (scheme, host, port):
    # 仅当直接对端 ∈ trusted_proxies（同 §4.1）才采信反代改写的外部值
    if direct_peer in trusted_proxies:
        scheme = X-Forwarded-Proto（取第一跳）else request.url.scheme
        host   = X-Forwarded-Host  else Host 头
    else:
        scheme, host = request.url.scheme, Host 头
    归一化：host 小写、去 IPv6 方括号、解析 port（缺省按 scheme 补 80/443）

def effective_origin(request) -> str | None:   # "scheme://host[:非默认port]"
    解析 Origin 头同法归一化；缺失返回 None

# same_origin(request): Origin 解析结果 == effective_scheme_host 的 (scheme,host,port)
#   —— 默认端口省略、大小写、IPv6 统一后再比
```

- **CSRF（§4.8）**：unsafe + cookie 鉴权 → `same_origin(request)` 必真，否则 403（缺 Origin 视为不同源）。
- **WebSocket**：握手按 `same_origin` 校验（原生 WS 无法带 `X-OBC-Auth`，故 WS 仅靠同源 + cookie；这是标准 CSWSH 防护）。
- **Bearer 裁定（§4.3）**：用 `effective_origin` 与 `allowed_bearer_origins` 比对。
- **`Set-Cookie Secure`（§4.3）**：用 `effective_scheme_host().scheme == "https"` 决定是否加 `Secure`。
- ⚠️ 非受信对端伪造 `X-Forwarded-Proto/Host` 不被采信（同 §4.1 受信判定），故无法借此骗 `Secure` 或绕同源。

## 5. 配置变更

### 5.1 新增 dataclass（`config.py`，紧跟 `ApiConfig`）

```python
@dataclass
class ApiAuthConfig:
    """LAN 访问的密码门禁。仅当 enabled=true 且请求非 loopback 时生效。"""
    enabled: bool = False
    password_hash: str = ""          # scrypt$... ；空 + enabled 视为配置错误
    session_secret: str = ""         # HMAC 签名密钥；首次启用自动生成
    session_ttl_hours: int = 0       # 登录态有效期；0 = 永不过期（D3 默认），>0 = 限时小时数
    trust_loopback: bool = True      # 本机请求免登录（仅在无转发头时生效，见 §4.1）
    trusted_proxies: list[str] = field(default_factory=list)  # 同机/前置反代 IP；命中才采信 X-Forwarded-For
    allowed_bearer_origins: list[str] = field(default_factory=list)  # 允许跨源 Bearer 登录的 Origin 白名单（默认空=只许同源 cookie）
    # 注：撤销纪元 auth_epoch 不在此（高频可变 → SQLite auth_state 行，见 §4.7）

@dataclass
class ApiConfig:
    host: str = "0.0.0.0"
    port: int = 8420
    auth: ApiAuthConfig = field(default_factory=ApiAuthConfig)   # 新增
```

### 5.2 装配（`_build_config`，`config.py:496` 附近）

```python
def _env(name: str) -> str | None:           # 仅取非空白
    v = os.environ.get(name)
    return v if v and v.strip() else None

auth_raw = api_raw.get("auth", {}) if isinstance(api_raw.get("auth"), dict) else {}

# 密码：明文(优先) → hash。明文来自 init 或 env，启动时即 hash；也接受预生成 hash。
# ⚠️ scrypt 含随机盐：同一明文每次启动得到的 pwd_hash 不同——这没问题（登录时仍能校验），
# 但**密码指纹绝不能取自 pwd_hash**，否则 env/Docker 每次重启都误判改密。指纹取稳定材料，见 §4.7。
plain = _env("OPENBILICLAW_API_AUTH_PASSWORD") or auth_raw.get("password")
pwd_hash = (
    _hash_password(plain) if plain
    else _env("OPENBILICLAW_API_AUTH_PASSWORD_HASH") or auth_raw.get("password_hash", "")
)
# 密码指纹（用于「改密即撤销」检测，§4.7），在 session_secret 解析后计算：
#   pw_fingerprint = HMAC_SHA256(session_secret, ("pw:"+plain) if plain else ("ph:"+pwd_hash))

# ⚠️ 多词字段全部显式读取——通用覆盖按 "_" 朴素切分会把 SESSION_SECRET 切成
# api.auth.session.secret，对不上字段（见 §2 约束 2）。enabled/password 单词也一并显式，统一风格。
auth = ApiAuthConfig(
    enabled=_coerce_bool(_env("OPENBILICLAW_API_AUTH_ENABLED") or auth_raw.get("enabled", False)),
    password_hash=pwd_hash,
    session_secret=_env("OPENBILICLAW_API_AUTH_SESSION_SECRET") or auth_raw.get("session_secret", ""),
    session_ttl_hours=int(_env("OPENBILICLAW_API_AUTH_SESSION_TTL_HOURS")
        or auth_raw.get("session_ttl_hours", 0)),          # 默认 0 = 永不过期（对齐 D3）
    trust_loopback=_coerce_bool(_env("OPENBILICLAW_API_AUTH_TRUST_LOOPBACK")
        or auth_raw.get("trust_loopback", True)),
    trusted_proxies=list(auth_raw.get("trusted_proxies", [])),  # 列表仅走 TOML，env 不支持
    allowed_bearer_origins=list(auth_raw.get("allowed_bearer_origins", [])),  # 同上，仅 TOML
)   # auth_epoch 不在 config（见 §4.7：SQLite auth_state 行）
```
> 显式读取的环境变量清单：`OPENBILICLAW_API_AUTH_{ENABLED, PASSWORD, PASSWORD_HASH, SESSION_SECRET, SESSION_TTL_HOURS, TRUST_LOOPBACK}`。`trusted_proxies` 是列表，env 朴素切分无法表达，**仅支持 TOML**（文档需注明）。

### 5.3 落盘（`_render_config_toml`，`config.py:1024-1026` 之后追加）

```python
"[api.auth]",
f"enabled = {_toml_bool(config.api.auth.enabled)}",
f"password_hash = {_toml_string(config.api.auth.password_hash)}",
f"session_secret = {_toml_string(config.api.auth.session_secret)}",
f"session_ttl_hours = {config.api.auth.session_ttl_hours}",
f"trust_loopback = {_toml_bool(config.api.auth.trust_loopback)}",
f"trusted_proxies = {_toml_str_list(config.api.auth.trusted_proxies)}",  # 需补一个 list→TOML 渲染助手
f"allowed_bearer_origins = {_toml_str_list(config.api.auth.allowed_bearer_origins)}",
"",
```

### 5.4 校验（`_collect_config_issues`）
- `enabled=true` 且 `password_hash=""` → **blocking**（启用了门禁却没设密码，等同 LLM 缺 key 的处理）。
- `enabled=true` 且 `session_secret=""` → 不报错：启动时自动生成并 `save_config` 回写。

### 5.5b 秘密绝不经配置接口外泄（review r5#1）
- 现有 `GET /api/config`（含 `reveal_keys=true`）会回显配置。**`api.auth.session_secret` 与 `api.auth.password_hash` 必须从 `ConfigResponse` 中无条件剔除/掩码**（即便 `reveal_keys=true` 也不返回）——否则 LAN 调用者读到签名密钥即可伪造任意 token，门禁形同虚设。
- `PUT /api/config` **不接受**直接写 `session_secret`；若它修改了 `password_hash`（或其它密码材料），落盘后须触发 §4.7 的指纹比对 → `auth_epoch += 1`。
- 因为这些秘密只要进了配置就有被 `/api/config` 读到的风险，**P1（配置层）不得单独合入/启用**：见 §10 的原子合入约束。

### 5.5 `config.example.toml`（`[api]` 段后补充，附中文注释）

```toml
[api.auth]
# 是否为局域网/远程访问开启密码登录门禁。本机（127.0.0.1）默认始终免登录。
enabled = false
# 密码哈希（scrypt）。请勿手填明文；用 `openbiliclaw set-password` 或 init 设置。
password_hash = ""
# 登录态签名密钥，首次启用自动生成，请勿外泄。
session_secret = ""
# 登录态有效期（小时）。0 = 永不过期（默认，需手动登出或轮换密钥才失效）；填 >0 改为限时。
session_ttl_hours = 0
# 本机请求是否免登录（扩展/CLI 依赖此项）。设 false 则连本机也要登录。
# 注意：带代理转发头(X-Forwarded-For 等)的请求不算本机，仍需登录。
trust_loopback = true
# 受信任的前置/同机反向代理 IP 列表。仅当直接对端命中此列表，才采信 X-Forwarded-For
# 解析真实客户端 IP。留空(默认)= 永不采信转发头。同机反代必须配置此项，否则远程会被误判为本机。
trusted_proxies = []
# 允许「跨源 Bearer 登录」的 Origin 白名单（如桌面端从另一 origin 访问后端）。
# 默认空 = 只允许同源 Cookie 登录，绝不向 JS 返回 token。
allowed_bearer_origins = []
# 注：撤销纪元由运行时写在 SQLite auth_state 行（非本文件），用户无需配置。
```

## 6. 后端 API

新增模块 **`src/openbiliclaw/api/auth.py`**（hash/verify/sign/verify_token/限流），由 `app.py` 调用。

| 方法 & 路径 | 鉴权 | 请求 | 响应 | 说明 |
|------------|------|------|------|------|
| `GET /api/auth/status` | 公开 | — | `{enabled, authenticated, trust_loopback}` | SPA 启动先调；据此决定是否显示登录页 |
| `POST /api/auth/login` | 公开（限流） | `{password}`（**模式由服务端按 Origin 裁定，非客户端选**） | 同源/缺 Origin：200 `{ok:true}` + `Set-Cookie`，**body 不含 token**；允许列表内的跨源 Origin 且 `ttl>0`：200 `{ok:true, token, expires_at}`；跨源不在允许列表→`403`；跨源但 `ttl=0`→`400`；401 `{ok:false}`；429 锁定 | 校验密码→签发 token。是否回 body token 是后端不变量（见 §4.3） |
| `POST /api/auth/logout` | **公开·幂等** | — | `{ok:true}` + `Set-Cookie` 清 `obc_session` | **仅清本机 Cookie、不改服务端状态**（让失效 token 也能清 cookie，review r4#4） |
| `POST /api/auth/logout?all=true` | **需已登录 + CSRF** | — | `{ok:true}` | `auth_epoch += 1`（SQLite 事务）→ **所有设备**（含被复制的 token）立即失效。须有效会话 + 过 §4.8 CSRF，防未授权全局撤销 DoS（review r3#1） |

**鉴权中间件**（`app.py` 紧跟 `_degraded_mode_guard` 之后用 `@app.middleware("http")` 注册——后注册者在 Starlette 中位于更外层、最先执行）：

```
若 auth 未 enabled → 放行
若 path 命中白名单(§4.2) 或 method==OPTIONS → 放行
# 解析真实客户端 IP（防反代绕过，见 §4.1 + 下方「XFF 解析契约」）
client_ip, trustworthy_local = resolve_client_ip(request, trusted_proxies)  # 异常一律 fail-closed
若 trust_loopback 且 trustworthy_local 且 client_ip ∈ {127.0.0.1, ::1} → 放行
# 取 token：Cookie 优先；仅当 Origin ∈ allowed_bearer_origins 才认 Bearer/?token=
used_cookie, token = pick_token(request, allowed_bearer_origins)
若 token 缺失/验签失败/(有 exp 且 exp<=now)/ep < current_auth_epoch()
    → 401 JSON {error:"auth_required"}（若 cookie 无效则附 Set-Cookie 清 obc_session）
# CSRF：cookie 鉴权 + 非安全方法 → 必须过 §4.8
若 used_cookie 且 method ∈ {POST,PUT,PATCH,DELETE} 且 不满足(Origin==Host 且 X-OBC-Auth)
    → 403 JSON {error:"csrf"}
否则 → 放行
# current_auth_epoch()：SELECT 实时读 SQLite auth_state（§4.7），DB 不可用 → fail-closed 401
```

**XFF 解析契约（fail-closed，单一实现，对应 review #3）**：

```
def resolve_client_ip(request, trusted_proxies) -> (ip, trustworthy_local):
    peer = norm_ip(request.client.host)            # ipaddress 归一化；去 IPv6 []/端口
    fwd_present = 任一存在: X-Forwarded-For / X-Real-IP / Forwarded
    if not fwd_present:
        return peer, True                          # 直连，无代理头
    if peer not in trusted_proxies:
        return peer, False                          # 带转发头却非受信对端 → 不当本机（要登录）
    # 受信代理：仅用 X-Forwarded-For 解析真实客户端
    xff = 合并所有 X-Forwarded-For 头(可能重复) → 逗号拆分 → 逐个 norm_ip
    if 解析失败/为空/含非法项: return peer, False     # 不 raise，按远程处理（fail-closed）
    # 从右向左跳过命中 trusted_proxies 的跳，取第一个非受信 IP
    real = rightmost_not_in(xff, trusted_proxies)
    if real is None or real in trusted_proxies: return peer, False
    return real, True
```

- 规则：**只认 `X-Forwarded-For` 解析 IP**（不从 `X-Real-IP`/`Forwarded` 取值，但它们的存在仍触发 `fwd_present`，避免被绕过）；要求文档提示代理用 XFF。
- 任何缺失/畸形/IPv6/带端口/RFC7239 引号/空跳/重复头 → 走 `norm_ip` 归一化，解析不出就 `trustworthy_local=False`（**绝不 500，绝不 fail-open**）。
- 客户端自带的 loopback/私网值永远不能让请求降级为「本机」：右向左解析天然只在受信代理追加的真实位取值。

**CORS 注意**：鉴权中间件在 CORS 之外层，其直接返回的 401 不会经过 CORSMiddleware。故 401 响应里**手动补** `Access-Control-Allow-Origin`（镜像 `Origin` 或 `*`），否则跨源桌面端读不到 401（现有 `_degraded_mode_guard` 的 503 也有同样隐患，可一并修）。OPTIONS 始终放行以保预检。

**WebSocket**（`/api/runtime-stream`，`app.py:1363` `accept()` 之前）：先校验 `Origin`==Host（跨源且非允许 Bearer origin → 拒握手，§4.8）；同源由 Cookie 自动覆盖；允许的跨源 Bearer origin 读 `websocket.query_params["token"]`。token 校验同 §6（含 `ep>=auth_epoch`）。无效则 `close(code=4401)`，前端据此跳登录。

## 7. 前端分端实现

三端共享同一套「拦 401 → 显示登录 → 提交密码 → 重试」逻辑。**默认纯 Cookie：前端不持有 token**（凭据由浏览器在 Cookie 里管理），SPA 只关心「是否已登录」这个布尔。按各端代码风格落地。

### 7.1 移动端 `/m/`（`web/js/`）
- **`api.js`**（`:42-60`）：API base 用**相对 `/api`**（同源）；所有 `fetch` 带 `credentials:"same-origin"`；**非安全方法附 `X-OBC-Auth: 1` 头**（满足 §4.8 CSRF）；**不读/不写任何 token**；遇 `401` → 触发 `auth:required` 事件并 `throw`（同时浏览器会按服务端 `Set-Cookie` 清掉失效 cookie）。
- **`state.js`**（`:9-26`）：加 `authEnabled / authenticated / needsLogin` 字段（无 `authToken`）。
- **`app.js`**（`init`，`:184-208`）：先 `GET /api/auth/status`；若 `enabled && !authenticated` → 渲染**登录视图**，**先于** health/stream/tab。登录成功服务端已种 Cookie，前端重跑 init。登出调用公开的 `POST /api/auth/logout`（清 cookie）后回登录视图。监听 `auth:required` → 回登录视图。
- **`stream.js`**（`:6-8`）：相对 `ws(s)://<同源>/api/runtime-stream`，握手自动带 Cookie，**无需** `?token=`。
- 新增 `web/js/views/login.js` + `web/css/app.css` 登录样式（复用设计令牌）。

### 7.2 桌面端 `/web/`（`web/desktop/assets/js/app.js`）
- **默认改用相对 `/api`**（现状是绝对 `http://host:port/api`，在 HTTPS 反代/PWA standalone 下会变跨源或混合内容，导致 cookie/WS 失效，review r4#5）：默认同源、纯 Cookie + `X-OBC-Auth` 头，不存 token。
- 仅当用户在设置里显式指向**另一 origin 的后端**时，才用绝对 URL 并进入受限 Bearer 模式（后端须把该 Origin 列入 `allowed_bearer_origins` 且 `ttl>0`，否则登录 403/400）：token 存 `sessionStorage`，fetch 带 `Authorization: Bearer`，`<img>`/WS 用 `?token=`。
- 首屏若 `needsLogin` 显示登录遮罩，挡住 settings/主内容直到登录。

### 7.3 浏览器扩展 popup
- `trust_loopback=true`（默认）下访问 `127.0.0.1`（无转发头）→ **无需改动**。
- 仅当用户设 `trust_loopback=false` 才需登录流——本期不做，文档标注为已知限制。

### 7.4 「记住登录」如何成立
持久化**完全靠 HttpOnly Cookie 的超长 `Max-Age`**（关浏览器重开仍在），token 默认不带 `exp`（D3 永不过期），后端重启因 `session_secret` 稳定也不失效——一次登录长期免登，直到手动登出或轮换密钥。**不依赖任何 JS 可读存储**，故 XSS 偷不到凭证。这即用户要的「记录登录态」，且不牺牲 `HttpOnly` 的安全价值。

## 8. CLI / init / 部署

- **init**（`cli.py:3854-3855`，`_persist_api_host_choice` 之后）：仅当 `allow_lan=true` 时追加
  `_ask_password_setup()`（默认 N）。选是→输入两遍密码→`_persist_auth_config(password=..., enabled=True)`（复用 load→改→`save_config` 套路，自动生成 `session_secret`）。
- **新命令** `openbiliclaw set-password`：交互设/改密码并 `enabled=true`（**改密自动 `auth_epoch += 1`**，旧设备失效）；`--disable` 关闭门禁；`--logout-all` 仅 `auth_epoch += 1`（强制所有设备重登，不改密码/密钥）；`--rotate-secret` 额外更换签名密钥（最强撤销，**需重启生效**）。
- **`start`**（`cli.py:3160`）：启动若 `auth.enabled` 打印「🔒 局域网访问已启用密码登录」；若同时 `trust_loopback=true && trusted_proxies==[]` 再补一行反代告警（见 §4.1）。
- **Docker / 非交互**：`OPENBILICLAW_API_AUTH_ENABLED=true` + `OPENBILICLAW_API_AUTH_PASSWORD=...`（启动时 hash）+ 可选 `OPENBILICLAW_API_AUTH_SESSION_SECRET`（多副本共用）。`trusted_proxies`/`allowed_bearer_origins` 仅 TOML；`auth_epoch` 在 SQLite `auth_state` 行（多副本须共享同一 `data/openbiliclaw.db`）。
- `scripts/install.sh`、`docs/docker-deployment.md`、`docs/agent-install.md` 增加可选密码设置说明与安全提示。

## 9. 安全考量
- 密码 scrypt 加盐哈希；token HMAC-SHA256 签名；全部比对走 `hmac.compare_digest`。
- **凭证默认只在 `HttpOnly` Cookie 中**：前端 JS 读不到 token，XSS 无法窃取。「是否回 body token」是**后端按 Origin 裁定的不变量**（§4.3），同源/缺 Origin 一律不回 token，不靠前端自律。仅允许列表内跨源 Bearer 才有 JS 可读 token，且强制限时。
- **CSRF 强制**（§4.8）：Cookie 鉴权的非安全方法须 `Origin==Host` + 自定义头 `X-OBC-Auth`，**不把 `SameSite` 当 origin 防护**（它是站点级，挡不住同站点跨源/跨端口）。
- **`Set-Cookie` 精确化**：host-only、`Path=/`、`SameSite=Lax`、`HttpOnly`、`Secure` 仅在对外 HTTPS 时加（经受信代理 `X-Forwarded-Proto` 判定）；LAN 纯 HTTP 下不加 `Secure`（否则 cookie 被丢）。
- **反向代理不再能绕过密码门**：loopback 仅在无转发头时可信，同机反代须显式配 `trusted_proxies` 且右向左解析（见 §4.1/§6）。
- 登录限流防暴力。
- **登出语义诚实**：本机登出只删本地 Cookie，**不**让已复制/嗅探的无状态 token 失效；真正撤销靠 `auth_epoch` 单调计数（`logout-all`/改密/轮换密钥触发），令 `ep` 小于当前纪元的 token 全部失效。UI/文档不得把「本机登出」表述成「全局失效」。`logout?all=true` 须已登录（防未授权全局撤销 DoS）。
- **CSRF 强制（详见 §4.8/§4.9）**：Cookie 鉴权的非安全方法须 `same_origin(request)` + 自定义头 `X-OBC-Auth`，二者缺一 → 403；**不把 `SameSite` 当 origin 防护**。
- **秘密不外泄（§5.5b）**：`session_secret`/`password_hash` 永不经 `/api/config`（含 `reveal_keys`）或任何响应返回；否则可被读去伪造 token。
- **改密必撤销（§4.7）**：任何通道改密 → `auth_epoch` 自增 → 旧 token 立即失效。
- **永不过期的权衡（D3）**：token 不会自行到期，设备丢失/token 泄露的风险持续到一次全局撤销为止。缓解：① 改密自动 `auth_epoch += 1`；② `set-password --logout-all` 一键全局失效；③ `--rotate-secret` 连签名密钥一起换（最强，需重启）；④ 介意者把 `session_ttl_hours` 设为 >0 改限时。
- **诚实声明（写进文档）**：LAN 多为 HTTP 明文，密码与 token 在网络上**可被同网段嗅探**。本特性是「防顺手翻看」级别，不等于强加密信道；真要对抗强敌请上 HTTPS（反代）或勿暴露。这是对 `mobile-web-spec.md:18` 安全边界的强化，而非取代。
- `session_secret` 视为机密；写 config 时文件权限沿用现状（建议文档提示 `chmod 600 config.toml`）。

## 10. 实施 Phase 分解

> ⚠️ **P1 与 P2 必须原子合入同一 PR**（review r5#1）：P1 一旦把 `session_secret`/`password_hash` 写进配置，而 P2 的「`/api/config` 秘密剔除 + 鉴权门」尚未就位，就会出现 LAN 可读签名密钥的真空窗口。要么同 PR、要么 P1 必须**先包含 `ConfigResponse` 秘密剔除（§5.5b）**且 `enabled` 默认 false 不激活任何行为。

1. **P1 配置层**：`ApiAuthConfig` + 装配 + 落盘 + 校验 + **`/api/config` 秘密剔除（§5.5b）** + `config.example.toml` + 单测（round-trip / env / **断言 `/api/config` 不回 `session_secret`/`password_hash`**）。
2. **P2 后端核心**：`api/auth.py`（hash/verify/sign/verify-token[含 `ep>=auth_epoch`]/`resolve_client_ip`/`effective_origin`/CSRF 校验/限流）+ SQLite `auth_state`（`auth_epoch` 事务自增 + `password_fingerprint` 变更检测自动 bump，§4.7）+ 路由（Origin 裁定的登录、公开幂等 `logout`、需鉴权+CSRF 的 `logout?all`）+ HTTP 鉴权/CSRF 中间件 + WS Origin/token 校验 + 401 的 CORS 头与 `Set-Cookie` 清除 + 单测（见 §12）。
3. **P3 移动端**：`login.js` + `api.js`/`state.js`/`app.js`/`stream.js` 改造（`extension/tests` 同源 `node --test`）。
4. **P4 桌面端**：登录遮罩 + fetch 层 + 跨源兜底。
5. **P5 CLI/部署/文档**：init 提示 + `set-password` 命令 + install.sh/docker/agent-install + 安全提示。
6. **P6 文档与架构图**：见 §11。

## 11. 文档更新清单（按 CLAUDE.md 强制项）
- `docs/modules/config.md` —— `[api.auth]` 字段表。
- `docs/modules/cli.md` —— `set-password` 命令 + init 密码步骤。
- `docs/mobile-web-spec.md` —— 「鉴权」行从「不做鉴权」改为「可选密码门禁，详见本 spec」。
- `docs/changelog.md` —— 顶部新增条目。
- 新增 `docs/modules/api-auth.md`（或并入既有 api 模块文档）。
- **架构图**：登录门属「用户交互层」的新网关，更新 `docs/architecture.md` + `docs/spec.md §3 ASCII 图` + `README.md`/`README_EN.md` 顶部图。
- README CN/EN 📌 highlights（若随版本发布）。

## 12. 测试要点
- 后端鉴权：loopback **无转发头**放行；LAN 无 token→401；白名单（health/login/static）放行；密码错→401 且计数；锁定→429；无 `exp` 的 token 永久有效、`ttl>0` 时过期 token→401；WS 无 Cookie/token→4401。
- **反代防绕过（重点回归，对应 review #1 高危项）**：
  - loopback **带 `X-Forwarded-For`** 且对端不在 `trusted_proxies`→**拒绝**（按远程要登录）；
  - **伪造头**：受信代理转发 `X-Forwarded-For: 127.0.0.1, <远程LAN IP>`（append 模式）→ 按右向左解析得到远程 IP →**401**（绝不能因最左的 127.0.0.1 被放行）；
  - 对端在 `trusted_proxies` 且真实客户端确为本机 IP→放行；解析出的真实 IP 仍落在 `trusted_proxies`→拒绝；
  - `trusted_proxies=[]` 时永不采信 XFF。
- **XFF 解析器变体（对应 review #3，须在接线中间件前先过）**：IPv6 字面量 `::1` / 带方括号端口 `[::1]:5678`、`X-Forwarded-For` 重复多头、空跳 `a,,b`、`Forwarded`-only、`X-Real-IP`-only（后两者触发 `fwd_present` 但因不取值而判远程）、畸形/非法 IP → 一律**返回 401 而非 500**（fail-closed），`ipaddress` 归一化结果稳定。
- **撤销 + SQLite 纪元（对应 r2#3 / r3 / r4#1）**：`logout?all=true`（须已登录，**未授权→401**）/ 改密 / `--logout-all` 后 `auth_epoch += 1`（SQLite 事务），此前签发的 token（含手工复制的同一 token）一律 401；之后新登录正常。**同秒撤销**：同一秒登录+撤销，旧 token 仍因 `ep<auth_epoch` 失效。**并发自增不丢**：模拟两路并发 `logout-all`，最终 `auth_epoch` 递增正确（事务原子）。**缺失/损坏**：`auth_state` 行缺失→视 0 可启动；DB 读失败→带 token 请求 fail-closed 401。并发 `logout-all` 与 `/api/config` PUT 互不覆盖（分离存储）。
- **CSRF（对应 r4#2）**：Cookie 鉴权下，`POST` 缺 `X-OBC-Auth` 头→403；`Origin` host≠`Host`→403；缺 `Origin` 的 unsafe cookie 请求→403；安全方法(GET)与 Bearer 请求与可信本机→豁免；跨源 WS 握手→拒绝。
- **登录模式服务端裁定（对应 review r2#2 / r4#3）**：同源或缺 `Origin` 登录→响应 body **无 `token`**、仅 `Set-Cookie`；`Origin` 不在 `allowed_bearer_origins`→`403`；在列表内但 `ttl=0`→`400`；在列表内且 `ttl>0`→body 含 token。断言**客户端无法通过任何请求字段在同源下索取到 body token**。
- **本地登出可用（对应 r4#4）**：token 已过期/被撤销时 `POST /api/auth/logout`（公开）仍 200 且回 `Set-Cookie` 清除；任意 cookie 无效的 401 也带清除 `Set-Cookie`。
- **`Set-Cookie` 属性（对应 r4#5）**：纯 HTTP 不含 `Secure`；受信代理带 `X-Forwarded-Proto: https` → 含 `Secure`；**非受信对端伪造 `X-Forwarded-Proto: https` 不生效**；始终 `HttpOnly; Path=/; SameSite=Lax`、无 `Domain`。
- **秘密不外泄（对应 r5#1）**：`GET /api/config` 与 `GET /api/config?reveal_keys=true` 的响应中**断言不含** `session_secret`、`password_hash`（掩码或剔除）；`PUT /api/config` 不能写入 `session_secret`。
- **改密全通道撤销（对应 r5#2）**：分别经 ① 直接改 `config.toml` 重启、② `OPENBILICLAW_API_AUTH_PASSWORD` 变更重启、③ `PUT /api/config` 改密、④ CLI `set-password` 改密——每条路径后，旧 token 一律 401（指纹变化触发 `auth_epoch` 自增）。
- **指纹稳定·不误撤销（对应 r6 致命项，必测）**：① **同一 `OPENBILICLAW_API_AUTH_PASSWORD` 跨多次重启**（每次 scrypt 重新加盐、`password_hash` 不同）→ `auth_epoch` **不变**、旧 cookie 仍有效；② 与密码无关的 `PUT /api/config`（改 LLM 等）→ 不 bump；③ **首次启用**（无旧指纹）→ 写指纹但 **不 bump**；④ hash-only 部署同一 hash 重启 → 不 bump；⑤ 并发两路触发改密检测 → 仅自增一致、不重复（`BEGIN IMMEDIATE` CAS）。
- **`effective_origin` 归一化矩阵（对应 r5#3）**：默认端口省略（`:80`/`:443`）、大小写、IPv6 bracket、缺端口；受信代理 `X-Forwarded-Host`/`-Proto` 采信、非受信不采信；同一实现被 CSRF/WS/Bearer 裁定/`Secure` 复用 → 一致。
- 配置：TOML round-trip 保留 `[api.auth]`（含 `trusted_proxies`、`allowed_bearer_origins`，**不含** `auth_epoch`/`password_fingerprint`）；**缺省 `session_ttl_hours` → 0**；**hash-only（仅 `..._PASSWORD_HASH`）非交互部署可启动**；多词 env 显式覆盖生效；`enabled` 无密码→blocking。
- 前端：401 拦截→登录视图；登录成功后封面图（Cookie 通道）与 WS 正常；刷新/重开后免登录；登出后回登录页；**默认不持有 token**（断言无 localStorage/sessionStorage token 写入）；非安全请求带 `X-OBC-Auth`。

## 13. 决策记录（已与用户确认）
| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 信任范围 | ✅ **本机免登录，仅 LAN/远程设备需密码**（`trust_loopback=true`，扩展不用改） |
| D2 | 凭证形态 | ✅ **单一共享密码（无用户名）** |
| D3 | 登录态时长 | ✅ **默认永不过期**（`session_ttl_hours=0`）；可改 >0 限时 |
| D4 | token 机制 | ✅ **无状态 HMAC 签名（不建表）** |
| D5 | 启用入口 | ✅ init 在「开局域网」后追加密码询问（默认 N）+ 独立 `set-password` 命令 |
