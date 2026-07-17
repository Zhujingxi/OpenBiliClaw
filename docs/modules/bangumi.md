# Bangumi 来源

> Bangumi 是匿名只读的正式内容来源。实现使用官方 `https://api.bgm.tv/v0`，不读取 Cookie、不申请 OAuth，也不提供任何站内写操作。

## 已实现功能

| 能力 | 状态 | 说明 |
|------|------|------|
| Subject 搜索 | ✅ | `POST /v0/search/subjects`；统一关键词 planner 产出的 Bangumi 查询由 producer claim 后抓取 |
| 排名浏览 | ✅ | `GET /v0/subjects?sort=rank`；按配置的 subject type 轮转 |
| 日期浏览 | ✅ | `GET /v0/subjects?sort=date`；用户界面明确标为“按日期浏览（可能含未播条目）”，不宣称实时最新 |
| 公开收藏初始化 | ✅ | 仅在用户显式提供公开用户名后读取 `GET /v0/users/{username}/collections`；可作为唯一画像来源 |
| 统一候选池 | ✅ | Subject 归一化后只进入 `discovery_candidates`，由共享 evaluator/admission 决定是否进入 `content_cache` |
| 目录指标 | ✅ | `rating_score`、`rating_count`、`source_rank` 与真实 `favorite_count` 贯穿候选池、推荐/惊喜 API 和三端卡片 |
| 设置与状态 | ✅ | 桌面 Web、扩展设置页和 `/api/sources/status` 支持开关、用户名、分支、类型、预算、节流和 pool share |
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

`BangumiDiscoveryProducer` 是 fetch-only producer。它维护 search/ranked/latest 各自的 UTC 日预算、cursor、最小调度间隔和持久化 cooldown；遇到 `429` 时遵循合法 `Retry-After`，停止本轮剩余请求，并让后续 runtime tick 继续恢复。429 属于上游暂时限流，当前及尚未执行的关键词会 rollback，不会被误记 zero-yield；若前面的关键词已经产出候选，这些候选仍以 `partial` 结果入队并计入实际预算。官方 browse API 会在持久化 offset 超出缩小后的 total 时返回 400；producer 对非零 cursor 的首次 `invalid_request` 会先持久化归零、再仅重试一次，避免 ranked/latest 永久卡死。ranked/latest 还会为每个 mode 持久化 subject-type 起始位：即使单轮 `limit` 小于类型数，后续轮次也会依次覆盖 book/game/music/real，不会长期只抓配置列表第一项。预算只按跨分支去重并应用最终 `limit` 后仍保留的候选扣减，分支召回的重复或被截断条目只进入 `discovered` 诊断、不消耗额度。空搜索结果和领取后规范化为空的关键词会标为 zero-yield/failed，而不是伪装成已使用或反复 rollback。

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
```

客户端边界固定如下：

- Base URL 固定为 `https://api.bgm.tv`，timeout 为 15 秒，单页 `limit` 收敛到 `1..50`。
- 每次请求携带可识别的项目 User-Agent；网络代理复用统一 `[network]` outbound policy。
- `400/404/429/5xx`、网络失败和 schema drift 转为带稳定 `code` 的 `BangumiAPIError`。
- 搜索虽然使用 POST，但只是官方只读查询；客户端没有修改收藏的 POST/PATCH 方法。

### 归一化

```python
from openbiliclaw.sources.bangumi import bangumi_subject_to_content

content = bangumi_subject_to_content(row, strategy="bangumi-ranked")
```

Subject identity 使用 `bangumi:<decimal subject id>`，页面 URL 为 `https://bgm.tv/subject/<id>`，`content_type="subject"`。首选中文名、再回落原名；封面按官方 image variants 回落；`nsfw=true` 始终丢弃。评分不会冒充点赞或评论：只有官方收藏人数进入 `favorite_count`，其它缺失 engagement 字段保持 0。

公开收藏通过 `bangumi_collection_to_event()` 转成统一事件：想看/看过/在看/搁置/抛弃使用不同信号强度，私有行丢弃，短评清洗后截断；官方 `updated_at` 不被当作可靠收藏时间或增量 cursor。

## 配置与状态

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

`GET /api/sources/status` 对 Bangumi 只读本地配置和 producer ledger，不会在打开设置页时访问上游。常见状态为：未启用 `disabled`、已启用但尚无真实运行 `unverified`、最近成功 `ready`、部分分支失败 `partial`、限流冷却 `rate_limited`、最近运行错误 `error`。凭据说明固定为公开 API、无需 Cookie/token。

桌面 Web 与扩展设置页都列出官方五种合法条目类型：动画、书籍、游戏、音乐和三次元；默认仍只勾选 `anime/book/game`。保存已有 `music/real` 配置时不会因界面缺少控件而静默丢失。

## CLI

```bash
# 公开收藏 smoke：默认不写 memory、不调用 LLM
openbiliclaw fetch-bangumi --username sai --limit 20

# 三个只读发现 smoke
openbiliclaw discover-bangumi "攻壳机动队" --limit 10
openbiliclaw discover-bangumi-ranked --limit 10
openbiliclaw discover-bangumi-latest --limit 10

# 正式 producer：写 discovery_candidates，交给共享 evaluator
openbiliclaw discover --source bangumi --limit 30
```

`fetch-bangumi --write-memory` 才会写本地事件；`--rebuild-profile` 还会真实调用配置中的 LLM，并隐含要求写 memory。guided init 若只选择 Bangumi，则必须提供公开用户名；若与其它画像来源混用而用户名为空，Bangumi 仅参与后续 discovery，并返回明确 warning。`source_options.bangumi.username` 显式出现时以本轮值为准，包括空字符串；只有字段缺失的旧客户端才回退已保存用户名。显式 `discover --source bangumi` 仍要求来源自身启用，但不受后台 `[scheduler].enabled` 总开关限制；该总开关只控制 daemon-owned 调度。

## 安全边界

- 不读取 Bangumi Cookie、浏览器登录态或 token，不新增扩展 host permission。
- 不调用收藏/评分/进度写接口；真实 smoke 只允许公开读取和本地写入。
- 封面只允许现有图片代理白名单中的 `lain.bgm.tv`，页面链接只构造 `bgm.tv/subject/<id>`。
- API 状态页纯本地；只有显式 CLI/init/discover 或启用后的后台 producer 会访问 Bangumi。统一状态 DTO 的 legacy `logged_in` 对 Bangumi 表示本地 discovery 是否已有 ready 结果，不表示存在账号会话；未运行时为 `unverified/false`。

## 测试

- `tests/test_bangumi_client.py`：请求契约、节流、分页、错误与 schema drift。
- `tests/test_bangumi_source.py`：subject/collection 归一化、NSFW、指标和信号矩阵。
- `tests/test_bangumi_producer.py`：预算、cursor、cooldown、关键词生命周期和本地状态。
- `tests/test_bangumi_web_surfaces.py` 与扩展 Node tests：设置、身份、URL、目录指标和 guided init 契约。

完整设计与验收边界见 [Bangumi 来源 Spec](../plans/2026-07-17-bangumi-source-spec.md)。
