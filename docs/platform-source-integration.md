# 新平台来源接入指南

> 这份指南沉淀自接入知乎来源的完整经历。目标是让后续新增任意平台时，都按同一套路径完成：事件抓取、初始化画像、discover、配置页、推荐卡、真实端到端测试、文档和发布。

## 核心原则

新增平台不是“加一个爬虫”，而是新增一个完整来源契约。只有下面链路都打通，才算功能完备：

- 后端事件 / 候选转换
- 浏览器插件或服务端取数路径
- CLI smoke 命令
- guided init 和画像初始化
- formal discover 调度
- 配置页和来源比例
- 桌面 Web / 移动 Web / 插件推荐卡
- LLM eval / 推荐链路中的候选兼容
- 单元测试和真实登录态 E2E
- 文档、版本和发布资产

优先复用现有平台模式，不要发明孤立路径。

| 目标 | 优先参考 |
| --- | --- |
| 登录态浏览器取数 | `extension/src/background/*-task-dispatcher.ts`、`extension/src/content/*/task-executor.ts`、`src/openbiliclaw/sources/*_tasks.py` |
| 服务端 / 直连 discover | `src/openbiliclaw/discovery/strategies/x.py`、`douyin_direct.py`、`bilibili_producer.py` |
| 初始化画像 | `src/openbiliclaw/cli.py` 中 B 站 / XHS / 抖音 / YouTube / X / 知乎路径 |
| 配置页 | `src/openbiliclaw/config.py`、`src/openbiliclaw/api/app.py`、`extension/popup/*`、`src/openbiliclaw/web/desktop/assets/js/app.js` |
| 纯文本推荐卡 | X / 知乎三端推荐卡处理 |

## 0. 定义来源契约

动代码前先写清楚：

- `slug`：平台全局 key，如 `zhihu`，必须在配置、事件、候选、UI 中一致。
- 内容单元：视频、笔记、推文、回答、文章、问题、帖子等。
- 事件类型：`view`、`like`、`favorite`、`follow`、`comment`、`share`、`dislike` 等。
- discover 模式：`search`、`hot`、`feed`、`creator`、`related` 或平台等价模式。
- 取数方式：官方 API、服务端 cookie replay、浏览器插件登录态、导入文件或混合方案。
- 是否只读：默认只读，不主动改变用户平台状态。
- 每个分支额度：按真实来源分支独立定义，不要因为多个分支最后都映射成 `favorite` 就共享上限。
- 状态变更边界：哪些 E2E 动作只读 / 安全，哪些会改变账号状态，必须提前写清楚。
- 三端内容卡形态：有封面、无封面文字、长文本、外链、评论 / 帖子 / 回答等特殊类型。
- engagement 计数逐项声明：`view / like / favorite / comment / share / danmaku` 六项，逐项写清「平台可提供并映射」还是「平台结构性缺失」。结构性缺失（如非 B 站的 `danmaku`、Reddit 的 `view`）合法留 0，前端不渲染、不做占位，不当 bug 修。可映射的计数必须在该平台**所有** fetch 子路径（feed / search / bootstrap / creator …）一致填充——同一内容在 A 路径有计数、B 路径全 0 是真实缺陷（参考 `docs/plans/2026-07-07-engagement-stats-completeness-spec.md` 的跨平台矩阵和三类成因）。
- 登录判定 cookie：声明哪个 cookie 名代表真实登录（例如 XHS `web_session`、知乎 `z_c0`、Reddit `reddit_session`），游客 cookie（`a1` / `_xsrf` 之类）不算。来源登录指示必须优先基于真实登录 cookie；只有没有新鲜 cookie 信号时，内容 / 任务历史等间接信号才可作兜底，不能覆盖或冒充 cookie 登录态。

如果平台依赖登录态，优先走浏览器插件任务链路。真实 E2E 要使用安装了插件且已有登录态的浏览器，不要用 MCP/CDP 临时浏览器代替，除非用户明确要求只做普通 UI 自动化。

### 0.1 接入契约必填项（有强制点，不是建议）

上面第 44 条讲了原则，但长期没有强制机制——七个平台各写各的，最后靠 `api/app.py` 里一个 400 多行的 if/elif 手工拍平，新平台全靠手抄上一个。所以新增平台时，下面这组字段**必须**在 `src/openbiliclaw/api/source_auth/providers.py` 里显式填写，否则过不了 `tests/test_source_auth_contract.py` 的参数化测试：

| 字段 | 必须回答的问题 | 允许留白吗 |
| --- | --- | --- |
| `auth_required` | 这个源到底需不需要凭据 | 否 |
| `credential` | 凭据在不在（`none` / `present` / `invalid`） | 否 |
| `credential_origin` | 凭据存在哪（config / env / data_file / extension / external_cli） | 否 |
| `verification` | 最近一次验证的结论 | 否 |
| `verify_method` | **这个结论有多硬** | 否 |
| `verify_ttl_seconds` | 结论多久过期，`None` = 不过期 | 可 `None`，但要有理由 |

`verify_method` 是其中最关键的一个，取值即证据强度：`live_probe`（真出网）> `passive_health`（由真实流量的错误反推）> `browser_heartbeat`（插件报登录 cookie 存在）> `local_file`（只读本地文件）> `task_history`（由历史任务反推）> `none`（无验证能力或不需要）。

**填 `none` 之前必须先做剥离对照实验**：实验组 = 完整凭据，对照组 = 剥掉登录 cookie 后的游客态，同一签名器 / UA / 时刻，唯一变量是登录 cookie。拿不出对照数据就不许在代码或 docstring 里声称"该平台无法验证"。

这条规则是有代价换来的：抖音的 `api/app.py` docstring 曾断言它"没有稳定的 nav 端点能区分未登录和软风控"，于是整个平台的登录态显示误报了很久，而且这句话还成了后续所有人不去修的理由。2026-07-18 的对照实验只花了几分钟就推翻了它——`/aweme/v1/web/user/profile/self/` 已登录返回 `status_code=0` + 非空 uid，游客返回 `status_code=8` "用户未登录"，干净得很。

对照实验本身也要防伪：同一次实验里 `/aweme/v1/web/query/user/` 在两组返回**完全相同**的 12 位 uid（那是设备级标识，由 `ttwid` / `odin_tt` 驱动）。只看"有没有返回 uid"会得出"可以验证"的错误结论。**判据必须是两组之间有差异，而不是单组看起来正常。**

完整设计与诊断见 `docs/plans/2026-07-18-source-auth-contract-spec.md`。

### 0.2 验证动作：`POST /api/sources/{slug}/verify`

新平台除了在 `providers.py` 填契约字段，还必须在 `src/openbiliclaw/api/source_auth/verify.py` 的 `VERIFY_ACTIONS` 里登记一个动作，否则第一次点「测试连接」就是 KeyError。`tests/test_source_auth_contract.py::test_verify_action_table_covers_every_platform` 断言两张表键集合相等。

| 动作 | 语义 | 当前平台 |
| --- | --- | --- |
| `live_probe` | 当场出网探测 | bilibili、douyin |
| `passive_health` | 汇报真实流量已经得出的结论，不出网 | twitter |
| `browser_heartbeat` | 经 WS 发 `*_login_state_sync_requested`，等插件回报（至多 5s） | xiaohongshu、zhihu |
| `local_file` | 重读本地凭据文件，不跑子进程 | reddit |
| `none` | 没有可验证的东西 | youtube |

三个容易写错的地方：

1. **动作是平台的固定属性，不等于契约里的 `verify_method`。** 后者描述「当前这个结论是怎么来的」，会随状态变化——知乎有插件心跳时是 `browser_heartbeat`，没有时回落 `task_history`。但点按钮要做的事永远是「请插件重新上报」。按 `verify_method` 分派会让知乎在最需要验证的时候反而没有可执行动作，还会凭空造出一个「重跑历史」这种不存在的操作。
2. **响应里 `outcome` 和 `auth.verification` 回答的是两个问题。** 前者是「这次点击验证到了什么」，后者是「我们现在相信什么」。插件没连时，一小时前的心跳仍然让 `verification=verified`，但这次点击什么都没验证到，所以 `outcome=indeterminate`。把两者合并会渲染出绿色「已验证」配上「插件未连接」的文案。
3. **三态，不是两态。** 探测超时、插件没回、平台限流、YouTube 无需登录——全部是 `indeterminate`，不是 `failed`。把「判定不了」显示成「凭据失效」，会让用户去删一份好好的 cookie。

去抖：每平台 10 秒，窗口内重复调用重放上次结果且不产生任何出网请求；并发点击另有 in-flight 标记拦截。用户能按住的按钮如果不挡，就是自己给自己造风控。

重放必须能被前端认出来，所以响应带两个字段：`replayed`（这次调用有没有真的干活）和 `retry_after_seconds`（还要等多久才会重新探测）。少了它们，一次被去抖的点击和一次真探测在响应里逐字节相同 —— 刚修好 Cookie 的用户再点一次，拿回缓存里的旧失败，会以为没修好。`retry_after_seconds` 由后端下发而不是前端各写一个 `10`，理由同 I4：两端各存一份常量，就是下一次漂移。

### 0.3 三端「测试连接」按钮

桌面 Web（`web/desktop/index.html` 的 `.source-status-row`）与插件 popup（`popup/popup.html` 的 `.settings-source-card`）每个平台各一个按钮，走同一套 DOM 约定：`renderProbePending()` → await → `renderVerifyResult()`，tone 写在 `dataset.tone`、文案写在 `textContent`，与「模型」tab 的 LLM 探测共用 `setProbeStatus()`。移动端见 Task 13。

三条硬规矩：

1. **tone 三态**：`verified` → `success`（绿）、`failed` → `error`（红）、`indeterminate` → `neutral`（蓝）。`neutral` 是本次新增的 CSS tone，**不能复用 `pending`** —— 那是「探测中」的灰，让终态和加载态长得一样。
2. **文案 100% 来自后端 `message`**，前端不得出现平台专属字符串（I4，指标脚本第 2 项会抓）。前端唯一自造的文案是「连不上我们自己的后端」那一条，且同样按 `indeterminate` 渲染 —— 后端连不上不能说明平台凭据坏了。
3. **状态标签与本次结论分开渲染**：`auth.verification` 驱动上方的「接入：…」badge，`outcome` 只驱动按钮旁那行字。小红书 / 知乎在插件没连时就是这个样子 —— badge 保持「状态待验证」，按钮旁是中性的「5 秒内没有收到回报」，绝不是绿色「已验证」配「插件未连接」。

按钮点完进入可见倒计时（`测试连接（10s）`）并 disable，长度取自 `retry_after_seconds`；真落进去抖窗口的点击（另一端点的、或刷新页面后的）文案会追加「沿用刚才的结果，本次未重新探测」。

### 0.4 凭据写入：`POST /api/sources/{slug}/credential`

新平台**不要**再新开一条写入路由。统一入口一条：

```
POST /api/sources/{slug}/credential
body  {kind: "cookie" | "token" | "login_state", value, source}
→ 200 {accepted, error_code, message, persisted, checked, unverified_reason, cookie_names, auth}
```

流程固定为：**结构校验 → 活体校验（有能力的平台）→ 落盘 → 广播 → 重算契约一并返回**。最后一步就是「保存后零回执」的解法——`auth` 是写完之后重新算出来的契约，所以保存页和下一次状态轮询不可能给出两种说法。

新平台要在 `src/openbiliclaw/api/source_auth/write.py` 的 `CREDENTIAL_SPECS` 里登记一条，字段含义：

| 字段 | 回答什么 |
| --- | --- |
| `kinds` | 这个平台接受哪几种写入；空元组 = 不接受任何凭据（youtube） |
| `required_keys` / `any_of_keys` | 结构校验用的 cookie 名，**必须与 `providers.py` 里数的那组一致** |
| `invalid_error_code` | 结构不合格时的机器可读码 |
| `live_gate` | 落盘前要不要真出网探一次 |
| `unverified_reason` | `live_gate=False` 时**必填**：说明为什么这个平台的写入无法确认 |

当前强度对照：

| 平台 | kind | 结构校验 | 活体校验 | 落到哪 |
| --- | --- | --- | --- | --- |
| bilibili | cookie | SESSDATA + bili_jct + DedeUserID | nav 探针 | `data/bilibili_cookie.json` + config.toml 镜像 |
| douyin | cookie | sessionid / sessionid_ss / sid_tt 至少一个 | profile/self 探针（D11） | `data/douyin_cookie.json` |
| twitter | cookie | auth_token + ct0 | 无（只能被动反推） | `data/x_cookie.json` |
| reddit | cookie | reddit_session | 无（只读本地文件） | rdt-cli 凭据库 |
| xiaohongshu | login_state / token | 类型即全部 | **架构上不可能** | DB 布尔位 / 内容令牌 |
| zhihu | login_state | 类型即全部 | **架构上不可能** | DB 布尔位 |
| youtube | — | 不接受写入 | — | — |

四条容易写错的地方：

1. **拒绝必须有证据。** 结构不合格是证据，平台明说「未登录」是证据。传输失败**不是**关于凭据的证据——但在能验证的平台上它仍然拒绝落盘，返回 `validation_network`（插件本来就对这个码做退避重试）。一份我们没能验证的凭据静默落盘，正是这个模块要防的事。
2. **`checked` 字段必须诚实。** `live_probe` / `structural` / `none` 三档，加上 `unverified_reason`。小红书和知乎后端只存一个布尔位，一个字节的 cookie 都没有，所以它们的写入永远是 `checked="none"` 并显式说明原因——不许因为「返回 200 好看」就假装校验过（I3/I5）。
3. **`kind="token"` 不是凭据。** 小红书的 `xsec_token` 是单篇笔记的内容访问令牌，跟账号登没登录毫无关系（spec D5）。单独设一个 kind 就是为了不让它继承登录凭据的那套承诺。
4. **`PUT /api/config` 也是凭据写入端点。** 它的路径叶子是 `config`，按词根扫描一个都看不见，但它一条路由写四个平台的凭据，而且设置页手工粘贴走的正是这条路。它现在委托同一个 `validate_credential()`，所以两条路径对同一份凭据给出相同的 `error_code`（`tests/test_source_auth_contract.py::test_write_paths_have_equal_validation` 锁死）。判断「这是不是凭据写入端点」的依据永远是**它会不会把用户凭据落盘**，不是路由名（I7）。

`PUT /api/config` 保留自己的落盘代码，因为它正处在 config.toml 的事务中间，不能让共享写入器把它没保存完的编辑刷出去。它也保留「留空不清除」和「掩码回显拦截」——那是这条路由的**局部更新协议**（「这个字段没被编辑」），在校验之前就解决掉，所以不构成两条路径的强度差异。

旧端点（`/api/bilibili/cookie`、`/api/sources/{dy,x,reddit}/cookie`、`/api/sources/xhs/tokens`、`/api/sources/{xhs,zhihu}/login-state`）全部保留为内部转发，响应逐字段不变（装着的插件在解析它们），并标 `deprecated=True`。这个标记不是文档修饰：`scripts/source_contract_metrics.py` 第 5 项只数**未标 deprecated** 的凭据写入形态，不标就永远显示「4 种命名」。

### 0.5 表单描述符：`GET /api/sources/credentials` 的 `form`

前端不该知道「小红书能不能粘贴 cookie」。新平台在 `CREDENTIAL_SPECS` 里补 `form_*` 字段，端点会把它投影成 `form` 描述符下发，三端照着渲染：

| 字段 | 回答什么 |
| --- | --- |
| `kind` | `cookie_textarea` / `token_input` / `extension_only` / `none`。**这是能力声明，不是布局偏好** |
| `label` / `placeholder` / `help_text` | 表单文案，全部来自后端 |
| `env_var` | 这个平台认哪个环境变量；`null` = 不认（B 站今天就是 `null`） |
| `required_keys` + `required_keys_mode` | 结构校验要哪些 cookie 名，`all` 还是 `any` |
| `actions` | 这个平台**真正做得到**的按钮 |

四条硬规矩：

1. **描述符是派生的，不是另写一份。** `required_keys` 直接来自 `CREDENTIAL_SPECS` 的 `required_keys` / `any_of_keys`，`build_credential_form()` 只做投影。表单说要三个 cookie、校验器只要一个，就是 D6 的漂移换个楼层重演一遍。
2. **`required_keys_mode` 不能省。** 抖音三个 session cookie 是**任选其一**，把它们平铺成 `required_keys` 会让 UI 声称校验器要求它其实不要的东西。spec 初稿的字段表里只有 `required_keys`，实现时发现表达不了，所以补了 mode——描述符宁可多一个字段，也不许说谎。
3. **`extension_only` 是绑定的。** 后端一个字节的小红书 / 知乎 cookie 都不存，所以这两个平台**不许渲染出可粘贴的输入框**。给一个能填的框，是在骗用户往虚空里打字。它们仍然有 `verify` 和 `open_login_window` 两个动作——「去浏览器登录」才是这两个平台唯一有效的修法。
4. **动作即能力，没有的不许挂。** 目前只有 `verify` / `copy` / `open_login_window` 三个，因为只有这三个背后有东西。spec 字段表里举例的 `clear` **故意没实现**：全 API 没有任何端点能抹掉已存的凭据（`PUT /api/config` 里空字段意为「本次没编辑」，恰恰相反），先挂按钮后补端点就是 UI 开始说谎的起点。

`summary`（凭据行那句话）同样由后端下发。小红书那句「已保存，但不代表账号登录」以前是桌面页里的一个平台特判，于是只有桌面页说得出这句话，插件和引导页都不知道。

### 0.6 三端共享渲染模块 `web/shared/source-status.js`

状态 → 文案/色调的表**只有一份**，在 `src/openbiliclaw/web/shared/source-status.js`。三个前端都加载它：

| 前端 | 加载方式 |
| --- | --- |
| 桌面 Web | `<script src="/shared/source-status.js" defer>`，在 `app.js` 之前 |
| 插件 side panel | `<script src="shared/source-status.js">`（classic，在 `popup.js` 模块之前）；文件由 `extension/scripts/build.mjs` 在每次 build 时复制进包 |
| setup 引导页 | `<script src="/shared/source-status.js">`，在内联脚本之前 |

几个要点：

- **`/shared` 是独立 mount。** `/web` 挂的是 `web/desktop/`，所以 `web/shared/` 下的文件从 `/web/shared/…` 取不到（只能从 `/m/shared/…`，那是移动端 mount）。跨端共享的资源不该走某一端专属的 URL。
- **插件必须用复制，不能用 HTTP 拉。** MV3 默认 CSP `script-src 'self'` 禁止从后端加载脚本，所以同一个源文件有两条投递路径：HTTP（桌面 / 引导页）和 build 期复制（插件）。复制产物 `extension/popup/shared/` 已 gitignore——**不要提交它**，一提交它就变成第四份手抄副本。
- **共享的是本枚举，不是所有相邻枚举。** 判据是「这张表的键是不是 `/api/sources/*` 发出来的字段值」。saved-sync 的任务状态表（`saved-sync-core.js` 等 6 处）跟本枚举共用 `login_required` / `rate_limited` 两个**拼写**，但回答的是「这一条收藏同步成功没有」，不是「这个源接不接得上」，所以不合并。引导页的 `INIT_REASON_TEXT` 同理（那是初始化前置条件枚举）。
- **依赖是硬的。** 模块缺失会让整个页面挂掉（与既有的 `saved-sync-core.js` 一致）。所以任何自建 HTTP stub 的 E2E 测试都必须加 `/shared/` 路由，否则 404 会以「某个不相关的测试超时」的形式暴露出来——`tests/test_desktop_web_autoload_margin_e2e.py` 等三个 stub 已加。

## 1. 调研和架构选择

1. 查是否有稳定官方 API 能拿到目标信号。需要联网时优先官方文档 / 一手资料。
2. 没有稳定 API 时，参考 XHS / 抖音 / YouTube / 知乎的浏览器插件任务模式：
   - 后端入队任务；
   - 插件打开或复用真实平台 tab；
   - content script 读取 DOM 或同源 JSON endpoint；
   - 插件把规范化结果 POST 回后端；
   - 后端再转换为统一事件或 discover 候选。
3. 如果选择第三方 CLI / SDK 作为默认后端，默认安装必须真正带上它：
   - 把依赖加到 `pyproject.toml` 默认 `dependencies`，更新 lockfile，并用项目虚拟环境实际安装验证；
   - 一键 AI 安装、本地脚本安装、Docker 构建若走 `pip install .` / `uv sync` 会自动吃默认依赖；如果某个安装入口绕过 `pyproject.toml`，要同步补清单；
   - 桌面 / PyInstaller 安装包还要显式收集 lazy / subprocess 依赖；如果冻结包没有 console script，要提供 in-process fallback 或把可执行文件打进包里；
   - 如果第三方 CLI / SDK 需要浏览器 Cookie，优先复用已连接 OpenBiliClaw 插件的 `chrome.cookies` 同步能力，把必要 Cookie 写入该工具的本地 credential store；手动 `login` 命令只能作为 fallback，不能成为有插件登录态时的唯一入口；
   - 用真实 `--help` / 源码确认命令、参数、结构化输出格式，不要凭 README 或记忆猜子命令；
   - smoke / producer 要输出 JSON/YAML 等机器可解析格式，并补单测锁定真实参数；
   - 状态探测不能隐式触发登录、浏览器 Cookie 提取或其他长耗时副作用；缺本地凭据时应返回 `login_required` 并提示显式登录命令；
   - 命令脚本通常装在虚拟环境 `bin/` / `Scripts/`，用户可能直接运行 `.venv/bin/openbiliclaw` 而没有激活 venv，`shutil.which()` 之外还要查当前 Python 环境的脚本目录。
4. 先做最小 smoke：
   - `fetch-<slug>` 或 `discover-<slug> <keyword>`；
   - 默认不写 memory、不触发画像；
   - 终端打印分支计数和失败原因；
   - 后端持久化任务结果，方便状态页和 debug。

## 2. 后端事件和任务链路

常见文件：

- `src/openbiliclaw/sources/<slug>_tasks.py`
- `src/openbiliclaw/runtime/<slug>_producer.py`
- `src/openbiliclaw/sources/event_format.py`
- `src/openbiliclaw/sources/bootstrap_state.py`
- `src/openbiliclaw/api/app.py`
- `src/openbiliclaw/api/models.py`
- `src/openbiliclaw/api/runtime_context.py`
- `src/openbiliclaw/cli.py`

必须满足：

- 平台原始 row 转成统一事件时带 `source_platform=<slug>`。
- metadata 保留平台稳定 ID、URL、作者、来源分支、原始互动动作等可解释字段。
- `signal_strength` 语义和其他平台一致；平台自带强度优先，缺失时用统一兜底。
- smoke 任务默认不写 memory、不触发画像。
- init / profile 任务必须显式带当前 init ownership 或 `profile_update=true` 等语义，避免普通 smoke 污染画像。
- `/api/sources/status` 基于最近任务结果给出 `ready`、`missing`、`partial`、`unverified`、`login_required` 等真实状态，不要硬编码 `no_auth`。
- 登录态平台要接真实登录指示链路：插件 `extension/src/background/cookie-sync.ts` 监控该平台登录 cookie（只上报 `logged_in` 布尔，绝不传 cookie 值）→ `POST /api/sources/<slug>/login-state` → 后端存 auth_state kv + 时间戳。`/api/sources/status` 优先按登录 cookie 状态判定，任务历史只作无 cookie 信号时的兜底。
- 插件任务平台通常需要 `/api/sources/<slug>/next-task`、`/task-result`、`/kick`，且必须严格用这个路径形状：init 写保护中间件按 URL 段精确放行 `/api/sources/<slug>/{kick,task-result}` 的 POST（`api/app.py` 的 `_init_write_allowed`），自造别的端点形状会在 init 期间被 409 拦掉（或反过来意外绕过 init 保护）。
- 插件 background 对后端的调用一律走带鉴权的共享 API client（device-key / session，见 PR #99），dispatcher 不要自己裸 `fetch`。
- 如果平台要支持「自定义来源 recipe」（`SourceRecipe`）取数，还需提供 `src/openbiliclaw/sources/<slug>_adapter.py` 实现 `SourceAdapter` 协议并注册进 `AdapterRegistry`。`sources/registry.py` 的 `AdapterRegistry.resolve(recipe)` 按 `recipe.source_type` 查找；当前 `DiscoveryEngine` 只提供 `register_adapter()` / `adapter_registry`，尚未在 discover 运行时调用 `resolve()`，所以只注册不能宣称 recipe 取数已接通，还要补运行时解析与调用。只走平台原生任务 / producer 链路则不需要 adapter。

## 3. 浏览器插件接入

登录态平台通常需要这些文件：

- `extension/src/shared/platforms/<slug>.ts`
- `extension/src/content/<slug>.ts`
- `extension/src/content/<slug>/task-executor.ts`
- `extension/src/content/<slug>/task-mode.ts`（需要任务 tab 标记时）
- `extension/src/background/<slug>-task-dispatcher.ts`
- `extension/src/background/service-worker.ts`
- `extension/manifest.json`
- `extension/manifest.firefox.json`
- `extension/scripts/build.mjs`
- `extension/tests/<slug>-*.test.ts`

插件要求：

- host permission 只加必要域名。
- 普通行为采集和显式任务执行隔离。
- 任务 tab 用 hash/query 标记，content script 在任务模式下只跑 executor，不上报普通浏览事件。
- 任务必须有超时和结构化错误，不要长期 pending。
- content executor 只做同源 DOM/JSON 归一化，最终事件权重、画像写入由后端决定。
- 现代站点常把按钮放在 Web Components / open shadow root 里；点击 / 分享 / 收藏等 E2E selector 要能处理 open shadow DOM、slot、icon-only button 和动态 aria/title/data-testid。
- 默认 E2E 只跑不改变账号状态的动作。`like`、`favorite`、`follow`、`save`、`upvote`、`subscribe` 等会改真实账号状态的动作必须有显式 `allow_state_changing` / 测试号 / 用户授权。
- 行为事件采集要验证 DB 里的统一事件：`source_platform`、稳定内容 ID、URL、作者 / subreddit / topic、target metadata 和 dedupe key 都要能追溯。
- 测试覆盖 URL 分类、任务校验、timeout、登录失败、分支 cap、normalizer、dispatcher 回传。

## 4. 配置和设置页

一个来源不支持 UI 配置，就还没有产品化。

需要更新：

- `src/openbiliclaw/config.py`
- `config.example.toml`
- `src/openbiliclaw/api/app.py` 的 `/api/config` GET/PUT
- `extension/popup/popup.html`
- `extension/popup/popup.js`
- `extension/popup/popup-helpers.js`
- `src/openbiliclaw/web/desktop/index.html`
- `src/openbiliclaw/web/desktop/assets/js/app.js`
- `/setup/` 和移动端 view-model 中的初始化来源列表
- `docs/modules/config.md`

配置项建议：

- `[sources.<slug>].enabled`
- `[sources.<slug>].source_modes`
- 每个 discover mode 独立 daily budget / cooldown
- `[scheduler.pool_source_shares].<slug>` 默认值
- 旧 `config.toml` 缺 `<slug>` 时自动补默认值
- 关闭平台时保留配置值，但 runtime quota 不应被它占用

特别注意：来源比例保存到配置页以后，必须真的进入 runtime source policy 和 candidate pool 配额，不只是 UI 上能看到。

配置页验收不要只看一个端：

- 插件 side panel 和 PC Web 都要能保存平台开关、source modes、每个分支预算、候选池 share。
- `/api/config` GET/PUT 要 round-trip 新字段，旧 `config.toml` 缺字段时按默认值回填。
- `/api/sources/status` 要支持该来源真实状态枚举。插件任务源常见 `unverified`：尚无任务证明不是失败，测试不能只允许 `ready/missing`。
- 开关关闭时保留用户填写的 share / budget，但 runtime 有效配比必须剔除该平台。

## 5. Guided Init 和画像初始化

所有初始化入口都要补：

- CLI：`--yes-<slug>` / `--no-<slug>`，必要时加分支上限参数。
- Desktop `/setup/` 来源选择。
- 插件 guided-init checklist。
- API init models、init status 和进度展示。

规则：

- 新可选平台默认 opt-in 提示，不阻塞 B 站或其他已选平台初始化。
- 如果平台能在已登录浏览器内稳定读取个人行为信号，应优先实现 `bootstrap_events` / `bootstrap_profile`：明确每个 scope、默认上限（当前强信号平台通常每 scope 300）、事件映射（例如 saved → `favorite`、upvoted/liked → `like`、subscribed/following → `follow`），并允许该平台作为唯一初始化来源，只要真实拉到至少一条信号。
- 如果平台只启用后续 discovery、不在 init 阶段产生个人行为信号，必须在 CLI / API / 插件 / Web UI 中标成 discovery-only，且不能作为唯一画像初始化来源；只选择这类来源时应给出明确错误（例如 `no_profile_signal_sources`），不要等到最后落成 `empty_signals`。
- 平台登录缺失只影响该平台，不应让其他来源无法初始化。
- init 任务结果必须绑定当前 init run，避免扩展延迟结果误写 memory。
- smoke 后若需要写 memory，必须用显式 flag，例如 `--write-memory`。
- 画像重建必须显式，例如 `--rebuild-profile`，且应隐含写 memory。
- 真实画像 E2E 必须使用本地实际 LLM / embedding 配置；不要擅自换成本地默认模型或 mock provider。若用户指定了本地配置中的某个 provider，要按配置里的 provider/model/base_url 跑。
- 测试要证明：普通 smoke 不写 memory/profile；init/profile 任务会写。

## 6. Discover 接入

同时要有 smoke 命令和正式 discover。

后端：

- `src/openbiliclaw/runtime/<slug>_producer.py`
- refresh/runtime controller 调度入口
- 转成 `DiscoveredContent(source_platform=<slug>, source_strategy=<slug>-<mode>)`
- candidate pool 和 source policy 识别该来源
- `/api/sources/status` 能反映 discover 任务结果

CLI：

- `discover-<slug>`：search smoke
- 可选 `discover-<slug>-hot`
- 可选 `discover-<slug>-feed`
- 可选 `discover-<slug>-creator`
- 可选 `discover-<slug>-related`
- `openbiliclaw discover --source <slug>` 必须走正式 producer，不能只提示去跑 smoke 命令。

质量要求：

- 没有显式关键词时用画像关键词 fallback。
- 只要该来源有 `search` 类 discover，就必须同时接入统一关键词链路的两半：
  - **生成侧（双轨，两条都要覆盖新平台）**：
    - merged prompt 轨：`runtime/keyword_planner.py` 的 `_PLANNER_PLATFORMS` 平台元组、`_PLATFORM_QUERY_STYLES` 平台 query 风格字典，以及 `llm/prompts.py` 的静态 `PLATFORM_SUPPLY_ADVANTAGES`（`<supply_advantage>` 表）/ 允许 key / schema 示例，都要加 `<slug>`；补测试证明 `<slug>` 缺口会触发一次 merged LLM 生成。
    - keyword inspiration axis 轨：`runtime/inspiration_pipeline.py` + `build_inspiration_axis_keyword_prompt`（axis+keyword 单次 LLM 调用，cross-domain explore 也从 axis 库取词）。它按 allocation targets 的 `platforms` 分配产词——确认新平台会出现在 allocation targets 里，否则 axis 轨永远不为该平台产词。
  - **抓取侧**：producer 使用 `KeywordFetchCoordinator.claim(<slug>)` 领取关键词，把 `source_keyword_id` 透传到候选；关键词池为空时回退画像关键词，抓取失败时标 `failed`，成功交付候选后标 `used`。
  - 只做 claim/fetch、不进 planner generation，会导致正式 discover 长期只能吃画像 fallback 或旧词库，不算接入完成；只接 merged 轨漏 axis 轨（或反之）同样是半截接入。
  - 补齐某个平台时顺手审计所有已接入 search 型来源；文档写着“使用统一关键词”的来源必须都有 generation 测试覆盖，不能只在 producer 里 claim。
- 候选入池阈值必须走统一 admission policy（`src/openbiliclaw/discovery/admission.py` 的 `effective_admission_threshold`）：策略 / producer 可以提供更严格的 requested threshold，但它只能抬高、不能压低或绕过 policy floor；exact `explore` 是唯一放宽语境。2026-07-10 的统一修复把候选自带的 `score_threshold` 作为 requested input 再与 policy floor 取 `max`，新来源不要恢复“直接采用候选阈值”的旧路径。
- creator / related 需要 seed；冷启动时可用同轮 search / hot / feed 结果兜底。
- 停止时给明确 reason：`pool_full`、`source_disabled`、`mode_disabled`、`budget_exhausted`、`login_required` 等。
- 候选入池必须尊重 `[scheduler.pool_source_shares]`。
- 正式 producer 与 smoke 命令的终端文案要描述真实后端；默认走插件时不要残留“命令后端”之类旧提示。
- discovery 入池后至少抽查 DB：`source_platform`、`source_strategy`、`source_keyword_id`、内容 URL、body_text / content_type 等字段能被 evaluator 和推荐卡消费。

## 6.5 Eval / 推荐链路接入

新来源不只是能抓到候选，还要能走完推荐闭环。

检查项：

- `DiscoveredContent` 字段足够 evaluator 判断：标题、作者、正文 / 摘要、标签、URL、内容类型。
- LLM prompt builder / merged prompt schema 不应因为新平台 key 或 text-only 内容破坏静态 system prompt 约定。
- 候选进入 `discovery_candidates(pending_eval)` 后，真实本地 LLM eval 配置能跑通；不要用 mock 或错误 provider 代替用户配置。
- admission 后推荐 API 返回的 item 保留 `source_platform` / `content_url` / `body_text` / `content_type`。
- 推荐卡的「去看看 / 收藏 / 稍后再看 / 不感兴趣 / 聊一聊」仍能对非 B 站来源发正确 payload。

## 7. 推荐卡三端适配

三端都要补齐：

- 桌面 Web：`src/openbiliclaw/web/desktop/assets/js/app.js` 和 CSS。
- 移动 Web：`src/openbiliclaw/web/js/view-models.js` 和 CSS。
- 插件 side panel：`extension/popup/popup-helpers.js`、`popup.html`、`popup.js`。

检查项：

- 来源 badge 和文案正确。
- 打开链接正确。
- 无封面来源有 text-card fallback。
- 非 B 站内容不会误构造 B 站 URL。
- 稍后再看、收藏、忽略、不感兴趣、聊一聊等动作仍可用。
- 长标题、长摘要、无封面卡片不会遮挡按钮。
- 桌面、移动、插件侧栏都做截图或视觉检查。
- 推荐页平台过滤 / source badge / source label 要包含新平台。
- engagement 契约包含 `view / like / favorite / comment / share / danmaku` 六项，但当前展示链路尚未补齐六项：`DiscoveredContent` 有六个字段，`RecommendationOut` 与移动 / 桌面两个 `recommendationStats()` 目前只有 `view / like / favorite / comment / danmaku`，没有 `share_count` / `🔁 share`（缺口见 `docs/plans/2026-07-07-engagement-stats-completeness-spec.md`）；插件侧栏也要单独核对。契约里声明为「结构性缺失」的字段不渲染、不占位；声明可映射的字段要用真实候选验证实际已透传到当前 DTO 与卡片，未落地的 `share` 不得宣称端到端完成。
- 如果源主要是文字内容，要确认 text-card 在 PC、移动、插件三端都不是断图 fallback，按钮不会被正文遮挡。
- 封面链路要显式决定：走后端 `/api/image-proxy` 缓存代理，还是浏览器直连。走代理必须把封面 CDN 域名加进 `runtime/image_cache.py` 的 `ALLOWED_IMAGE_HOST_SUFFIXES`（否则一律 403 Domain not in whitelist）；CN CDN 域名还要同时加 `_DIRECT_FETCH_HOST_SUFFIXES` 绕过系统代理（风控会封代理出口 IP，抖音 / B 站 / XHS 都踩过）。浏览器直连则要先确认该 CDN 无防盗链 / referer 限制。
- 移动 Web 的「去看看」会尝试拉起平台原生 App：`src/openbiliclaw/web/js/app-launch.js` 的 `buildAppDeepLink(url)` 按内容 URL 的 host / path 分支解析并返回 URL scheme。新平台有可靠官方 scheme 就加对应解析分支；没有就返回空串，由 `openContentUrl()` 走浏览器 fallback，不要硬造 scheme。

临时 E2E 截图不要直接提交到根目录。只有迁移到 `docs/images/` 且被 README / 首页 / 文档引用时才提交。

## 8. 测试清单

后端常见测试：

- `tests/test_<slug>_tasks.py`
- `tests/test_<slug>_producer.py`
- `tests/test_api_<slug>_ingest.py`
- `tests/test_config.py`
- `tests/test_source_policy.py`
- `tests/test_cli.py`
- `tests/test_api_app.py`
- `tests/test_keyword_planner.py` / `tests/test_llm_prompts.py`（search 型来源必须证明统一 query generation 已接入）
- 推荐卡样式 / view-model 测试

插件常见测试：

- `extension/tests/<slug>-adapter.test.ts`
- `extension/tests/<slug>-task-dispatcher.test.ts`
- `extension/tests/<slug>-task-executor.test.ts`
- popup/settings/init 相关测试

原生保存 executor 还必须覆盖 strict task/page/item/type 关联、full ancestor visibility、closest identity fence、hidden/related dialog、同名 ambiguity、checked idempotency，以及 directional action-local risk。需要命名容器的平台必须在创建后 close/reopen/re-query；创建失败或重查不一致不得 fallback 到其它容器。fixture 接线完成不等于真实账号验证，文档和 PR 必须分别报告两种状态。

完成前至少跑：

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

对大改动，最终还要跑一次全量 `.venv/bin/pytest -q` 和 `npm test`。如果全量检查发现旧测试断言漏了新合法状态（如插件源 `unverified`），修测试；如果 `ruff format --check src tests` 命中历史无关文件，不要顺手格式化，先用 `origin/main:<path>` 验证是否已在 main 修复，并在交付说明里说明。

发布前插件包验证：

```bash
cd extension
npm run package:only -- --archive-version <extension-version>
npm run build:firefox
npm run package:firefox:only -- --archive-version <extension-version>
```

全仓 `ruff format --check src tests` 如果命中历史无关文件，不要顺手大规模格式化；只格式化本次改动文件。

## 9. 真实端到端验证

登录态相关来源必须用真实扩展浏览器验证。

验证阶梯：

1. 启动后端，确认使用的 data/config root 和扩展连接的是同一套环境。
2. 重新加载本地构建的插件；若项目已有热更新，可按现有机制使用。
3. 打开平台页面，确认当前浏览器已登录。
4. 跑 `fetch-<slug>` 或 discover smoke，看分支计数、cap、错误原因。
5. 每个 discover mode 跑一次，确认候选入 `discovery_candidates`，或因合理 reason 停止。
6. 跑 `openbiliclaw discover --source <slug>`，确认正式 producer 通。
7. 在插件配置页和桌面 Web 配置页保存 source modes / source share，回读 `/api/config`。
8. 桌面 Web、移动 Web、插件 side panel 都看推荐卡样式。
9. 如用户要求，跑 `--write-memory` / `--rebuild-profile`，确认 memory/profile 真的变化。

真实 E2E 的终端输出、任务 result、数据库计数比截图更有价值；截图只作为临时视觉证据。

真实 E2E 要分层报告：

- 安全动作：snapshot / scroll / click / share / search / hot / related 等默认可以跑。
- 状态变更动作：like / favorite / follow / save / upvote / subscribe 只在用户明确允许或测试号中跑。
- native-save 精确授权记录：仅有 `allow_state_changing=true` 不够；每次真实 favorite / watch-later 必须同时命名 exact platform、action、public `content_id` 与 `expected_target`，并按平台矩阵校验。trusted-local `/api/extension/e2e/run` dedicated 模式必须与 generic actions 互斥，只提交一个 canonical item 到 production `/api/saved/{action}/sync`，再按同一 durable task/item/resolved target 关联；通用 DOM E2E runner 禁止 native-save mutation。授权和结果都拒绝账号 ID、Cookie、token、HTML、响应正文和含秘密 URL；安全 callback 仅记录 `platform/action/content_id/expected_target/task_status/error_code`。自动同步默认关闭，手动两种 action 分开授权；duplicate 必须得到 `already_synced`，本地 cleanup 只删 membership 且确认平台记录保留。
- 配置动作：插件页和 PC Web 保存后必须回读 `/api/config`，再确认 runtime source policy / pool share 生效。
- 推荐动作：三端截图或像素/DOM 检查要覆盖长标题、无封面、文字卡和按钮区域。
- 画像 / eval：使用真实本地配置的 LLM provider，记录 provider、命令、候选 / 事件计数和最终 profile / candidate 状态。
- 混合后端动作：如果默认后端会 fallback 到插件，报告时要把“默认后端成功”和“fallback 成功”拆开说；例如 CLI / SDK credential 未就绪但插件 fallback 完成 discovery，不能表述成默认后端已通。
- Cookie / credential 同步动作：如果实现了插件同步第三方 CLI credential，要同时验证后端 endpoint、插件 runtime-stream / hot reload、浏览器 cookie 可读性和最终 credential 文件；若真实浏览器缺必要 cookie 名，要记录“不阻塞 fallback，但默认命令后端仍 login_required”。

## 10. 文档和发布

接口、数据流、配置、CLI、新来源行为变化都要更新文档。

按范围更新：

- `docs/changelog.md`
- `docs/modules/cli.md`
- `docs/modules/config.md`
- `docs/modules/discovery.md`
- `docs/modules/extension.md`
- `docs/modules/soul.md` 或 memory/runtime 文档
- `docs/architecture.md`
- `docs/spec.md`
- `README.md`
- `README_EN.md`
- `docs/index.html`
- `docs/index.md`（新增文档时）

发布检查：

- 后端版本：`pyproject.toml`、`src/openbiliclaw/__init__.py`、`uv.lock`
- 插件版本：`extension/package.json`、`extension/package-lock.json`、`extension/manifest.json`
- 首页版本 / SEO：`docs/index.html` 的 `softwareVersion`、meta description、首页 source card、英文翻译都要包含新平台。
- README / README_EN 顶部定位、核心特性、安装登录说明、架构图中的来源列表都要同步。
- 如果新增 / 修改了本指南或 skill，确认不是未跟踪文件，并与 `origin/main` 已存在版本做 diff，避免合并时丢掉后补规则。skill 有两份入口（`.codex/skills/add-platform-source/SKILL.md` 和 `.claude/skills/add-platform-source/SKILL.md`），内容必须保持一致；实现细节只写在本指南，skill 只保留入口指向和精简的关键约束。
- 推 tag 前先查远端是否已存在同名 tag；如果同名 tag 已经存在，不要改旧 release 对应的 changelog 语义，必须 bump 新版本并把新改动放进新的 changelog block。
- 常规 tag：
  - `backend-vX.Y.Z`
  - `extension-vA.B.C`
  - `desktop-vX.Y.Z`
- Docker 渠道：`.github/workflows/release-docker.yml` 分别发布 backend 镜像和独立的 `openbiliclaw-ollama` baked-embedding 镜像，也都在版本对齐范围内。新来源若给 `pyproject.toml` 加了默认依赖（第三方 CLI / SDK），要确认 backend Docker 镜像构建真的带上它。GHCR 新建的 package 默认 private，需要手动设 public，否则用户无法匿名拉取。
- 本地提交前跑 `git status --short --ignored` 看清楚：未跟踪设计稿、截图、`dist/`、zip/xpi/dmg/exe、临时 release 包不要误提交；只有文档引用的图片或明确要求入库的资产才纳入提交。
- 本地可先用 `uv build` 验证后端 sdist / wheel；当前项目 venv 可能没有 `python -m build`，不要因此把 `build` 加进运行时依赖。
- 插件本地包验证后，release 产物仍以 tag-triggered GitHub Actions 为准；本地 zip 只是验证，不提交。
- backend release 是 source tag 校验，不一定有 GitHub Release 资产；插件和桌面安装包由对应 workflow 发布，聚合 release 再收敛当前插件 / 桌面资产。
- 推 main 后再推 tag，确认 CI、backend source tag、extension package、desktop installers、pages build 都成功；聚合 release 只允许收录同版本资产，某个 channel 还没完成时应显示未发布，不能回填上一版桌面或插件包。
- 确认聚合 release `openbiliclaw-vX.Y.Z` 只包含当前版本资产，尤其不要混入旧 `.dmg` / `.exe`。
- 如果有 Chrome Web Store / Firefox AMO / 其他插件市场，按项目 workflow 触发上传或说明为什么不能发；Chrome Web Store 审核异步，成功上传不等于立刻对用户可见。
- 发布后把 release 链接、tag、commit、workflow 结果和本地残留未提交文件一起汇报。

## 常见失败模式

- 只加了爬取命令，没有接 formal discover。
- Search 型来源只接 `KeywordFetchCoordinator.claim()`，漏掉 `KeywordPlanner` 平台集合和 merged prompt，导致 query generation 没有真正复用统一链路。
- 只接 merged prompt 轨，漏掉 keyword inspiration axis 轨的 `_PLATFORM_QUERY_STYLES` / allocation targets（或反之），新平台在其中一条生成轨上永远拿不到词。
- 策略 / producer 自设 admission min_score，绕过 `discovery/admission.py` 的统一入池阈值。
- engagement 计数只在某个 fetch 子路径映射，同一内容换个入口（bootstrap / activity / collection）计数全 0。
- 把平台结构性缺失的计数当 bug，硬造占位值或假数据。
- 封面走 `/api/image-proxy` 却没把 CDN 域加进 `ALLOWED_IMAGE_HOST_SUFFIXES`，卡片全部断图 403；或 CN CDN 没加 direct-fetch 后缀，被系统代理出口 IP 风控拦掉。
- 只加后端，没有插件登录态任务。
- 用临时浏览器自动化替代真实安装插件的登录态浏览器。
- 用错误 LLM provider / mock provider 跑 eval，和用户本地真实配置不一致。
- smoke 默认写 memory 或触发画像。
- 多个来源分支因为映射到同一 event type 而错误共享额度。
- 配置页能保存，但 runtime source policy 没有使用。
- 只做插件配置页，漏掉 PC Web；或只做平台开关，漏掉候选池 share。
- 旧 `config.toml` 缺新字段时崩溃或默默禁用。
- `/api/sources/status` 永远显示固定状态，或测试漏掉 `unverified` 等插件任务源合法状态。
- 推荐卡只适配一端，移动 Web 或插件侧栏破版。
- 推荐卡能显示但按钮 payload / source filter / 打开链接仍按 B 站假设工作。
- 只跑单元测试，不跑真实 E2E。
- 真实 E2E 用了临时自动化浏览器，没有用已安装插件的登录态浏览器。
- 把根目录截图、`dist/`、zip 包等临时产物提交进仓库。
- 发布时复用已存在的版本号或 tag。
- 已经存在 release tag 后继续改旧版本 changelog / README，导致“旧 tag 说明包含新代码”。
- 只确认插件 / 后端 workflow 成功，没等桌面 workflow 更新聚合 release；或发现 Latest Release 里混入上一版插件 / 桌面资产却没有清理。
- Chrome Web Store workflow 没触发，或把“GitHub 插件包已发”误当成“插件市场已提交审核”。
- 混合后端 fallback 成功后，把默认 CLI / SDK credential 也说成已通。
