# 2026-07-17 — Bangumi 来源接入 Spec

> 状态：Verified（2026-07-17）。实现与验证位于 `feat/bangumi-source` worktree；尚未提交、推送或发布。
>
> 实施计划：[`2026-07-17-bangumi-source-plan.md`](2026-07-17-bangumi-source-plan.md)

## 0. 目标与范围

给 OpenBiliClaw 增加第八个正式内容来源 **Bangumi**，使用 Bangumi 官方 `v0` API 完成两条只读链路：

1. **内容发现**：`search / ranked / latest` 三个分支把动画、书籍、游戏等条目归一化为统一候选，进入现有 `discovery_candidates → LLM eval → content_cache → recommendation` 链路。
2. **可选画像初始化**：用户明确填写 Bangumi 用户名后，读取该账号的**公开收藏**，转换为统一事件；因此 Bangumi 可以在确有公开收藏信号时作为唯一初始化来源。

首版不需要浏览器插件登录态，不新增扩展 host permission，不重放 Cookie。桌面 Web、移动 Web、扩展 side panel 和 CLI 仍需完整识别 Bangumi 候选、状态与配置。

### 0.1 In scope

- 固定 `slug/source_platform = "bangumi"`。
- 官方 API 直连客户端；不引入第三方 SDK。
- 条目搜索、排行榜浏览、新近条目浏览。
- 统一关键词生成双轨和 `KeywordFetchCoordinator.claim("bangumi")`。
- 可选公开收藏初始化；smoke 默认不写 memory、不重建画像。
- 来源开关、条目类型、分支预算、候选池 share、用户名配置。
- Bangumi 评分、评分人数、站内排名的候选池/API/UI 端到端透传。
- PC、移动 Web、扩展推荐卡与设置/初始化入口。
- 单元测试、官方 API 安全 smoke、真实本地 LLM eval 和三端视觉验证。

### 0.2 Out of scope

- OAuth、Access Token、私有收藏。
- Bangumi 站内收藏、评分、章节进度等任何写操作。
- 浏览器插件在 `bgm.tv` 页面被动采集点击或收藏行为。
- 角色、人物、目录、章节作为独立推荐内容。
- 基于 `/v0/subjects/{subject_id}/subjects` 的 related 分支；该接口返回的信息不足以直接评估，首版不做 N+1 详情补全。
- Bangumi 原生 App deep link；移动端使用网页 fallback。
- 定时同步公开收藏。官方 schema 明确提醒 `updated_at` 不能可靠表示收藏更新时间，首版不据此建立增量游标。

## 1. 官方 API 调研基线

调研日期：2026-07-17。API schema 以 `bangumi/api@c7e931393fb47f3aa7908808d2abc5f1a4edfbc9` 为基线；当天真实响应头报告服务版本 `2026-07-02-6d21705`。

一手资料：

- [Bangumi API 仓库](https://github.com/bangumi/api)
- [OpenAPI v0 schema](https://github.com/bangumi/api/blob/master/open-api/v0.yaml)
- [User-Agent 建议](https://github.com/bangumi/api/blob/master/docs-raw/user%20agent.md)
- [OAuth 说明](https://github.com/bangumi/api/blob/master/docs-raw/How-to-Auth.md)

已验证的只读接口：

| 能力 | 接口 | 鉴权 | 本项目用途 | 已知约束 |
| --- | --- | --- | --- | --- |
| 条目搜索 | `POST /v0/search/subjects` | 无 | `search` | 官方标为实验性，schema/行为可能变化 |
| 条目浏览 | `GET /v0/subjects` | 可选 Bearer | `ranked / latest` | `type` 必填；`sort=rank/date`；分页上限 50；首屏有服务端缓存 |
| 条目详情 | `GET /v0/subjects/{id}` | 可选 Bearer | smoke/诊断、未来补全 | 不是首版 producer 的必经 N+1 请求 |
| 用户公开收藏 | `GET /v0/users/{username}/collections` | 可选 Bearer | init/fetch smoke | 私有收藏只有 token 可见；分页上限 50 |
| 当前用户 | `GET /v0/me` | Bearer | 不使用 | 属于 OAuth 范围 |
| 修改收藏 | `POST/PATCH /v0/users/-/collections/{id}` | Bearer + write scope | 不使用 | 明确排除写操作 |

官方要求非浏览器客户端显式发送可识别的 User-Agent；请求库默认 UA 可能被禁用。OpenBiliClaw 使用：

```text
whiteguo233/OpenBiliClaw/<version> (https://github.com/whiteguo233/OpenBiliClaw)
```

官方资料没有给出可依赖的固定数值限流额度。因此实现不得猜测 QPS；采用本地保守节流、每日预算，并对 `429`/`Retry-After` 做显式冷却。这是基于官方文档缺少数值限额的工程推断，不是 Bangumi 的承诺。

## 2. 关键设计决策

| 决策点 | 选定方案 | 理由 |
| --- | --- | --- |
| 后端 | 官方 `api.bgm.tv/v0` + in-repo `httpx` 客户端 | 接口公开稳定、零新依赖、易 mock |
| 登录/凭据 | 首版完全无凭据 | 搜索、浏览和公开收藏均可匿名读取 |
| 初始化身份 | 配置中的 `username` | 用户主动提供；不读取浏览器 Cookie，不猜当前账号 |
| 发现分支 | `search / ranked / latest` | 分别覆盖画像意图、长期高质量、新近供给；不把“排行榜”伪称“热门” |
| 默认条目类型 | `anime / book / game` | 聚焦 Bangumi 的 ACG 核心供给；设置页也可开启 `music / real` |
| NSFW | 首版强制排除 | 请求显式传 `nsfw=false`，归一化层再次丢弃 `nsfw=true` |
| 用户可见评分 | 新增通用 `rating_score / rating_count / source_rank` 字段 | 评分不是点赞/收藏，不能塞进 engagement 假装同一种指标 |
| 卡片形态 | `content_type="subject"`，有封面走图片卡，无封面走现有 text fallback | 不新增平行媒体模型 |
| 收藏写回 | 仅 OpenBiliClaw 本地收藏/稍后再看 | Bangumi 站内写回需要 OAuth，另立 spec |
| 网络代理 | 复用 `[network]` outbound policy | Bangumi 不在仓库强制 direct 的国内客户端清单内；不读取环境代理的行为由统一网络配置决定 |

## 3. 来源契约

### 3.1 身份与内容单元

| 维度 | 值 |
| --- | --- |
| `slug` / `source_platform` / config key / pool share key | `bangumi` |
| 显示名 | `Bangumi` |
| 内容单元 | 条目 subject |
| 稳定 ID | 十进制 `subject.id` 转字符串 |
| `content_url` | `https://bgm.tv/subject/<id>` |
| `content_type` | `subject` |
| 候选 key | `bangumi:<subject_id>` |
| 来源策略 | `bangumi-search` / `bangumi-ranked` / `bangumi-latest` |
| 是否只读 | 是；所有默认及 E2E 请求均为 GET/搜索 POST，不修改账号状态 |

`bgm.tv` 的条目 URL必须始终显式写入候选；三端 fallback URL builder 仍补测试，防止缺 URL 时错误拼成 B 站视频链接。

### 3.2 条目类型

配置使用稳定英文名，客户端边界再映射官方整数 enum：

| 配置值 | API `SubjectType` | 展示 |
| --- | ---: | --- |
| `book` | 1 | 书籍 |
| `anime` | 2 | 动画 |
| `music` | 3 | 音乐 |
| `game` | 4 | 游戏 |
| `real` | 6 | 三次元 |

未知值保存时拒绝；旧配置缺字段时回填默认 `anime/book/game`。不把不存在的 type 5 发送给上游。

### 3.3 Engagement 六项契约

Bangumi 的站内评分是 1–10 分目录评分，不等于社交平台点赞。六项统一 engagement 只映射真实同义数据：

| 统一字段 | Bangumi 映射 | 说明 |
| --- | --- | --- |
| `view_count` | 结构性缺失，保持 0 | 官方 subject 响应不提供浏览量 |
| `like_count` | 结构性缺失，保持 0 | 评分不能冒充点赞 |
| `favorite_count` | `collection_total`，或 `wish + collect + doing + on_hold + dropped` | 表示收藏/收录该条目的总人数 |
| `comment_count` | 结构性缺失，保持 0 | `rating.total` 是评分人数，不是评论数 |
| `share_count` | 结构性缺失，保持 0 | 官方 subject 响应不提供 |
| `danmaku_count` | 结构性缺失，保持 0 | 非 Bangumi 内容结构 |

额外目录指标：

| 字段 | 来源 | 规则 |
| --- | --- | --- |
| `rating_score: float` | `rating.score` / `SlimSubject.score` | `0..10`；0 表示未知，不渲染 |
| `rating_count: int` | `rating.total` | 非负；0 表示未知，不渲染 |
| `source_rank: int` | `rating.rank` / `SlimSubject.rank` | 正数才渲染 `#N` |

三个字段必须贯穿 `DiscoveredContent → DiscoveryCandidateWrite → discovery_candidates → content_cache → RecommendationOut/PendingDelightOut → PC/移动/插件卡片`。不得只留在 `raw_payload`，否则 admission 后会丢失。

### 3.4 状态变更边界

- 安全 E2E：搜索、排行榜、新近条目、读取公开收藏、打开条目页、截图、OpenBiliClaw 本地收藏/稍后再看。
- Bangumi 账号状态变更：全部不存在于首版客户端接口，测试也不得通过临时脚本调用写 API。
- 未来若增加站内“想看/看过/评分”，必须单独设计 OAuth、scope、token 存储、精确授权和幂等回滚；不能在本 spec 上顺手追加。

## 4. 数据流

### 4.1 正式发现

```text
KeywordPlanner / Inspiration Axis
  └─ discovery_keywords(platform="bangumi")
       └─ BangumiDiscoveryProducer
            ├─ search  ── POST api.bgm.tv/v0/search/subjects
            ├─ ranked  ── GET  api.bgm.tv/v0/subjects?sort=rank&type=...
            └─ latest  ── GET  api.bgm.tv/v0/subjects?sort=date&type=...
                 └─ normalize subject → DiscoveredContent
                      └─ shared admission policy + discovery_candidates
                           └─ real configured LLM eval
                                └─ content_cache → recommendation APIs → 三端卡片
```

### 4.2 公开收藏初始化

```text
CLI / PC setup / extension guided-init
  └─ 用户明确填写 Bangumi username
       └─ GET /v0/users/{username}/collections (read-only, paged)
            └─ collection row → unified event(source_platform="bangumi")
                 └─ init ownership → memory batch → preference/profile build
```

Bangumi 不经过扩展 task queue，不新增 `/api/sources/bangumi/{next-task,task-result,kick}`。直连结果使用本地 run ledger 持久化状态和分支计数。

## 5. `BangumiClient` 契约

新增 `src/openbiliclaw/sources/bangumi_client.py`：

```python
class BangumiClient:
    async def search_subjects(
        self,
        keyword: str,
        *,
        subject_types: tuple[str, ...],
        limit: int,
        offset: int = 0,
        sort: str = "match",
    ) -> PagedSubjects: ...

    async def browse_subjects(
        self,
        subject_type: str,
        *,
        sort: str,
        limit: int,
        offset: int = 0,
    ) -> PagedSubjects: ...

    async def get_user_collections(
        self,
        username: str,
        *,
        collection_type: int | None = None,
        limit: int,
        offset: int = 0,
    ) -> PagedCollections: ...
```

实现约束：

- Base URL 固定为 `https://api.bgm.tv`，不开放任意 base URL 配置，避免把生产配置变成 SSRF 入口。
- 复用 `network.outbound_httpx_kwargs()`；客户端可注入 `httpx.AsyncClient`/transport 供测试。
- 请求 timeout 15 秒；一次失败不自动无限重试。
- 分页 `limit` 强制 clamp 到 `1..50`，offset 非负。
- 所有请求发送项目 User-Agent、`Accept: application/json`；搜索发送 JSON content type。
- `429` 解析合法 `Retry-After`，落持久化 cooldown；没有 header 时用有界退避。
- `400/404/429/5xx/timeout/schema drift` 转为稳定错误码，不吞掉上游原因。
- `search/subjects` 是实验性 API：缺 `data`、类型错误或条目 schema 明显漂移时返回 `bangumi_schema_changed`，不得当作成功空结果缓存。
- 正常 200 + `data=[]` 是合法 empty，不等于网络失败；关键词生命周期应标 `failed/zero-yield`，不能标 used。
- 不实现本地响应缓存；候选层已有 identity dedupe，上游浏览接口本身声明了缓存。

## 6. Subject 归一化

新增 `src/openbiliclaw/sources/bangumi.py`（名称在实现期可拆为 `bangumi_normalize.py`，但公共函数边界保持稳定）：

```python
def bangumi_subject_to_content(
    row: Mapping[str, Any],
    *,
    strategy: str,
    source_keyword_id: int | None = None,
) -> DiscoveredContent | None: ...
```

字段映射：

| `DiscoveredContent` | Bangumi 来源 |
| --- | --- |
| `bvid` / `content_id` | `str(id)`；legacy 兼容字段也使用裸 ID，不加伪 BV 前缀 |
| `item_key` | 由统一 identity 生成 `bangumi:<id>` |
| `content_url` | `https://bgm.tv/subject/<id>` |
| `source_platform` | `bangumi` |
| `source_strategy` | 当前分支 |
| `content_type` | `subject` |
| `title` | 非空 `name_cn` 优先，否则 `name` |
| `author_name/up_name` | 空；条目没有统一“作者”，不得用类型或平台冒充作者 |
| `body_text` | `summary` 或 `short_summary` |
| `description` | 空，避免和 `body_text` 重复进入 LLM prompt |
| `cover_url` | `images.common`，缺失时依次 `medium / large / grid / small` |
| `published_at` | 合法 `YYYY-MM-DD` 的 `date` |
| `tags` | 去重后的 `meta_tags` + 按 count 排序的 tags，最多 20 个 |
| `favorite_count` | §3.3 的收藏总数 |
| `rating_score/count/source_rank` | §3.3 的目录指标 |
| `score_threshold` | `0.0`，只走共享 admission floor |

防御规则：

- `id <= 0`、标题与 URL 均无法构造、类型不在官方 enum：丢弃并计入 malformed telemetry。
- 请求端显式 `nsfw=false`；归一化端仍无条件丢弃 `nsfw is True`，形成双层保护。
- 数字字段只接受可安全转换的有限值；负数归零，评分 clamp `0..10`，非法值 WARNING 后回默认。
- `name_cn` 为空不算错误；使用原名。
- tag 同名大小写去重，不把空 tag 或统计字段写进标签。
- `raw_payload` 可保留 `subject_type/platform/series/volumes/eps` 供诊断，但用户评论、token 或其他秘密不得出现；评分三个正式字段不能只存在这里。

## 7. Discover 设计

### 7.1 `search`

- `KeywordFetchCoordinator.claim("bangumi")` 领取统一关键词。
- keyword store 空时，回退 Soul profile 关键词；不另起一个私有 LLM keyword generator。
- 搜索请求 `sort=match`，filter 包含配置的多个 SubjectType 与 `nsfw=false`。
- `source_keyword_id` 贯穿候选；成功交付非空候选后 mark used，异常/合法空结果及领取后规范化为空的 keyword mark failed，只有因本轮容量/预算尚未执行的合法 keyword 才 rollback。
- 搜索型来源必须同时进入两条生成轨：
  - merged prompt：`_PLANNER_PLATFORMS`、`_PLATFORM_QUERY_STYLES`、`PLATFORM_SUPPLY_ADVANTAGES`、schema/允许 key；
  - inspiration axis：allocation targets 必须能生成 `platforms=["bangumi"]` 的 axis keywords。
- Bangumi query 风格：作品题材/IP/作者/制作团队/媒介类型，避免“热议、爆款、速看”等社媒口吻。

### 7.2 `ranked`

- 对启用的 subject types 轮询 `GET /v0/subjects?type=<type>&sort=rank`。
- 将本轮 limit 按类型 round-robin 分配，不能让第一个类型耗尽所有预算。
- 每个类型保存 offset cursor；成功页后推进，超过 total 后回绕，避免每小时重复 top 50。
- 榜单是长期质量供给，UI/日志称“排行榜”，不得称“热门/实时热点”。

### 7.3 `latest`

- 对启用类型调用 `sort=date`，称“新近条目”。
- 使用独立 cursor；不得与 ranked 共用额度或 offset。
- 上游首屏缓存意味着它不是实时源，产品文案不得承诺实时更新。

### 7.4 Producer 与预算

`BangumiDiscoveryProducer` 为 fetch-only producer，结构参考 `YoutubeDiscoveryProducer`，但客户端为官方 API：

- 调度前检查 `enabled / min_interval / pool deficit / cooldown / per-mode budget`。
- 每个分支单独 ledger、daily budget、cursor 和停止原因。
- browse 的持久化非零 cursor 若因上游 total 缩小收到 `invalid_request`，必须先归零落库再最多重试一次；offset=0 仍失败则正常上报 error，不无限重试。
- 默认配置预算按**跨分支去重并应用最终 `limit` 后成功交付的候选条目数**计，再按候选最终 `source_strategy` 归属 search/ranked/latest ledger；分支内召回但跨 mode 重复或被最终 limit 截断的条目只记 discovered 诊断，不扣预算。默认 search 300、ranked 100、latest 100；0 表示不限，负数加载时拒绝/回默认。
- 默认 `request_interval_seconds=1`、`min_interval_minutes=60`。
- 请求顺序串行或最多 2 并发；同一 client 的节流在最外层统一执行。
- 分支失败互不拖垮：search schema drift 时 ranked/latest 仍可进入 pool，状态标 partial。
- `429` 只表示上游暂时限流：当前和未执行的 claimed keywords rollback，不能标成 zero-yield/failed。
- producer 只 enqueue raw candidates，不自己写 `content_cache`，不绕过统一 evaluator。
- candidate admission 只走 `effective_admission_threshold()`；Bangumi 不设低于 policy floor 的私有阈值。

正式 `openbiliclaw discover --source bangumi` 必须调用这个 producer，不能只是提示用户运行 smoke。它服从 `[sources.bangumi].enabled`，但作为用户显式命令不受 daemon `[scheduler].enabled` 总开关限制。

## 8. 公开收藏初始化

### 8.1 配置与取数

`[sources.bangumi].username` 默认为空。只有用户明确输入非空用户名时才读取公开收藏。

- 用户名作为 URL path segment 严格编码；本地仅做长度、控制字符和 `/` 校验，不猜 Bangumi 的完整字符集。
- init 最多转换 `bootstrap_limit=300` 条收藏；API 每页最多 50。
- 为避免单一状态挤满 300 条，按 `wish/done/doing/on_hold/dropped` 分支分页并公平分配剩余额度。
- `private=true` 行即使异常出现在匿名响应里也必须丢弃。
- 官方 schema 明确说明 `updated_at` 不能可靠代表收藏时间。它只能保存在 `metadata.source_updated_at` 供诊断，不能作为事件时间、增量 cursor 或“最近兴趣”排序依据。

### 8.2 收藏信号映射

显式评分优先于收藏状态：

| 条件 | 统一事件 | `signal_strength` | 语义 |
| --- | --- | ---: | --- |
| `rate >= 8` | `like` | 0.85 | 明确高分 |
| `1 <= rate <= 4` | `feedback` + `feedback_type=dislike` | 1.0 | 明确低分 |
| 无上述评分且 type=1 wish | `favorite` | 1.0 | 明确想看/想读/想玩 |
| 无上述评分且 type=3 doing | `favorite` | 0.85 | 正在消费，强于被动浏览 |
| 无上述评分且 type=2 done | `view` | 0.35 | 完成不自动等于喜欢 |
| 无上述评分且 type=4 on_hold | `view` | 0.25 | 弱上下文 |
| 无上述评分且 type=5 dropped | `feedback` + `feedback_type=dislike` | 0.60 | 放弃是负向但弱于明确低分 |

校准来源：8–10/1–4 是对 Bangumi 10 分制的保守正负分区，5–7 留给状态语义；0.85/1.0/0.35 复用现有 like/favorite/view 默认强度，0.25/0.60 只表达“搁置弱于浏览、无评分放弃弱于明确低分”。这不是生产数据校准；首次真实用户 E2E 后必须重开校准，并在任何 provider/model swap 后复核。

事件 metadata 至少包含：

```text
source_platform=bangumi
subject_id / subject_type
collection_type / user_rate
collection_tags
ep_status / vol_status
rating_score / rating_count / source_rank
source_updated_at (diagnostic only)
import_source=bangumi_public_collection
signal_strength
```

用户公开短评经 `sanitize_comment_text()` 后最多保留 200 字符为 `collection_comment`；不把它伪装成 `comment` 正向事件，因为短评可能是负面内容。

### 8.3 Init 行为

- CLI 增加 `--yes-bangumi / --no-bangumi / --bangumi-username`。
- PC setup、桌面初始化抽屉、扩展 guided-init 在 Bangumi 选项旁提供可选 username。
- UI 文案必须说明“无需登录；填写用户名可导入公开收藏，留空只启用后续发现”。现有“每个平台都要在浏览器登录”的总提示要拆成来源特定提示。
- 选中 Bangumi 且 username 非空、真实取得至少一条合法事件时，它可以作为唯一初始化来源。
- 只选择 Bangumi 但 username 为空时，API/CLI 在启动前返回 `no_profile_signal_sources`；不能等到阶段末才报 `empty_signals`。
- Bangumi 与其他来源一起选中但 username 为空时：Bangumi 作为 discovery-only 开启，不阻塞其他来源初始化，并给 warning。
- username 存在但用户不存在/公开收藏为空时：其他来源继续；若没有其他信号，则以 `empty_signals` + 明确 Bangumi 细节失败。
- 普通 `fetch-bangumi` 默认只打印统计；`--write-memory` 才持久化，`--rebuild-profile` 隐含 write 并使用用户真实配置的 LLM/embedding。

## 9. 配置、API 与状态

### 9.1 配置模型

```toml
[sources.bangumi]
enabled = false
username = ""
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

规则：

- 新来源默认 opt-in (`enabled=false`)。
- 关闭来源时保留 username、类型、预算和 share；runtime effective shares 移除 Bangumi。
- `/api/config` GET/PUT、`save_config()`、`config-show` 和旧配置缺字段回填必须 round-trip。
- username 不是秘密，但日志只记录是否配置和安全用户名，不把公开短评写入 INFO 日志。
- API base URL、OAuth token、NSFW 开关不进入首版配置。

### 9.2 来源状态

`GET /api/sources/status` 不主动访问 Bangumi，只读取配置、最新 run ledger 和 cooldown：

| state | 条件 |
| --- | --- |
| `disabled` | source 关闭 |
| `unverified` | 已开启但从未执行 |
| `ready` | 最近至少一个启用分支成功且没有未恢复的整体错误 |
| `partial` | 部分分支成功、部分失败；或公开收藏导入失败但发现可用 |
| `rate_limited` | 最近命中 429 且 cooldown 未结束 |
| `error` | 所有尝试分支失败 |

Bangumi 不产生 `login_required`。状态 detail 分开描述 discovery 和可选公开收藏，避免“用户名为空”被误报成来源不可用。

`GET /api/sources/credentials` 可列出 Bangumi 为“公开 API、无需凭据”；username 仍在 config 展示，不放进 credential value。

## 10. CLI smoke

新增命令：

```text
openbiliclaw fetch-bangumi --username <name> --limit 20
openbiliclaw discover-bangumi <keyword> --limit 20
openbiliclaw discover-bangumi-ranked --limit 20
openbiliclaw discover-bangumi-latest --limit 20
openbiliclaw discover --source bangumi --limit 20
```

约束：

- 所有 smoke 默认无 memory/profile 写入。
- 三个 `discover-bangumi*` smoke 固定只读：使用配置中的 `subject_types`，不写候选池、memory/profile，也不调用 LLM；需要入队时只走正式 `discover --source bangumi`，避免 API 探针产生隐式本地状态。
- 输出请求分支、subject type、返回/归一化/去重/入队计数和稳定失败原因。
- `fetch-bangumi --rebuild-profile` 必须显式；真实 LLM/embedding，不用 mock/Ollama 替换用户配置。
- 失败统一非零退出，并输出稳定、安全的 `not_found / rate_limited / schema_changed / network_error` 人话提示；合法空结果保持成功且计数为 0。

## 11. 推荐卡与用户界面

### 11.1 三端卡片

PC、移动 Web、扩展 side panel 都要满足：

- 平台 label/badge 为 Bangumi。
- `content_url` 打开 `https://bgm.tv/subject/<id>`；绝不构造 B 站 URL。
- 有封面走现有图片卡；无封面用 `body_text/title` text fallback，按钮区域不被摘要遮挡。
- 评分行仅在值有效时显示，例如 `评分 9.2 · 9,959 人 · #1`。
- engagement 行只显示真实 `⭐ 收藏总人数`；view/like/comment/share/danmaku 为 0 时不占位。
- 本地“收藏、稍后再看、不感兴趣、聊一聊” payload 保留 `source_platform/content_id/content_url/item_key`。
- 不展示或触发“同步到 Bangumi”之类站内写操作。
- 推荐平台过滤器、来源计数、已保存列表都识别 `bangumi`。

### 11.2 设置与初始化

- Desktop settings：开关、username、subject types、source modes、三个预算、interval、pool share、状态。
- Extension settings：与 desktop 同字段、同 round-trip。
- PC setup / desktop init drawer / extension guided init：Bangumi 选择项和 username 说明。
- Mobile Web 没有设置页，明确排除配置编辑；但推荐卡、来源过滤、打开链接和本地反馈必须支持。
- CLI 通过 `config-show` 覆盖第四表面。

### 11.3 图片链路

- 后端 `/api/image-proxy` 白名单加入精确后缀 `lain.bgm.tv`。
- 不把整个 `bgm.tv` 加入图片白名单，避免扩大 SSRF 面。
- 是否加入 `_DIRECT_FETCH_HOST_SUFFIXES` 由实现期真实网络 spike 决定；默认仅允许、遵循 outbound policy，不凭地域猜测 direct。
- 测试覆盖白名单、子域边界、redirect 仍需重新校验目标 host、无图 fallback。

### 11.4 移动 App 拉起

当前没有纳入本 spec 的可靠官方 URL scheme。`buildAppDeepLink()` 对 Bangumi 返回空串，`openContentUrl()` 走浏览器；不得自造 scheme。

## 12. 安全、隐私与可靠性

- 全链路只读；客户端类型层不暴露写方法。
- 不收集 Cookie、token、邮箱或当前用户私有信息。
- 公开收藏只有用户明确给 username 后才请求；不自动从 URL、浏览器或其他账号推断。
- username 进入 URL 前 path-encode；控制字符、斜杠、超长值保存时拒绝。
- 搜索 keyword 只进 JSON body，不拼接原始 query string。
- API 错误正文只截取安全摘要，避免整段 HTML/响应体进入日志和 UI。
- 429 遵守 cooldown；5xx/timeout 有界退避；不得每分钟热循环。
- Search schema 漂移与合法空结果分开，失败不伪装成“0 条成功”。
- `rating_score`、收藏计数、标签等结构化值在持久化前校验；非法值 WARNING 并回默认。
- no-NSFW 双层过滤；测试包含上游忽略 filter 仍返回 `nsfw=true` 的 fixture。
- User-Agent 固定可识别，不允许用户配置成浏览器伪装 UA。

## 13. 验收标准

### 13.1 自动化

- `BangumiClient`：真实 method/path/body/header、limit clamp、分页、timeout、400/404/429/5xx、schema drift、合法空结果。
- normalization：五类 subject、中文名 fallback、评分/排名/收藏、tag 去重、无图、NSFW、非法数字、稳定 identity。
- collection events：评分优先映射、五种 collection type、private skip、短评净化、`updated_at` 不作为事件时间。
- producer：三分支独立预算/cursor、跨 mode 去重与最终 limit 后扣账、source deficit、cooldown、partial success、keyword claim/used/failed/rollback、candidate enqueue；显式 CLI 不受 scheduler 总开关误伤。
- keyword generation：merged 和 inspiration axis 两条轨都能为 Bangumi 产词。
- source policy/platform family：aliases、`bgm.tv` / `bangumi.tv` URL 推断、share 开关与 stranded producer warning。
- config/API：load/save/config-show、GET/PUT、状态、share suggestion、旧配置回填。
- init：Bangumi-only + username 成功；Bangumi-only 无 username 早失败；混合来源无 username 不阻塞；smoke 默认零写入。
- candidate round-trip：`rating_score/rating_count/source_rank` 从原始候选到推荐 DTO 不丢。
- UI：PC/移动/扩展 label、URL、评分行、无图 fallback、本地动作 payload、设置 round-trip。
- image proxy：`lain.bgm.tv` 成功、近似恶意域名拒绝。

实现完成前至少运行：

```bash
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension && npm test && npm run typecheck && npm run build
```

### 13.2 真实 E2E

1. 使用合规 User-Agent 运行匿名 search/ranked/latest smoke。
2. 用用户明确提供或公开测试用户名跑 `fetch-bangumi`，确认默认不写 memory/profile。
3. 配置 Bangumi source + share，回读 `/api/config`，确认 runtime effective share 生效。
4. `openbiliclaw discover --source bangumi` 进入 `discovery_candidates`。
5. 使用本地真实 LLM/embedding 完成 eval/admission，抽查 DB 字段和推荐 DTO。
6. PC、移动、扩展分别验证有图/无图/长标题/评分/按钮区域。
7. 打开条目页、执行 OpenBiliClaw 本地收藏/稍后再看/不感兴趣/聊天；确认没有 Bangumi 账号写请求。
8. 人工模拟 429/schema drift，确认 cooldown/partial 状态和错误文案。

报告必须记录 API 分支、命令、候选/事件计数、最终状态、LLM provider/model 和未验证项。匿名公开 API 不需要也不得伪造“登录态 E2E”。

### 13.3 2026-07-17 验证报告

- 自动化：`ruff format --check src tests` 检查 447 个文件，Ruff lint 与 MyPy（203 个 source files）通过；Fable 两轮 review 修复后 Python 全量为 `5231 passed, 18 skipped`；扩展为 `1076 passed`，typecheck/build 通过；`git diff --check` 通过。自动加载边界另做 10 轮连续 Chromium 压测，10/10 通过。
- 官方 API smoke：隔离数据目录、匿名公开 API、合规 User-Agent。`discover-bangumi 科幻`、`discover-bangumi-ranked`、`discover-bangumi-latest` 各返回 5 条，三者本地写入与 LLM 调用均为 0；`fetch-bangumi --username sai --limit 5` 返回 5 条公开收藏事件（done 2、wish 3），memory/profile 均未写入。环境的 direct 模式超时后按项目配置改用 system proxy 成功，证明来源遵循统一 outbound policy。
- 正式 producer/eval：隔离临时配置和合成画像下，首次发现/入队/评估 6 条，缓存 3、拒绝 3，`search/ranked/latest` 各 2；带真实待用关键词“科幻”的第二轮发现/入队/评估/缓存均为 3，三个分支各 1，关键词状态为 `used`、`yield_count=1`。LLM 为 `openai_compatible/deepseek-v4-flash`，embedding 为 `ollama/bge-m3`；DB 抽查确认 `bangumi:<id>` identity、strategy/keyword、评分与排名字段进入 `content_cache`。
- 图片：真实 `lain.bgm.tv` 封面经 `/api/image-proxy` 首次 `miss`、二次 `hit`，两次均为 `200 image/jpeg`、41,906 bytes 且内容一致；白名单未扩大到整个 `bgm.tv`。
- 三端：真实 Chromium 分别检查 Desktop、移动 Web、extension side panel 的 Bangumi label、`bgm.tv` 链接、9.2 分、9,959 人评分、`#1` 排名；Desktop/extension 设置显示公开 API、username、三种 mode/type/budget。补充网络审计实际点击条目链接、本地收藏、稍后再看、聊天和不感兴趣，所有状态变更只命中本地 OpenBiliClaw API，没有请求 `api.bgm.tv` 或任何 Bangumi 写接口。
- 故障注入：自动化覆盖 429 + `Retry-After` cooldown、search schema drift + 其他分支 partial success、timeout/5xx 有界一次重试、图片失败 fallback、用户 404/empty/private skip。
- Fable review：首轮指出的显式 CLI/scheduler、最终预算扣账、状态读取与空 keyword 生命周期均已修复；复核进一步发现并验证官方 browse 超界 offset 实际返回 HTTP 400，现已加入 cursor 归零有界重试，同时修复显式空 username、429 keyword rollback、存量 prompt 零值字段和 popup 输入保留。`OPENBILICLAW_NO_*` 优先于 `--yes-*` 为现有测试固化的“永久跳过”契约，未按误报改动；与 Bangumi 无关的既有 desktop UX diff 保持不碰。
- 未验证项：没有使用私人 Bangumi 账号执行画像重建，也没有 OAuth/私有收藏/站内写回（均属明确 out of scope）；未执行 commit、push、PR、安装包或发布验证。公开收藏信号强度仍需在首个真实用户初始化后按 §8.2 复校。

## 14. 文档与发布影响

实现 PR 按范围同步：

- `docs/modules/discovery.md`
- `docs/modules/runtime.md`
- `docs/modules/config.md`
- `docs/modules/cli.md`
- `docs/modules/init.md`
- `docs/modules/extension.md`（设置/推荐卡；明确无 Bangumi host permission）
- `docs/changelog.md`
- `docs/architecture.md`、`docs/spec.md` 架构图
- `README.md`、`README_EN.md` 来源列表与架构图
- `docs/index.md`、`docs/index.html` 来源卡、SEO/版本信息
- `config.example.toml`

不新增默认依赖，不需要安装器/Docker 依赖补丁；release 仍需验证 backend/extension/desktop 版本、插件包、聚合 release 和 marketplace 文案中的来源数量一致。

## 15. 实现期 Spike 结论

2026-07-17 使用合规项目 User-Agent 对官方匿名 API 和公开测试数据完成 spike，结论如下：

1. `lain.bgm.tv` 封面在现有 outbound policy 下可稳定读取。实现只把精确后缀加入图片白名单，不加入 direct-fetch 后缀，也不扩大到整个 `bgm.tv`；redirect 仍逐跳复验。
2. `sort=date` 可分页返回合法条目，但会包含未来日期或未播条目，因此内部 mode 保留 `latest`，所有用户可见文案固定为“按日期浏览（可能含未播条目）”，不宣称实时或严格新近。
3. 匿名公开收藏端点只返回公开可读行，五种收藏状态与评分信号能按 §8.2 的既定矩阵归一化；首批 smoke 未发现需要修改强度分界的证据。`updated_at` 只保留诊断字段，不作为事件时间或增量游标。

以上 spike 没有扩大范围：仍无 OAuth、Cookie、私有收藏、related N+1 或任何 Bangumi 写操作。
