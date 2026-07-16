# Multi-Platform Published Time Design

## Goal

补齐 issue #75 尚未实现的多平台发布时间，让用户能在推荐卡上判断内容新鲜度，同时不把发现时间冒充发布时间、不为补时间额外请求详情页，也不新增投币字段。

发布时间覆盖当前四个用户界面：桌面 Web、移动 Web、浏览器扩展 popup 和 CLI。推荐卡与惊喜推荐卡必须使用相同的数据语义和展示规则；字段缺失时整段隐藏，不显示空占位或伪造时间。

## Non-Goals

- 不采集、存储或展示投币数。
- 不为缺少发布时间的候选额外打开页面或请求详情 API。
- 不对旧 `content_cache` 数据执行联网历史回填；旧行在重新发现时自然补齐。
- 不把 `discovered_at`、推荐生成时间、任务创建时间或用户互动时间当作内容发布时间。
- 不实现浏览器右键菜单“在新标签页打开”的点击上报；页面无法可靠知道用户最终选择了哪个原生菜单项。
- 不把画像编辑改造成真正的乐观更新；现有即时 pending 状态和服务端权威结果保持不变。
- 不在本项工作中发布版本、回复或关闭 GitHub issue。

## Canonical Data Contract

统一内容模型新增两个互补字段：

- `published_at: str = ""`：可验证的精确发布时间，规范化为 UTC RFC 3339，格式为 `YYYY-MM-DDTHH:MM:SSZ`。
- `published_label: str = ""`：来源只提供相对时间时保存的安全展示文本，例如 `3小时前` 或 `2 years ago`。

精确时间优先于相对标签。一个来源同时提供两者时允许同时保存，但消费者必须优先使用 `published_at`。无法可信解析的值不得塞进 `published_at`；只有确实是平台提供的相对发布时间文本才进入 `published_label`。

新增集中式发布时间规范化工具，负责：

- 接受 Unix 秒、Unix 毫秒、RFC 3339/ISO 8601 和已知平台日期格式。
- 输出 UTC RFC 3339，不保留来源时区歧义。
- 拒绝布尔值、NaN、无穷值、空值、占位字符串和明显不合理日期。
- 对 `published_label` 合并空白、去除首尾空格、拒绝占位值并限制为 64 个字符。
- 解析失败时不抛出到采集主链路；保留可用的相对标签或返回两个空字段。

数据库写入边界再次调用同一规范化规则，防止某个适配器漏掉校验。异常来源值记录带平台和字段名的诊断，但不得包含 Cookie、URL token 或整段原始响应。

## Component Boundaries

- `src/openbiliclaw/published_time.py` 承载平台无关的后端规范化与 CLI 展示格式化。它只依赖 Python 标准库，不导入 discovery、storage、API 或 CLI 模块，避免形成循环依赖。
- 各平台适配器只负责选择语义明确的来源字段，再调用统一规范化工具；不得各自实现日期猜测规则。
- `DiscoveredContent` 和 `DiscoveryCandidateWrite` 只携带规范化后的字符串，不负责解析平台原始值。
- storage 层在持久化前调用统一工具做防御性校验，并负责空值保留的 upsert 语义。
- API 层只透传已规范化字段，不根据当前时间重写内容。
- 三个浏览器界面分别使用其现有 helper/formatter 边界实现同一显示契约；契约测试用相同边界样例防止三份实现漂移。CLI 直接复用 Python formatter。

## Data Flow And Storage

两个字段完整贯穿统一发现链路：

```text
platform payload
  -> source normalizer
  -> DiscoveredContent
  -> DiscoveryCandidateWrite
  -> discovery_candidates
  -> row_to_discovered_content
  -> content_cache
  -> recommendation / delight payload
  -> desktop / mobile / extension / CLI
```

需要同步扩展的结构包括：

- `DiscoveredContent` 与 `to_cache_kwargs()`。
- `DiscoveryCandidateWrite`、`discovered_content_to_candidate_write()` 和 `row_to_discovered_content()`。
- `discovery_candidates` 与 `content_cache` 的新建表 schema、旧库增量迁移、insert/upsert 和所有 DB row -> model 重建点。
- `RecommendationOut`、`PendingDelightOut`、reshuffle、pending delight、runtime delight event 及其它推荐/惊喜序列化出口。
- 稍后再看、收藏和消息卡只在其既有查询结果自然携带字段时展示，不新增额外查询或网络请求。

数据库列均为 `TEXT NOT NULL DEFAULT ''`。旧数据库通过现有 `_ensure_*_columns()` 模式增量加列。重新发现的 upsert 遵循：

- 新值非空时更新。
- 新值为空时保留已有非空值。
- 精确时间和相对标签分别保留，不让一次字段不完整的重新采集清空历史值。

不从 `discovered_at` 回填 `published_at`，也不把候选队列的 `created_at` 当作发布时间。

## Platform Mapping

各适配器遵循“精确值优先、相对标签兜底、缺失隐藏”：

- Bilibili：优先 `pubdate`；兼容明确标识为发布时间的等价字段。搜索、热门、相关推荐和扩展搜索入口都要映射。
- Douyin：优先 `create_time`。直连响应与扩展 fetch-tap 规范化结果都要保留该字段。
- X：优先 `createdAtISO`，再解析 `createdAt`；`createdAtLocal` 只有带明确时区时才可进入精确字段。
- Zhihu：使用内容自身的创建时间；不得使用收藏/点赞等 `interaction_time`。
- Reddit：使用 `created_utc`；评论与帖子使用相同规则。
- YouTube：优先 `timestamp`、`release_timestamp`、`upload_date`、`publishedAt` 等精确值；只有 `publishedTimeText` 时写入 `published_label`。
- Xiaohongshu：只使用状态/API 数据里语义明确的内容发布时间；DOM 卡片或当前状态没有可靠时间时保持为空。
- 通用网页 LLM 提取：本轮不要求模型猜测发布时间；没有结构化可靠值就留空。

平台字段变化必须覆盖服务端直连和浏览器扩展登录态采集两条路径，避免同一平台因来源不同出现永久缺字段。

## Display Contract

四端共享同一显示语义。精确时间按用户本地时区格式化：

- 小于 1 分钟：`刚刚`
- 小于 24 小时：`N 小时前`
- 小于 7 天：`N 天前`
- 同一年：`M月D日`
- 更早：`YYYY-MM-DD`

精确时间元素的辅助文本或 `title` 提供完整本地日期时间。若没有精确时间但存在 `published_label`，按纯文本显示该标签。两者都没有时不渲染发布时间节点。

发布时间放在作者/主题元信息行末尾，采用现有 muted token，不增加新的强调色。推荐网格与惊喜横幅使用相同 formatter；移动 Web、扩展 popup 和 CLI 输出必须与桌面语义一致。CLI 可使用同样的中文短格式，不输出原始 UTC 字符串。

格式化函数必须处理未来时间、无效日期和客户端时钟偏差：小幅未来偏差显示 `刚刚`，明显未来时间按绝对日期显示，不产生负数文案。

## Remaining Issue #75 Decisions

本项实现同时补一条精确静态契约测试，要求桌面推荐卡的左键 `click` 与中键 `auxclick(button === 1)` 都调用 `openRecommendation(item, card)`，从而同时执行点击上报、状态行更新和 toast。现有实现不需要行为修改，仅需要防回归。

以下保持现状：

- 原生右键菜单打开不做上报承诺。
- 画像编辑继续立即进入 pending/disabled 状态，服务端成功后重渲染，失败时恢复并提示；不引入嵌套状态回滚框架。

## Error Handling

- 单个平台缺少或返回异常时间不得导致候选丢弃、批次失败或 API 500。
- 来源解析器只接受已知键，不从任意 `time`/`date` 字段猜测语义。
- 精确时间解析失败时可以退回来源提供的相对发布时间标签，但不能把失败原文伪装成精确时间。
- API 字段为新增可选字符串，默认空值；旧扩展和旧客户端继续忽略未知字段。
- 前端 formatter 对任意无效值返回空字符串，所有插入 DOM 的标签继续使用现有转义或 `textContent` 路径。
- 重新发现缺字段时保留旧值，避免低信息来源覆盖高信息来源。

## Automated Testing

后端测试覆盖：

- Unix 秒/毫秒、RFC 3339、带时区字符串、相对标签、占位值和异常值的集中规范化。
- Bilibili、Douyin、X、Zhihu、Reddit、YouTube、Xiaohongshu 的精确/相对/缺失映射。
- `DiscoveredContent -> DiscoveryCandidateWrite -> discovery_candidates -> DiscoveredContent -> content_cache` 完整 round-trip。
- 新建数据库 schema 与旧库增量加列。
- upsert 用新非空值更新、用空值保留旧值。
- 推荐列表、reshuffle、pending delight、delight batch 和 runtime event 序列化。
- Recommendation/delight API 默认空字段保持向后兼容。

前端和 CLI 测试覆盖：

- `刚刚`、小时、天、同年、跨年、未来时间和无效输入边界。
- `published_at` 优先于 `published_label`，仅标签和双空值降级。
- 桌面推荐/惊喜、移动推荐/惊喜、popup 推荐/惊喜和 CLI 输出。
- 所有空字段不渲染节点，标签通过安全文本路径输出。
- 中键 `auxclick` 与左键都调用 `openRecommendation()` 的精确回归。

## Verification

实现完成后至少运行：

```bash
ruff format src/ tests/
ruff check src/ tests/
mypy src/
pytest
cd extension && npm test
cd extension && npm run typecheck
```

扩展若没有对应脚本则使用仓库现有等价命令。真实浏览器验证桌面 Web、移动 Web 和扩展 popup：精确时间、相对标签、缺失字段、惊喜切换和中键提示均符合设计；验证过程不得使用用户真实 Cookie 或修改生产数据。

## Documentation

实现同步更新：

- `docs/changelog.md`
- `docs/modules/discovery.md`
- `docs/modules/runtime.md`
- `docs/modules/extension.md`
- `docs/modules/cli.md`
- `docs/architecture.md` 的模块角色和数据流说明
- `docs/spec.md` §3 系统架构图
- `README.md` 与 `README_EN.md` 顶部架构图
- issue #75 的既有 spec/plan，明确投币不做、发布时间由本设计接续完成

文档必须明确区分内容发布时间、发现时间和推荐生成时间，并说明旧缓存不会联网回填。
