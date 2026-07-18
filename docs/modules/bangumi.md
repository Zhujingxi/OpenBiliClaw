# Bangumi 来源

> Bangumi 是只读的正式内容来源。实现使用官方 `https://api.bgm.tv/v0`，不读取 Cookie、不申请 OAuth，也不提供任何站内写操作。默认匿名；可选配置个人令牌（Personal Access Token，Bearer）以自动识别当前用户并读取本人私密收藏。

> **网络要求**：Bangumi API（`api.bgm.tv`）与封面 CDN（`lain.bgm.tv`）都是**海外服务**（Cloudflare）。国内网络在默认 `[network].mode = "direct"` 下会连接超时（实测 2026-07-18），需把 `[network]` 配为 `system` 或 `custom` 代理才能正常抓取；Bangumi 出口复用统一的 `[network]` outbound policy。

## 已实现功能

| 能力 | 状态 | 说明 |
|------|------|------|
| Subject 搜索 | ✅ | `POST /v0/search/subjects`；统一关键词 planner 产出的 Bangumi 查询由 producer claim 后抓取 |
| 排名浏览 | ✅ | `GET /v0/subjects?sort=rank`；按配置的 subject type 轮转 |
| 日期浏览 | ✅ | `GET /v0/subjects?sort=date`；用户界面明确标为“按日期浏览（可能含未播条目）”，不宣称实时最新 |
| 公开收藏初始化 | ✅ | 仅在用户显式提供公开用户名后读取 `GET /v0/users/{username}/collections`；可作为唯一画像来源 |
| 个人令牌认证 | ✅ | 可选 `access_token`（https://next.bgm.tv/demo/access-token 生成，约 1 年有效）；`GET /v0/me` 自动识别用户名，收藏读取带 Bearer 并包含本人私密收藏；无令牌时行为与匿名路径完全一致 |
| 令牌过期降级 | ✅ | 同步/发现期收到 401/403 → `unauthorized` 错误码；producer 记 WARNING（不打印令牌）并降级为匿名公开发现；init 阶段返回 `invalid_token` 状态并给出重新生成指引 |
| 拒绝状态持久化并可见 | ✅ | 401/403 降级时把拒绝标记持久化到 `bangumi_discovery_state`（`state_key='token_rejected'`，`note` 存令牌 SHA-256 前 12 位指纹 + ISO 时间戳，**绝不存令牌本身**）；重启后配置仍有该令牌且指纹未变 → 直接走匿名不再重复吃 401；令牌变化（指纹不同）→ 先试新令牌，成功即清标记，再 401 则按新指纹重记。`/api/sources/status` 增加 `token_state`（`ok`/`rejected`/未配置缺省），rejected 时 detail 明写"个人令牌已被拒绝（可能过期）…请重新生成"，桌面 Web 与扩展 popup 状态区渲染警示（红点/"令牌已失效"），凭据卡 detail 追加失效提示 |
| 设置页保存 /v0/me 校验 | ✅ | `PUT /api/config` 收到**新的非 masked 非空** `access_token` 时镜像 init 语义经 `resolve_access_token_identity`(`/v0/me`) 校验：401 → HTTP 400 `invalid_bangumi_access_token`，网络/上游失败 → 502 `bangumi_token_check_failed`，绝不静默接受坏令牌；校验通过才写入令牌 + `/v0/me` 用户名并清除拒绝标记。masked echo / 省略 key / 其它配置保存零网络 |
| 清除令牌入口 | ✅ | 桌面设置页与扩展 popup 设置页各有「清除已保存的令牌」勾选控件，勾选后本次保存显式发送 `access_token:""` → 后端清空令牌并清除拒绝标记，GET `access_token_set` 变 `false`；不破坏"留空=保持不变"语义 |
| 扩展自动识别 | ✅ | 浏览器扩展在 bgm.tv/bangumi.tv 上读取公开的 `CHOBITS_UID`（MAIN-world 桥）+ 导航栏 `/user/<username>` 链接，上报 `POST /api/sources/bangumi/identity` 持久化；guided init/CLI 在既无令牌又无显式用户名时自动回退使用；只采集 uid+username（公开信息），不碰 Cookie，不采集浏览行为 |
| 统一候选池 | ✅ | Subject 归一化后只进入 `discovery_candidates`，由共享 evaluator/admission 决定是否进入 `content_cache` |
| 目录指标 | ✅ | `rating_score`、`rating_count`、`source_rank` 与真实 `favorite_count` 贯穿候选池、推荐/惊喜 API 和三端卡片 |
| 设置与状态 | ✅ | 桌面 Web、扩展设置页和 `/api/sources/status` 支持开关、用户名、**个人令牌**、分支、类型、预算、节流和 pool share；令牌为 password 输入 + 生成链接，GET config 只回传 `access_token_set` 布尔（绝不回传明文），留空保存则保持已配置令牌不变 |
| CLI smoke | ✅ | 公开收藏、搜索、排名和日期浏览默认只读；正式 `discover --source bangumi` 才写待评估池 |
| 账号写回 | 不支持 | 本地收藏/稍后再看仍可用；首版不修改 Bangumi 收藏、评分或进度 |

## 数据流

```text
KeywordPlanner ──claim("bangumi")──┐
                                   ├─ BangumiDiscoveryProducer
rank/date cursor + branch budgets ─┘       │
                                           ▼
                                  official Bangumi v0 API
                                           │
                                           ▼
                               bangumi_subject_to_content()
                                           │
                                           ▼
              discovery_candidates → shared LLM eval/admission → content_cache
                                           │
                                           ▼
                            recommendation/delight API → PC/mobile/extension

explicit public username → public collections → unified events → guided init/profile
```

`BangumiDiscoveryProducer` 是 fetch-only producer。它维护 search/ranked/latest 各自的 UTC 日预算、cursor、最小调度间隔和持久化 cooldown；遇到 `429` 时遵循合法 `Retry-After`，停止本轮剩余请求，并让后续 runtime tick 继续恢复。429 属于上游暂时限流，当前及尚未执行的关键词会 rollback，不会被误记 zero-yield；若前面的关键词已经产出候选，这些候选仍以 `partial` 结果入队并计入实际预算。官方 browse API 会在持久化 offset 超出缩小后的 total 时返回 400；producer 对非零 cursor 的首次 `invalid_request` 会先持久化归零、再仅重试一次，避免 ranked/latest 永久卡死。ranked/latest 还会为每个 mode 持久化 subject-type 起始位：即使单轮 `limit` 小于类型数，后续轮次也会依次覆盖 book/game/music/real，不会长期只抓配置列表第一项。预算只按跨分支去重并应用最终 `limit` 后仍保留的候选扣减，分支召回的重复或被截断条目只进入 `discovered` 诊断、不消耗额度。空搜索结果和领取后规范化为空的关键词会标为 zero-yield/failed，而不是伪装成已使用或反复 rollback。当所有启用分支都因当日预算耗尽（`branch_limit <= 0`）而完全没有发起请求时，producer 返回顶层 `reason=budget_exhausted`（而非 `empty`），`mode_results` 逐分支如实记为 `budget_exhausted`，CLI 据此提示"今日预算已用完，可在配置页调整分支预算"而非误报"官方 API 可达但无可转换条目"；只要有任一分支真正跑通（即便为空），仍按 `empty`/`partial`/`ok` 判定。

## 公开 API

### `BangumiClient`

```python
from openbiliclaw.sources.bangumi_client import BangumiClient

client = BangumiClient(request_interval_seconds=1)
try:
    search_page = await client.search_subjects(
        "攻壳机动队",
        subject_types=("anime",),
        limit=10,
    )
    ranked_page = await client.browse_subjects(
        "anime",
        sort="rank",
        limit=10,
    )
    collections = await client.get_user_collections("sai", limit=20)
finally:
    await client.aclose()

# 个人令牌通道：全部请求带 Bearer，可解析当前用户并读私密收藏
authed = BangumiClient(access_token="<personal access token>")
try:
    me = await authed.get_me()          # 401/403 → BangumiAPIError("unauthorized")
    page = await authed.get_user_collections(me["username"], limit=20)
finally:
    await authed.aclose()
```

客户端边界固定如下：

- Base URL 固定为 `https://api.bgm.tv`，timeout 为 15 秒，单页 `limit` 收敛到 `1..50`。
- 每次请求携带可识别的项目 User-Agent；网络代理复用统一 `[network]` outbound policy。
- `400/404/429/5xx`、网络失败和 schema drift 转为带稳定 `code` 的 `BangumiAPIError`；`401/403` 统一映射为 `unauthorized`（令牌缺失/错误/过期），绝不静默吞掉。
- 搜索虽然使用 POST，但只是官方只读查询；客户端没有修改收藏的 POST/PATCH 方法。
- `access_token=None` 时行为与历史匿名客户端逐字节一致（无 Authorization 头）。
- 令牌相关辅助 API：`validate_bangumi_access_token()`（结构校验，非空/单行 ASCII/≤512）、`me_username()`（防御性解析 `/v0/me`，缺 `username` 抛 `schema_changed`）、`resolve_access_token_identity()`（一次 `/v0/me` 校验并返回用户名，供 init/CLI 在持久化前拒绝坏令牌）、`client.disable_access_token()`（401 后降级为匿名请求）。日志只记录令牌存在与否/长度，绝不打印明文。

### 归一化

```python
from openbiliclaw.sources.bangumi import bangumi_subject_to_content

content = bangumi_subject_to_content(row, strategy="bangumi-ranked")
```

Subject identity 使用 `bangumi:<decimal subject id>`，页面 URL 为 `https://bgm.tv/subject/<id>`，`content_type="subject"`。首选中文名、再回落原名；封面按官方 image variants 回落；`nsfw=true` 始终丢弃。评分不会冒充点赞或评论：只有官方收藏人数进入 `favorite_count`，其它缺失 engagement 字段保持 0。

公开收藏通过 `bangumi_collection_to_event()` 转成统一事件：想看/看过/在看/搁置/抛弃使用不同信号强度，私有行丢弃，短评清洗后截断；官方 `updated_at` 不被当作可靠收藏时间或增量 cursor。令牌认证路径下（`include_private=True`，仅在读取令牌所有者本人收藏时传入），私密行同样转为画像信号；匿名路径行为不变。`fetch_bangumi_public_collection_events(..., include_private=...)` 逐 lane 透传该开关。

## 配置与状态

```toml
[sources.bangumi]
enabled = false
username = ""
# 可选个人令牌：https://next.bgm.tv/demo/access-token 生成（约 1 年有效）。
# 设置后 init/discovery 经 /v0/me 自动识别账号并带 Bearer 读取本人（含私密）收藏；
# 留空保持匿名公开用户名老路。视同密码保管；config.toml 已被 gitignore。
access_token = ""
subject_types = ["anime", "book", "game"]
source_modes = ["search", "ranked", "latest"]
daily_search_budget = 300
daily_ranked_budget = 100
daily_latest_budget = 100
request_interval_seconds = 1
min_interval_minutes = 60
bootstrap_limit = 300

[scheduler.pool_source_shares]
bangumi = 1
```

`GET /api/sources/status` 对 Bangumi 只读本地配置和 producer ledger，不会在打开设置页时访问上游。常见状态为：未启用 `disabled`、已启用但尚无真实运行 `unverified`、最近成功 `ready`、部分分支失败 `partial`、限流冷却 `rate_limited`、最近运行错误 `error`。配置了个人令牌时额外返回 `token_state`（`ok`=已配置且无拒绝标记 / `rejected`=令牌被拒已降级匿名；未配置则缺省），`rejected` 时 `detail` 明示已被拒绝并指引重新生成，配置了令牌时 detail 不再说"无需登录"。读取拒绝标记同样纯本地（`bangumi_discovery_state`），不访问上游。凭据说明固定为公开 API、无需 Cookie；令牌为可选。

桌面 Web 与扩展设置页都列出官方五种合法条目类型：动画、书籍、游戏、音乐和三次元；默认仍只勾选 `anime/book/game`。保存已有 `music/real` 配置时不会因界面缺少控件而静默丢失。

## CLI

```bash
# 公开收藏 smoke：默认不写 memory、不调用 LLM
openbiliclaw fetch-bangumi --username sai --limit 20

# 个人令牌路径：自动识别当前用户（/v0/me），带 Bearer 读取含私密收藏
openbiliclaw fetch-bangumi --token <personal-access-token> --limit 20

# guided init 提供令牌（先经 /v0/me 校验，坏令牌当场拒绝并给出真因）
openbiliclaw init --yes-bangumi --bangumi-token <personal-access-token>

# 三个只读发现 smoke
openbiliclaw discover-bangumi "攻壳机动队" --limit 10
openbiliclaw discover-bangumi-ranked --limit 10
openbiliclaw discover-bangumi-latest --limit 10

# 正式 producer：写 discovery_candidates，交给共享 evaluator
openbiliclaw discover --source bangumi --limit 30
```

`fetch-bangumi --write-memory` 才会写本地事件；`--rebuild-profile` 还会真实调用配置中的 LLM，并隐含要求写 memory。`fetch-bangumi --token`（缺省读 `[sources.bangumi].access_token`）优先于用户名：命中令牌即经 `/v0/me` 解析账号并读含私密收藏；两者皆缺时报错提示"提供 --token（推荐）或 --username"。guided init 若只选择 Bangumi，则必须提供 `--bangumi-token`（推荐，自动识别当前用户）或 `--bangumi-username`（公开用户名）；令牌在持久化前先经 `/v0/me` 校验，被拒绝（401）当场退出并指引重新生成；显式用户名与 `/v0/me` 不一致时以 `/v0/me` 为准并提示。校验通过的令牌与解析出的用户名写入 config，供后台周期发现与下次 init 复用；同步期令牌过期（401）则记 WARNING 并降级到匿名公开路径。API 侧 `source_options.bangumi` 白名单为 `{username, access_token}`；`access_token` 显式出现且非空时以本轮值为准（token 缺省时回退已配置令牌）。若与其它画像来源混用而用户名为空，Bangumi 仅参与后续 discovery，并返回明确 warning。`source_options.bangumi.username` 显式出现时以本轮值为准，包括空字符串；只有字段缺失的旧客户端才回退已保存用户名。扩展 popup、桌面 Web 与打包 setup 三端据此约定：仅当用户手动编辑、或在成功 prefill 后显式清空该字段时才发送 `username`（清空即发送 `""` 覆盖配置）；prefill 失败/未完成或字段从未被触碰时省略该字段，避免用空值误删已配置用户名。三端还会读取 `/api/init` 202 响应里的 `warnings` 并按现有状态/提示样式安全渲染（如未填公开用户名的 discovery-only 提示），不再静默丢弃。`fetch_bangumi_public_collection_events` 对正常 bootstrap 按 50 行请求，较小的全局 `limit` 则不超过目标量，并把富余行按 lane 缓存复用；仍以 `per_pair` 公平份额、去重、限速、终止与不过量导入为界，用较大的缓冲分页替代大量小页。显式 `discover --source bangumi` 仍要求来源自身启用，但不受后台 `[scheduler].enabled` 总开关限制；该总开关只控制 daemon-owned 调度。

## 安全边界

- 不读取 Bangumi Cookie 或浏览器登录态；扩展在 `bgm.tv` / `bangumi.tv` 上确有 host permission，但仅用于账号身份识别（读公开 uid + 用户名），不采集浏览行为、不传令牌。唯一写入型凭据是用户显式粘贴的个人令牌。
- 令牌只存 `config.toml`（gitignored），设置 GET 响应不回传明文（masked echo 不覆盖已存令牌）；日志只记录存在与否/长度。令牌拒绝标记只在本地 SQLite (`bangumi_discovery_state`) 里存令牌的 SHA-256 前 12 位指纹（不可逆推明文，仅用于区分"同一坏令牌"与"用户换了新令牌"）与拒绝时间，不涉及明文，因此不改变隐私边界或商店披露。
- 不调用收藏/评分/进度写接口；真实 smoke 只允许公开读取和本地写入。
- 封面只允许现有图片代理白名单中的 `lain.bgm.tv`，页面链接只构造 `bgm.tv/subject/<id>`。
- API 状态页纯本地；只有显式 CLI/init/discover 或启用后的后台 producer 会访问 Bangumi。统一状态 DTO 的 legacy `logged_in` 对 Bangumi 表示本地 discovery 是否已有 ready 结果，不表示存在账号会话；未运行时为 `unverified/false`。

## 前端表面

扩展 popup、桌面 Web 与打包 setup 三个 GUI init 面板都提供可选"个人令牌"输入（password 型，附生成链接 https://next.bgm.tv/demo/access-token），仅在用户输入时发送 `source_options.bangumi.access_token`（留空即省略，保留已配置令牌），并映射 `invalid_bangumi_access_token` / `bangumi_token_check_failed` 错误文案。加上 CLI 的 `--bangumi-token`，令牌通道满足完整 four-surface 契约，无排除项。

### 扩展自动识别通道（零配置主推路径）

浏览器扩展在 `*.bgm.tv` / `*.bangumi.tv` 注册两段脚本（对应新增 host permission，商店披露需同步更新）：

- **MAIN-world 桥 `dist/main/bgm-identity-bridge.js`** — 读取页面公开全局 `window.CHOBITS_UID`（>0 即已登录；隔离世界读不到页面全局，故需桥，模式同 xhs-state-bridge），postMessage `{source:"obc-bgm-identity", uid}`；登出（uid=0）时绝不上报，避免覆盖已知身份。
- **isolated content script `dist/content/bangumi.js`** — 只做身份识别，不做行为采集：收到桥消息后**只**从本人专属导航区（`idBadgerNeue` / `#dock`）的 `/user/<username>` 链接解析用户名，非法值当缺失。真机 E2E（2026-07-18）实证泛化 `a.avatar[href*='/user/']` 兜底会在匿名首页命中时间线路人头像（/user/yuzzyu、/user/474349）并把陌生人用户名当成本人，该类兜底已删除；DOM 抓不到就上报 `username: ""`，交给后端权威解析。

后端把身份持久化到 `discovery_runtime_state["bangumi_self_info"]`（`data/memory/discovery_runtime.json`；非正整数 uid 422 拒绝、非法用户名降为缺失）。**持久化前做权威校验**（匿名公开端点 `GET /v0/users/{username}`，`trust_env=False`，不缓存失败结果）：API 实测（2026-07-18）路径参数只认 username slug（`/v0/users/1` 404），但未设自定义 slug 的用户 `username == str(uid)`（`/v0/users/474349` → `id=474349`），故 uid-only 上报对默认 slug 用户也能解析。规则：API `id` 与上报 uid 一致 → 持久化 API 返回的 username（权威值）；username 属于其他 uid 或不存在 → **只存 uid、丢弃 username 并 log WARNING**（plausible-but-wrong 防线）；网络/上游失败 → best-effort 接受 DOM 值（debug log），下次上报再校验。guided init 与 CLI init 的用户名解析按优先级取值：**令牌 `/v0/me` > 显式/已配置用户名 > 扩展上报用户名 > 报错**；命中扩展身份时在 202 warnings/CLI 输出中明示"使用浏览器扩展识别到的账号"。隐私边界：uid 与用户名本就是公开资料（构成用户主页 URL），通道不读 Cookie、不传令牌、不采集任何浏览行为。init 写保护中间件对 `POST /api/sources/bangumi/identity` 做精确路径放行（`_init_write_allowlist`），因此扩展在 guided init 进行中上报的身份能当轮落地——正是三级账号解析最需要它的时刻，而非被 409 拦到下一轮。

## 已知限制

- **海外网络依赖**：见顶部「网络要求」——`api.bgm.tv` / `lain.bgm.tv` 均为海外服务，CN 网络默认 `direct` 会超时，需 `[network]` 走 `system`/`custom` 代理。
- **作者字段恒空**：Subject 无「制作方 / 出版社」结构化字段，`sources/bangumi.py` 未填 `author_name`（归一化后恒为 `""`）。补制作方需对每个 subject 额外请求 `/v0/subjects/{id}` 的 `infobox`，成本与收益待后续评估，本版不做。
- **delight 惊喜信号不适配 bangumi**：`recommendation/delight.py:443-449` 的 gem 信号要求 `view_count ≥ 100`，而 Bangumi 归一化从不写 `view_count`（无播放量语义），故该门对 bangumi 恒不满足；`rating_score` 目前也未纳入 delight。Bangumi 的惊喜因此依赖语义新颖 / 跨域信号而非播放量 gem。调整这些阈值属推荐质量改动、须过推荐质量门，列为已知限制不在本分支动。

## 测试

- `tests/test_bangumi_client.py`：请求契约、节流、分页、错误与 schema drift；令牌分支（Bearer 头、`get_me`、401→`unauthorized`、`disable_access_token` 降级、`me_username`/token 校验）。
- `tests/test_bangumi_source.py`：subject/collection 归一化、NSFW、指标和信号矩阵；`include_private` 透传（匿名跳过私密行、认证保留）。
- `tests/test_bangumi_producer.py`：预算、cursor、cooldown、关键词生命周期和本地状态；401 令牌降级（丢 Bearer 继续匿名发现）；拒绝标记持久化（指纹非明文）、同指纹重启即匿名不发 Bearer、换新令牌成功清标记，以及 `token_state` 三态。
- `tests/test_bangumi_web_surfaces.py` 与扩展 Node tests：设置、身份、URL、目录指标和 guided init 契约（含 setup/popup 令牌输入与 `source_options.bangumi.access_token` 发送规则）；桌面与 popup 的「清除令牌」控件、`access_token:""` 发送与 `token_state==='rejected'` 警示渲染。
- `tests/test_cli.py` / `tests/test_api_app.py`（`-k bangumi`）：init 令牌→用户名自动解析、坏令牌 400 拒绝、令牌+用户名持久化、`invalid_token` 状态与仅 Bangumi 无凭据的报错文案；`/api/sources/bangumi/identity` 校验/幂等/脏值降级、guided init 扩展身份回退与三级优先级、CLI `_load_extension_bangumi_username` 读取与脏值容错；`/api/sources/status` 的 `token_state` 三态、`PUT /api/config` 新令牌 401→400 拒绝 / 成功清标记且回写 `/v0/me` 用户名 / `access_token:""` 离线清空+清标记 / masked echo 与省略 key 零网络。
- `extension/tests/bangumi-identity.test.ts`：`CHOBITS_UID` 解析、`/user/<username>` 提取（本人专属选择器与登出空值）、匿名首页路人头像不泄漏进用户名的回归（真实 E2E DOM 场景）、桥消息过滤，及双 manifest 的 host/content-script 注册契约。后端侧另有 uid↔username 不一致丢弃、uid-only 权威解析、自定义 slug 404 保持 uid-only、API 失败降级四条 `-k bangumi_identity` 测试。

完整设计与验收边界见 [Bangumi 来源 Spec](../plans/2026-07-17-bangumi-source-spec.md)。
