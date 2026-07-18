# Bangumi Source Implementation Plan

> Spec：[`2026-07-17-bangumi-source-spec.md`](2026-07-17-bangumi-source-spec.md)
>
> 目标：按 spec 完成只读官方 API 直连、公开收藏初始化、统一 discover、三端推荐卡、设置/初始化入口、测试、真实 E2E 与文档。每个任务先写失败测试，再做最小实现；不要把 OAuth、私有收藏或站内写操作混入本计划。

> 实施状态（2026-07-17）：Task 0–11 全部完成并验证。三个 `discover-bangumi*` API 探针固定为零写入，正式入队统一由 `discover --source bangumi` 承担；分支尚未提交、推送或发布。

## 架构摘要

```text
Bangumi official v0 API
  ├─ public subject search/browse → BangumiDiscoveryProducer
  │    └─ discovery_candidates → shared eval/admission → content_cache
  └─ public user collections (explicit username)
       └─ unified events → guided init/profile

Config/API/status/CLI
  └─ Desktop settings + PC setup + Mobile cards + Extension settings/cards
```

## 锁定决策

- 内部 key 一律 `bangumi`；显示名 `Bangumi`。
- 首版只有 `search / ranked / latest`，不做 related。
- 只调用官方匿名只读 API；不收 Cookie/token，不实现 OAuth。
- `nsfw=false` 请求 + normalization 二次过滤，首版没有 NSFW 开关。
- 默认类型 `anime/book/game`；桌面 Web 与扩展设置页也提供可选的 `music/real`。
- `rating_score / rating_count / source_rank` 是正式通用字段，不能塞进 `raw_payload` 后丢失，也不能冒充 like/comment。
- `favorite_count` 只表示 Bangumi 收藏总人数；其他 engagement 缺失项保持 0。
- 公开收藏只有显式 username 才取；`updated_at` 不作事件时间/增量 cursor。
- producer fetch-only，候选必须经过共享 admission policy 和真实本地 LLM eval。
- 不新增 extension host permission/content script/task endpoint。

---

## Task 0：实现前 API / 图片 Spike

**目的：** 关闭 spec §15 的外部行为不确定性，不写生产代码。

**检查：**

1. 用合规 User-Agent 对五种 SubjectType 分别请求：

   ```bash
   curl -H 'User-Agent: whiteguo233/OpenBiliClaw/<version> (https://github.com/whiteguo233/OpenBiliClaw)' \
     'https://api.bgm.tv/v0/subjects?type=2&sort=date&limit=5&offset=0'
   ```

2. 记录 `sort=date` 是否稳定降序、是否含未来条目、空页与 total 行为；只裁决 UI 文案，不改变 `latest` 范围。
3. 通过现有 `/api/image-proxy` 测一张 `https://lain.bgm.tv/...` 封面：
   - outbound policy 下是否稳定；
   - system/custom proxy 是否需要 direct bypass；
   - redirect 目标域名。
4. 对公开测试用户名读取 1 页 collections，确认无 token 时 private 行不出现。
5. 把日期、API commit、响应服务版本和结论回填 spec §15；不提交原始个人收藏响应、header 中潜在标识或临时截图。

**验收：** spike 结果只改变已留出的网络/文案裁决；不得以 spike 为理由增加 OAuth、Cookie、related 或写操作。

---

## Task 1：来源身份与目录评分字段全链路

**Files：**

- Modify: `src/openbiliclaw/sources/platforms.py`
- Modify: `src/openbiliclaw/sources/event_format.py`
- Modify: `src/openbiliclaw/discovery/engine.py`
- Modify: `src/openbiliclaw/discovery/candidate_pool.py`
- Modify: `src/openbiliclaw/storage/database.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`（recommendation/delight projection）
- Test: `tests/test_source_platforms.py`
- Test: `tests/test_event_format.py`
- Test: `tests/test_discovery_candidate_store.py`
- Test: `tests/test_storage.py`
- Test: `tests/test_api_app.py`

### Step 1：先写 round-trip 失败测试

构造：

```python
item = DiscoveredContent(
    bvid="326",
    content_id="326",
    content_url="https://bgm.tv/subject/326",
    source_platform="bangumi",
    source_strategy="bangumi-ranked",
    content_type="subject",
    title="攻壳机动队 S.A.C. 2nd GIG",
    body_text="条目简介",
    rating_score=9.2,
    rating_count=9959,
    source_rank=1,
    favorite_count=26203,
)
```

断言：

- `item_key == "bangumi:326"`；
- `infer_source_platform_from_url("https://bgm.tv/subject/326") == "bangumi"`；
- candidate enqueue/claim/row conversion 不丢三个 rating 字段；
- admission/cache/recommendation DTO 不丢字段；
- `content_type == "subject"`，不能回落成 video；
- B 站/知乎/Reddit 现有字段和值保持不变。

### Step 2：注册来源身份

- `PLATFORM_BANGUMI = "bangumi"`。
- `SourceFamilyRule` aliases 至少 `bangumi/bgm`，URL host 仅 `bgm.tv`。
- 加入 `CANONICAL_SOURCE_FAMILIES`、engine canonical storage key 集合和 candidate canonicalizer。
- `SOURCE_BANGUMI` + `_PLATFORM_LABELS[bangumi] = "Bangumi"`；不改全局 satisfaction 集合。

### Step 3：新增字段

给 `DiscoveredContent` 和 `DiscoveryCandidateWrite` 增加：

```python
rating_score: float = 0.0
rating_count: int = 0
source_rank: int = 0
```

贯穿：

- `to_cache_kwargs()`；
- candidate conversion / row conversion；
- fresh `content_cache` / `discovery_candidates` schema；
- 既有 `_ensure_*_columns()` 迁移 helper；
- enqueue/claim/evaluate/admit/cache/select；
- `RecommendationOut`、`PendingDelightOut` 和 API projection。

数值约束：评分有限数且 clamp `0..10`；count/rank 非负。非法 provider/upstream 值落默认并 WARNING。

### Step 4：prompt 可见性

- `_prompt_visible_content_fields()` 输出 rating 三字段；`rating_score` 保持 float，不经现有 int cast。
- evaluation 静态 system prompt 增加“目录评分/排名只能作供给质量辅助，不能覆盖画像匹配”的规则。
- prompt system byte-invariance 测试必须保持通过；动态值只在 user message。

### Step 5：focused tests

```bash
.venv/bin/pytest -q \
  tests/test_source_platforms.py \
  tests/test_event_format.py \
  tests/test_discovery_candidate_store.py \
  tests/test_storage.py \
  tests/test_api_app.py
```

**建议 commit：** `feat: add bangumi source identity and catalog metrics`

---

## Task 2：官方 API Client、错误模型与归一化

**Files：**

- Create: `src/openbiliclaw/sources/bangumi_client.py`
- Create: `src/openbiliclaw/sources/bangumi.py`
- Create: `tests/test_bangumi_client.py`
- Create: `tests/test_bangumi_source.py`
- Create: `tests/fixtures/bangumi/search_subjects.json`
- Create: `tests/fixtures/bangumi/browse_subjects.json`
- Create: `tests/fixtures/bangumi/user_collections.json`
- Create: `tests/fixtures/bangumi/nsfw_subject.json`

### Step 1：client contract tests

用 `httpx.MockTransport` 锁定：

- base URL、method、path、query/body；
- User-Agent 精确包含开发者 ID、应用名、版本和项目主页；
- `Accept`/`Content-Type`；
- SubjectType string→int 映射；
- `limit` clamp `1..50`、offset 非负；
- timeout、400、404、429+Retry-After、500；
- 200 合法 empty 与 schema drift 的不同错误；
- injected client 的 ownership/close 行为。

定义稳定异常/结果，例如：

```python
class BangumiAPIError(RuntimeError):
    code: str
    status_code: int | None
    retry_after_seconds: int | None
```

不把完整上游 HTML/JSON body写进异常字符串；仅保留安全、长度受限摘要。

### Step 2：实现 client

- `httpx.AsyncClient(timeout=15, **outbound_httpx_kwargs())`。
- base URL 固定，不接受 config override。
- client 只暴露 spec 三个读方法；不定义 post/patch collection write helper。
- `_request_json()` 做统一 header、节流、状态码和 schema guard。
- 不缓存失败/空响应。

### Step 3：normalizer tests

覆盖：

- 中文名优先与原名 fallback；
- 五种 subject type；
- `images.common→medium→large→grid→small`；
- `rating_score/count/source_rank`；
- full Subject collection sum 与 SlimSubject `collection_total`；
- tags/meta_tags 去重、排序、上限；
- 合法 date、非法 date；
- `nsfw=true` 双层漏网仍 drop；
- malformed id/type/numbers；
- `content_url/item_key/content_type/body_text`。

fixture 只保留测试必要字段，顶部注明来自官方 schema/真实匿名响应的裁剪版本与采样日期；不提交公开用户完整收藏历史或无关短评。

### Step 4：实现 normalizer

- `bangumi_subject_to_content()` 返回 `None` 表示明确丢弃。
- `author_name/up_name` 保持空，不用类型/平台伪装作者。
- `description=""`、`body_text=summary/short_summary`，避免 prompt 重复。
- `score_threshold=0.0`。
- 返回 normalization telemetry（accepted/malformed/nsfw/duplicate），供 CLI/producer 输出。

### Step 5：focused tests

```bash
.venv/bin/pytest -q tests/test_bangumi_client.py tests/test_bangumi_source.py
```

**建议 commit：** `feat: add read-only bangumi api client and normalizer`

---

## Task 3：配置、来源 policy、API round-trip 与本地状态

**Files：**

- Modify: `src/openbiliclaw/config.py`
- Modify: `config.example.toml`
- Modify: `src/openbiliclaw/runtime/source_policy.py`
- Modify: `src/openbiliclaw/api/models.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`（platform order 常量）
- Test: `tests/test_config.py`
- Test: `tests/test_source_policy.py`
- Test: `tests/test_api_app.py`
- Create: `tests/test_api_bangumi.py`（若拆分能减少 `test_api_app.py` 继续膨胀）

### Step 1：配置失败测试

覆盖：

- 缺段默认值；
- 全字段 load→save→load；
- `config-show` 文本；
- invalid `subject_types/source_modes/username/budget/interval`；
- unknown mode/type 保存时 400，且磁盘不变；
- `enabled=false` 保留用户值但 effective share 移除；
- share suggestion/order 包含 Bangumi。

### Step 2：实现 `BangumiSourceConfig`

按 spec 字段和默认值实现；parser、serializer、`SourcesConfig`、默认 pool shares、normalizer 的所有硬编码枚举都加入 Bangumi。

username 校验：strip 后允许空；非空时拒绝 `/`、控制字符和超过 128 code points。不要用 ASCII-only regex 拒绝潜在合法 Unicode 用户名。

### Step 3：API config models/handlers

- `BangumiSourceConfigOut`。
- `SourcesConfigOut.bangumi`。
- GET 投影全部字段。
- PUT whitelist、枚举/数值校验、transactional save、runtime hot rebuild。
- 不接受 `base_url/token/nsfw` 等 spec 外字段。

### Step 4：本地状态

先建立可被 producer 使用的 run-state contract（DAO 可放 `bangumi.py` 或独立 `bangumi_state.py`）：

```text
mode / status / error_code / discovered / enqueued / units_used
started_at / completed_at / cooldown_until / cursor
```

`GET /api/sources/status` 只查 config + ledger，按 spec 输出 disabled/unverified/ready/partial/rate_limited/error；不在 status GET 里访问 `api.bgm.tv`。

`GET /api/sources/credentials` 加 Bangumi “公开 API、无需凭据”，不把 username 当 credential。

### Step 5：focused tests

```bash
.venv/bin/pytest -q tests/test_config.py tests/test_source_policy.py tests/test_api_app.py tests/test_api_bangumi.py
```

**建议 commit：** `feat: add bangumi source configuration and status`

---

## Task 4：统一关键词双轨与 inspiration grounding

**Files：**

- Modify: `src/openbiliclaw/runtime/keyword_fetch.py`
- Modify: `src/openbiliclaw/runtime/keyword_planner.py`
- Modify: `src/openbiliclaw/runtime/inspiration_pipeline.py`
- Modify: `src/openbiliclaw/llm/prompts.py`
- Modify: `src/openbiliclaw/discovery/inspiration_provider.py`
- Test: `tests/test_keyword_fetch.py`
- Test: `tests/test_keyword_planner.py`
- Test: `tests/test_llm_prompts.py`
- Test: `tests/test_inspiration_pipeline.py`
- Test: `tests/test_inspiration_provider.py`

### Step 1：generation 失败测试

必须分别证明：

1. merged planner 的 Bangumi deficit 会触发一次 merged LLM call，并持久化 `platform="bangumi"` keywords；
2. Bangumi 缺词时 platform 不能被静默 drop；
3. inspiration axis allocation target 可包含 Bangumi，并产出 Bangumi keyword；
4. planner schema/允许 key 包含 Bangumi，未知 key 仍拒绝；
5. static system prompt 保持 call-invariant。

### Step 2：query style 与 supply advantage

加入：

```text
题材 / IP / 原作 / 作者 / 监督 / 制作公司 / 游戏平台 / 类型
```

示例使用作品发现口吻；avoid markers 包括“爆款、热议、速看、探店”等社媒词。prompt 文案不得要求 Bangumi 返回帖子/视频。

### Step 3：fetch coordinator

- export/import `PLATFORM_BANGUMI`；
- `claim("bangumi")` 生命周期复用 fetch-only 语义；
- empty→failed，预算未执行→rollback，成功 enqueue→used。

### Step 4：grounding backend

在 `build_platform_source_backends()` 中仅当 Bangumi enabled 时注册只读搜索 backend，复用 `BangumiClient.search_subjects()`，输出标题/摘要/标签/url 的小型 grounding rows。

- 不创建第二套 client config；
- 遵守同一节流/cooldown；
- backend 失败只影响 inspiration grounding，不把正式 producer 标成 ready。

### Step 5：focused tests

```bash
.venv/bin/pytest -q \
  tests/test_keyword_fetch.py \
  tests/test_keyword_planner.py \
  tests/test_llm_prompts.py \
  tests/test_inspiration_pipeline.py \
  tests/test_inspiration_provider.py
```

**建议 commit：** `feat: add bangumi to unified keyword generation`

---

## Task 5：正式 Producer、预算、cursor 与 runtime 调度

**Files：**

- Create: `src/openbiliclaw/runtime/bangumi_producer.py`
- Modify: `src/openbiliclaw/api/runtime_context.py`
- Modify: `src/openbiliclaw/runtime/refresh.py`
- Test: `tests/test_bangumi_producer.py`
- Test: `tests/test_runtime_context.py`
- Test: refresh/controller 现有对应测试文件

### Step 1：producer 失败测试

覆盖：

- disabled/throttled/pool_full/no_profile/source_disabled；
- source deficit 决定 per-run limit；
- 三分支独立 budget 与 ledger，跨 mode 去重及最终 limit 后按保留候选的 strategy 扣账；
- selected subject types 公平 round-robin；
- ranked/latest 独立 cursor、成功推进、total wrap、超界 400 归零有界重试、其它失败不推进；
- search keyword claim/fallback/used/failed/rollback，空白 claim 必须 failed、不能无限 rollback；
- cross-mode subject ID dedupe；
- `nsfw/malformed` 计数；
- 一个分支 schema drift、其他成功 → partial；
- 429 落 cooldown、rollback 在途关键词，cooldown 内零请求；
- enqueue 按 `source_context=bangumi-*`，不直接 cache/eval；
- candidate pipeline 拒绝/池满有明确 reason。

### Step 2：实现 producer

结构参考 `YoutubeDiscoveryProducer` 的 fetch-only 边界：

- `produce_if_due(limit=...)`；
- `remaining_budgets()` / `consumed_today()` / `record_strategy_run()`；
- search 一次请求可 filter 多种 SubjectType；
- ranked/latest 按类型分配；
- 所有内容经 Task 2 normalizer；
- `source_keyword_id` 只绑定 search；
- 返回 `{reason, discovered, enqueued, source_counts, malformed, nsfw_filtered}`。

### Step 3：runtime 装配

- `RuntimeContext.rebuild_from_config()` 构建一个 shared BangumiClient 和 producer；disabled 路径不发网络请求。
- `ContinuousRefreshController` 增加 `bangumi_producer`、loop、tick、deficit 和 stranded share 检查。
- `run_forever()` task tree/docstring 同步。
- hot reload 正确替换/关闭旧 client，防资源泄漏。

### Step 4：admission 回归

测试候选 `score_threshold=0` 仍受 `discovery.admission.effective_admission_threshold()` 的全局 floor；exact explore 特例不适用于 Bangumi。

### Step 5：focused tests

```bash
.venv/bin/pytest -q tests/test_bangumi_producer.py tests/test_runtime_context.py -k 'bangumi or source_share or producer'
```

**建议 commit：** `feat: feed bangumi subjects into the discovery pool`

---

## Task 6：CLI discovery smoke 与正式 `discover --source`

**Files：**

- Modify: `src/openbiliclaw/cli.py`
- Test: `tests/test_cli.py`

### Step 1：失败测试

锁定以下命令：

```text
discover-bangumi <keyword>
discover-bangumi-ranked
discover-bangumi-latest
discover --source bangumi
```

断言：

- method/query/type/limit 正确；
- 默认不写 memory、不重建 profile；
- 三个 smoke 固定零写入，正式 producer 才 enqueue；
- 输出召回/归一化计数、前 5 条预览和明确的本地写入/LLM 调用计数；
- schema_changed/rate_limited/network 的非零退出和人话提示稳定，合法 empty 为成功；
- `discover --source bangumi` 调正式 producer，不打印“请改跑 smoke”。

### Step 2：实现命令

- smoke 使用 config `subject_types`，可覆盖单次 limit，但不写回配置。
- ranked/latest 使用显式 mode，不把 ranked 显示成 hot。
- 输出前 5 条标题、类型、评分、URL；不打印整段 summary/上游响应。

### Step 3：focused tests

```bash
.venv/bin/pytest -q tests/test_cli.py -k bangumi
```

**建议 commit：** `feat(cli): add bangumi discovery smoke commands`

---

## Task 7：公开收藏事件、fetch smoke 与 Guided Init

**Files：**

- Modify: `src/openbiliclaw/sources/bangumi.py`
- Modify: `src/openbiliclaw/cli.py`
- Modify: `src/openbiliclaw/api/app.py`
- Modify: `src/openbiliclaw/runtime/init_prereqs.py`（如启用来源枚举在此硬编码）
- Test: `tests/test_bangumi_source.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_web_guided_init.py`
- Test: `tests/test_api_app.py`

### Step 1：event mapping 失败测试

覆盖 spec §8.2 全矩阵：

- rate 8/10 → like；rate 1/4 → feedback dislike；5/7 走 collection type；
- wish/doing/done/on_hold/dropped；
- 评分与状态冲突时评分优先；
- `private=true` skip；
- username/subject stable metadata；
- collection comment 控制字符剥离 + 200 字上限；
- `updated_at` 只进 `source_updated_at`，不作为 event timestamp；
- malformed/empty row drop；
- signal strength 精确且有校准注释。

### Step 2：`fetch-bangumi`

实现：

```text
fetch-bangumi --username <name> --limit 20
              [--write-memory] [--rebuild-profile]
```

- username option > config username；空值明确报错。
- 默认只读+只打印，memory/profile 调用为 0。
- `--rebuild-profile` 隐含 write，使用真实 configured provider。
- 五种 collection type 公平取数，API page limit 50。
- user 404、empty、partial page failure 可区分。

### Step 3：shared init pipeline

- `run_guided_init(..., include_bangumi: bool)`。
- `InitResult` 增加 Bangumi events/counts/status；stage-1 source total/progress 包含它。
- 直连 fetch 受 stage-1 global deadline 和单源 timeout 约束。
- events 加入统一 persist/analyze/profile history；source event count/share suggestion 包含 Bangumi。
- 只选择 Bangumi + username 时可成功；真实 0 条则清晰 `empty_signals`。

### Step 4：CLI flags 与 API start payload

CLI：

```text
--yes-bangumi / --no-bangumi / --bangumi-username
```

API 请求扩展为：

```json
{
  "sources": ["bangumi"],
  "source_options": {"bangumi": {"username": "sai"}}
}
```

后端只 whitelist `source_options.bangumi.username`，使用与 config PUT 同一 validator，并在 run reservation 前持久化；未知 source option 拒绝 400，不能静默忽略。

早期门禁：

- explicit sources 只有 Bangumi 且 effective username 空 → 409 `no_profile_signal_sources`；
- Bangumi + 其他画像来源且 username 空 → 允许，warning 为 discovery-only；
- API status detail 不把 Bangumi 说成需要浏览器登录。

### Step 5：focused tests

```bash
.venv/bin/pytest -q \
  tests/test_bangumi_source.py \
  tests/test_cli.py -k bangumi \
  tests/test_web_guided_init.py \
  tests/test_api_app.py -k 'bangumi or guided_init'
```

**建议 commit：** `feat: import public bangumi collections during init`

---

## Task 8：Desktop / Extension 设置与三处 Guided Init

**Files：**

- Modify: `src/openbiliclaw/web/desktop/index.html`
- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: desktop CSS（按现有位置）
- Modify: `src/openbiliclaw/web/setup/index.html`
- Modify: `extension/popup/popup.html`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup-init-control.js`
- Create: `tests/test_desktop_web_bangumi_settings.py`
- Test: `tests/test_web_guided_init.py`
- Test: `extension/tests/popup-settings.test.ts`
- Test: `extension/tests/init-control.test.ts`

### Step 1：设置 round-trip 失败测试

PC 与 extension 都覆盖：

- enabled、username；
- five subject type checkboxes；
- search/ranked/latest modes；
- three budgets、request/min interval、bootstrap limit；
- pool share；
- source status disabled/unverified/ready/partial/rate_limited/error；
- load→edit→save→reload 不丢字段；
- disabled 保留值。

### Step 2：实现设置卡

- 文案明确“官方公开 API，无需 Cookie/插件登录”。
- username 旁标“只用于读取该账号公开收藏；留空只做发现”。
- 不添加 credential textarea/token 字段。
- source share suggestion payload 的 enabled/configured shares 加 Bangumi。
- source status/credential key arrays 加 Bangumi。

### Step 3：实现 Guided Init UI

三个入口都加 Bangumi option + username input：

- `src/openbiliclaw/web/setup/index.html`
- desktop init drawer
- extension guided init

发送 Task 7 的 `source_options`。把通用“所有来源都需浏览器登录”提示拆成：

- 插件登录态来源提示；
- Bangumi “无需登录/用户名决定是否有画像信号”提示。

client-side 预判 Bangumi-only + empty username，后端仍作权威校验。

### Step 4：无扩展权限回归

测试/检查：

- `extension/manifest.json`、Firefox manifest 不新增 `bgm.tv` host permission；
- service worker 不新增 Bangumi dispatcher/cookie sync；
- popup 只访问本地 authenticated shared API client。

### Step 5：focused tests

```bash
.venv/bin/pytest -q tests/test_web_guided_init.py tests/test_desktop_web_bangumi_settings.py
cd extension && node --test --experimental-strip-types tests/popup-settings.test.ts tests/init-control.test.ts
```

**建议 commit：** `feat(ui): configure bangumi source across setup surfaces`

---

## Task 9：三端推荐卡、评分展示、图片代理与本地动作

**Files：**

- Modify: `src/openbiliclaw/web/desktop/assets/js/app.js`
- Modify: desktop CSS
- Modify: `src/openbiliclaw/web/js/view-models.js`
- Modify: `src/openbiliclaw/web/js/views/recommend.js`
- Modify: `src/openbiliclaw/web/js/views/saved.js`
- Modify: `src/openbiliclaw/web/js/app-launch.js`
- Modify: `extension/popup/popup-helpers.js`
- Modify: `extension/popup/popup.js`
- Modify: `extension/popup/popup.html`
- Modify: `src/openbiliclaw/runtime/image_cache.py`
- Test: `tests/test_mobile_web_view_models.py`
- Create: `tests/test_desktop_web_bangumi_cards.py`
- Test: `tests/test_image_cache.py`
- Test: `extension/tests/popup-helpers.test.ts`

### Step 1：卡片失败测试

统一 fixture：

```json
{
  "content_id": "326",
  "content_url": "https://bgm.tv/subject/326",
  "source_platform": "bangumi",
  "content_type": "subject",
  "rating_score": 9.2,
  "rating_count": 9959,
  "source_rank": 1,
  "favorite_count": 26203
}
```

断言三端：

- label/source badge；
- explicit URL 和 fallback URL；
- 评分 `9.2 / 9,959 / #1`；无值不占位；
- 收藏总人数显示，结构性缺失 engagement 不显示 0；
- 无图 text fallback；
- click/favorite/watch-later/dislike/chat payload identity 完整；
- platform filter/saved list 识别 Bangumi；
- 不生成 native-save mutation/deep link。

### Step 2：实现评分 helper

三个前端各用小型 helper 生成 catalog stats；保持现有 recommendation engagement helper 不被 Bangumi 特例污染。非 Bangumi 如字段为 0，DOM 不变化。

### Step 3：图片代理

- `ALLOWED_IMAGE_HOST_SUFFIXES += ("lain.bgm.tv",)`；
- direct suffix 按 Task 0 结论；
- 测试 `lain.bgm.tv`、子域边界、`lain.bgm.tv.evil.example`、redirect 目标；
- 不把 `bgm.tv` 整域加入图片白名单。

### Step 4：移动端行为

- `buildContentUrl()` 对 Bangumi fallback 到 `/subject/<id>`；
- `buildAppDeepLink()` 返回空，浏览器 fallback；
- 本地操作 API 正常；native saved-sync 对 Bangumi 保持 unsupported/不调度，UI 不误报已同步平台。

### Step 5：focused tests

```bash
.venv/bin/pytest -q \
  tests/test_mobile_web_view_models.py \
  tests/test_image_cache.py \
  tests/test_desktop_web_bangumi_cards.py
cd extension && node --test --experimental-strip-types tests/popup-helpers.test.ts
```

**建议 commit：** `feat(ui): render bangumi recommendations across clients`

---

## Task 10：模块文档、架构图与产品入口

**Files（按实际改动逐项更新）：**

- Modify: `docs/modules/discovery.md`
- Modify: `docs/modules/runtime.md`
- Modify: `docs/modules/config.md`
- Modify: `docs/modules/cli.md`
- Modify: `docs/modules/init.md`
- Modify: `docs/modules/extension.md`
- Modify: `docs/changelog.md`
- Modify: `docs/architecture.md`
- Modify: `docs/spec.md`
- Modify: `README.md`
- Modify: `README_EN.md`
- Modify: `docs/index.md`
- Modify: `docs/index.html`

### 文档要求

- 来源列表、架构图、README CN/EN、首页 source card 一致。
- 明确 Bangumi 是官方公开 API 直连，不要求扩展登录，不支持站内写回。
- CLI 文档列出五个命令及默认无写入语义。
- config 文档列出所有字段、enum、默认、budget 单位和 username 隐私边界。
- extension 文档说明只改设置/卡片，没有 host permission/content script。
- changelog 放入新的未发布/新版本 block；不得修改已发布 tag 的旧版本语义。
- release highlight 只有正式发布时才替换，CN/EN 同步且不超过 4 条。
- 本 spec/plan 从 Draft 更新为 Implemented/Verified 只在真实验收完成后。

**建议 commit：** `docs: document bangumi source integration`

---

## Task 11：完整验证与真实只读 E2E

### 11.1 静态与单元测试

```bash
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/mypy src
.venv/bin/pytest -q --tb=short
cd extension
npm test
npm run typecheck
npm run build
```

如果 `ruff format --check` 命中历史无关文件，不顺手全仓格式化；仅格式化本次文件并记录主干现状。

### 11.2 API smoke（无账号写操作）

```bash
openbiliclaw discover-bangumi '科幻' --limit 5
openbiliclaw discover-bangumi-ranked --limit 5
openbiliclaw discover-bangumi-latest --limit 5
openbiliclaw fetch-bangumi --username <explicit-public-username> --limit 5
```

确认：

- User-Agent 合规；
- 默认 memory/profile 不变；
- 计数/评分/URL/封面正确；
- run ledger 与 status 合理；
- 没有 Authorization/Cookie header。

### 11.3 正式 producer + eval

1. 在临时 config/data root 启用 Bangumi 和 pool share。
2. `openbiliclaw discover --source bangumi --limit 10`。
3. 抽查 `discovery_candidates`：identity、strategy、keyword id、rating fields、NSFW。
4. 用用户真实 configured LLM/embedding 跑 eval/admission；记录 provider/model。
5. 抽查 `content_cache` 和 recommendation API：字段不丢、admission floor 生效。

### 11.4 配置与视觉

- Desktop 与 extension 保存所有字段，再回读 `/api/config` 和 runtime effective share。
- PC setup、desktop init drawer、extension init 分别测：
  - Bangumi-only + username；
  - Bangumi-only 无 username；
  - Bangumi discovery-only + 另一画像来源。
- PC、移动、extension side panel 各看：有图、无图、长中/日文标题、长摘要、评分未知、未上榜。
- 验证打开链接和本地 favorite/watch-later/dislike/chat；网络日志中没有 Bangumi write endpoint。

### 11.5 故障注入

- 429 + Retry-After → cooldown/rate_limited；
- search schema drift → search failed，但 ranked/latest 成功且 status partial；
- API timeout/500 → 有界退避，不热循环；
- `lain.bgm.tv` 图片失败 → text/cover fallback，不阻断候选；
- user 404/empty/private-only → 清晰 init/fetch 结果。

### 11.6 交付报告

报告：

- worktree/branch/commit；
- 自动化命令与结果；
- 三个 API 分支及公开收藏计数；
- DB candidate/cache 状态；
- LLM provider/model；
- 三端视觉结果；
- status/cooldown 故障注入；
- 未验证项和本地未跟踪/忽略产物。

只有上述相关测试和真实只读 E2E 完成后，才可把来源称为“接入完成”。

### 11.7 完成记录（2026-07-17）

- 静态/自动化：Ruff format/lint、MyPy、扩展 typecheck/build 全部通过；Fable 两轮 review 修复后 Python `5231 passed, 18 skipped`，扩展 `1076 passed`。
- 匿名 API：search/ranked/latest 各 5 条且零本地写入/零 LLM；公开用户名 `sai` 读取 5 条事件（done 2、wish 3），默认零 memory/profile 写入。
- 正式链路：真实 configured `openai_compatible/deepseek-v4-flash` + `ollama/bge-m3` 完成三分支 producer、keyword claim、eval/admission/cache；候选、缓存、identity、评分/排名和 run ledger 已抽查。
- 图片/三端：真实 `lain.bgm.tv` 图片代理 miss→hit；Desktop、移动 Web、extension side panel 卡片与设置已检查。真实 Chromium 点击审计确认 favorite/watch-later/dislike/chat 只调用本地 API，Bangumi 上游只有匿名读取和条目页 GET。
- 故障：429、schema drift partial、timeout/5xx、图片失败和公开用户异常均有自动化覆盖。
- Review 收口：显式 CLI/scheduler、最终预算扣账、本地状态与 keyword 生命周期已修复；官方 API 实测确认超界 offset 返回 400 后，补上 cursor 归零有界重试，并修复显式空 username、popup 草稿与存量 prompt 零值字段。
- 完整证据、精确计数与未验证项见 spec §13.3。

---

## 推荐实施顺序与依赖

```text
Task 0 spike
  ├─ Task 1 identity + data model
  └─ Task 2 client + normalizer
       ├─ Task 3 config/status
       ├─ Task 4 keyword generation
       └─ Task 5 producer/runtime
            ├─ Task 6 discovery CLI
            └─ Task 7 public collections/init
                 ├─ Task 8 settings/init UI
                 └─ Task 9 cards/image/local actions
                      └─ Task 10 docs
                           └─ Task 11 full verification
```

Task 1 与 Task 2 可以并行实现但合并前必须先完成字段 round-trip；Task 8/9 可在 API shape 锁定后并行。不要在 producer 之前只做 UI 假开关，也不要在 keyword claim 之前漏掉两条 generation 轨。
