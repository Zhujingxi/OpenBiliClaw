# 平台登录状态同步修复设计

## 目标

让桌面 Web（同时也是 PC 安装模式使用的设置页）与浏览器插件 side panel 对所有平台展示一致、可解释、不会因打开页面而触发平台请求的接入状态。

本次修复以“避免风控”为硬约束：`GET /api/sources/status`、设置页 30 秒轮询、浏览器插件登录态心跳均不得访问 Bilibili、小红书、抖音、YouTube、X、知乎或 Reddit 的远端接口。它们只能读取本地配置、数据库、凭据文件、已持久化健康状态，以及浏览器本地 Cookie 名是否存在。

## 已确认问题

1. 小红书凭据区展示 `xsec_token 已保存`，但没有说明它只是内容访问令牌，容易被理解成登录成功。
2. 小红书、知乎的登录布尔态主要依赖扩展启动、Cookie change 和周期 alarm；后端重启或热更新连接恢复时不会主动请求一次本地状态重发。
3. 知乎收到新鲜 `logged_in=false` 后，仍允许旧任务成功记录覆盖成 `ready`。
4. Reddit 默认 `rdt` 后端在每次 `/api/sources/status` 请求里运行 `rdt status --json`，单次可阻塞 10 秒；超时还被误判为 `login_required`。
5. 桌面 Web 没有映射 Reddit 合法返回的 `login_required` / `error`，会显示“状态未知”。插件与桌面 Web 的文案也不一致。
6. 抖音仅凭本地存在 Cookie 就显示“接入可用”，但该 Cookie 没有被登录接口实时验证。
7. 原始 Cookie 来源可能来自扩展、环境变量、手动粘贴或 CLI。仅凭某个浏览器配置文件退出登录就删除后端凭据，可能误删用户显式配置，因此不能盲目做自动清除。

## 方案比较

### 方案 A：配置页继续实时探测平台或命令后端

优点是状态看似最新。缺点是设置页轮询会制造重复请求、触发平台风控，且命令超时会拖慢整个状态接口。本方案不采用。

### 方案 B：后台定时探测，配置页读取短期缓存

比方案 A 更稳定，但仍会产生用户未主动发起的平台流量；还需要维护探测调度、退避和缓存一致性。本方案不采用。

### 方案 C：纯本地状态快照，真实任务结果只作健康证据（采用）

设置页只聚合本地信号。插件只检查本地 Cookie 名并 POST 到 localhost；真正的登录失败、限流或凭据过期由用户主动运行的 init/discover/smoke 任务更新健康记录。该方案不会因打开配置页增加平台请求，状态语义也能明确区分“凭据存在”和“最近任务验证成功”。

## 状态契约

统一保留以下状态，桌面 Web 与插件必须全部映射：

| state | 含义 | UI 文案 |
| --- | --- | --- |
| `ok` | 最近真实任务确认可用 | 接入可用 |
| `ready` | 登录 Cookie/凭据结构完整，但本次状态读取未访问平台 | 凭据已就绪 |
| `unverified` | 尚无登录信号，或只能确认存在未验证 Cookie | 状态待验证 |
| `missing` / `login_required` | 新鲜登录信号明确为 false，或本地缺必要凭据 | 需要登录 |
| `partial` | 凭据不完整或最近任务非登录原因失败 | 部分可用 |
| `stale` | 曾有登录信号但已超过可信窗口 | 需要刷新 |
| `missing_cookie` | X 等后端缺必要 Cookie | 缺少 Cookie |
| `expired_cookie` | 最近真实任务确认 Cookie 失效 | Cookie 失效 |
| `rate_limited` | 最近真实任务确认限流 | 频率受限 |
| `blocked` | 最近真实任务确认被拒绝 | 接入受阻 |
| `error` | 本地状态读取或命令健康记录异常 | 检查失败 |
| `no_auth` | 平台来源无需登录 | 无需登录 |

`logged_in` 只在 `ok`、`ready`、`no_auth` 时为 true。`unverified` 不能冒充已登录。

## 后端数据流

### 小红书与知乎

- `auth_state` 中“无记录”“新鲜 false”“新鲜 true”“过期 true”必须四态可区分。
- 新鲜 false 是权威登出信号，旧任务历史不得覆盖。
- 只有完全没有 Cookie 登录信号时，最近任务历史才可作为兜底。
- 过期 true 返回 `stale`，不伪装成 `missing`。
- 后端 background runtime-stream 连接建立时发送本地同步请求，扩展立即重读 Chrome 本地 Cookie 名并回传布尔值。该过程不加载平台页面、不请求平台网络。

### Reddit

- `/api/sources/status` 禁止调用 `probe_reddit_command_backend()` 或执行 `rdt status`。
- `rdt` 后端只读取本地 credential 文件，并沿用七天 TTL、必需 `reddit_session` 和文件格式校验。
- 本地 credential 完整时返回 `ready`，文案说明“凭据已就绪，未实时访问 Reddit 验证”。
- 缺失、过期、损坏分别映射为 `login_required`、`stale`、`error`，不得把命令超时解释为未登录。
- `rdt status` 仍可由显式 CLI smoke 或真实 discover 使用，不进入设置页轮询。

### 抖音

- 本地 Cookie 存在时返回 `unverified`，文案为“Cookie 已同步，需在实际任务中验证”；不再仅凭文件存在把 `logged_in` 设为 true。
- 没有 Cookie 时保持 `missing`。
- 本次不增加任何抖音登录探测请求。

### Bilibili、X、YouTube

- Bilibili 保留本地字段完整性判定；Cookie 同步时已有显式验证结果，设置页本身不新增验证。
- X 保留“本地必要 Cookie + 已持久化健康状态”的组合判定，不新增请求。
- YouTube 保持 `no_auth`。

## 扩展同步

runtime-stream 新增本地状态同步事件，至少覆盖 `xhs_login_state_sync_requested` 与 `zhihu_login_state_sync_requested`。`handleCookieSyncRuntimeEvent()` 收到后调用现有布尔上报函数。

扩展仍保留 startup、关键 Cookie `onChanged`、每平台独立 hourly alarm。所有这些动作只调用 `chrome.cookies.getAll()` 和 localhost API，不访问平台接口。

本次不根据浏览器 Cookie 缺失自动删除 Bilibili、抖音、X 或 Reddit 的后端凭据，因为这些凭据可能来自环境变量、CLI 或手动覆盖。后续若要做双向清除，必须先为凭据增加来源和所有权契约。

## 配置页

- 桌面 Web 和插件 side panel 使用同一份完整状态到文案映射。
- 小红书凭据摘要与详情明确写出“`xsec_token` 是内容令牌，不代表账号登录”。
- 两端保持 30 秒轮询，但轮询只命中本地 API，因此不会增加平台请求。
- 来源启用/停用状态继续与接入状态分开展示；停用来源也可以展示本地凭据状态，但降低视觉权重。

## 错误处理

- 本地 credential 文件不可读返回 `error`，不抛出导致整个 `/api/sources/status` 失败。
- 没有任何浏览器登录信号返回 `unverified`，不武断显示“需要登录”。
- 本地登录信号过期返回 `stale`，提示打开已安装扩展的浏览器刷新。
- 真实任务失败只有明确的 `*_login_required` 才能产生登录缺失状态；网络超时、命令超时、限流和普通抓取失败使用各自状态。

## 测试与验证

所有生产改动使用 TDD：先写测试并确认因当前错误行为失败，再做最小实现。

后端测试覆盖：

- `/api/sources/status` 的 Reddit 分支不会调用命令 runner；
- Reddit credential present/missing/expired/invalid 的本地状态；
- 知乎新鲜 false 压过旧成功任务；
- 小红书/知乎无信号与过期 true 的区别；
- 抖音 Cookie 存在时为 `unverified`；
- runtime-stream 请求小红书/知乎本地状态刷新。

扩展与前端测试覆盖：

- runtime-stream 两个新事件触发现有本地布尔同步；
- 桌面 Web 与插件完整映射 `login_required`、`error`、`stale`、`unverified`；
- 小红书 `xsec_token` 文案不再暗示登录成功。

最终执行 Ruff、MyPy、相关 Pytest、扩展 test/typecheck/build。真实 E2E 只检查 localhost API、设置页渲染和安装扩展的本地 Cookie 心跳；不运行 discover、search、feed、hot、related，不访问平台 API，也不执行任何账号状态变更动作。

## 文档范围

代码落地时同步更新：

- `docs/modules/extension.md`
- `docs/modules/config.md`
- `docs/changelog.md`
- 若状态数据流边界发生变化，再同步 `docs/architecture.md` 与 `docs/spec.md`；本设计不改变 discover/recommend 主架构。
