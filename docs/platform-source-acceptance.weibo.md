# 微博平台来源验收报告

> Integration level 固定为 `discovery-only`。本报告区分静态注册、模拟回归、真实上游、真实 LLM 和浏览器视觉证据；未获授权的提交、发版和上游写操作均未执行。

## 范围与 provenance

- Contract: `docs/platform-source-contract.weibo.toml`
- Worktree / baseline: `/Users/white/workspace/OpenBiliClaw/.worktrees/weibo-source` / `7fdb1dbab53897fb578cd16905a0c12a3cd081a0`
- Main at final verification: `f9f4f3bf22b3eea511d907e55c448e02fe2137f7`；worktree 比 main 落后 7 个提交，47 个改动路径与 main 漂移重叠，因此本轮没有在 118 个工作区改动路径上自动 merge
- Python import / CLI: `PYTHONPATH=$PWD/src $PWD/.venv/bin/python` / `$PWD/.venv/bin/openbiliclaw`
- Real E2E storage: 独立临时 config/data/database；没有读取或写入用户微博 Cookie
- Browser / extension: Chrome for Testing、macOS Firefox、extension `0.3.201`
- Release mutations: 无 commit、merge、version bump、tag、push 或 publish

## Gate ledger

| Gate | Applicability | Status | Evidence | Remaining risk |
| --- | --- | --- | --- | --- |
| Scope/worktree provenance | required | PASS | 独立 `feat/weibo-source` worktree；显式 import path | 提交前需人工同步 47 个 main 漂移重叠路径 |
| Frozen source contract | required | PASS | contract audit: 34 PASS / 0 missing / 14 N/A / 12 manual | audit 是注册清单，不替代语义 E2E |
| Historical precedent + transport choice | required | PASS | GitHub 前例、许可证与匿名 visitor 选择记录在模块文档 | 私有 H5 API 仍可能漂移 |
| Real upstream spike + fixtures | required | PASS | 真实 search/hot/creator；脱敏 success、empty、challenge、MIME、JSONP、schema-drift fixtures | 上游未提供稳定公开 SLA |
| Canonical identity / storage | required | PASS | `weibo:<post_id>`；`weibo` / `wb` / `微博` / host 归一和去重测试 | none |
| Transport / normalizer / taxonomy | required | PASS | 动态 visitor callback/version/request_id/return_url；严格 JSONP；false-empty、429、MIME、schema drift 覆盖 | 仅 HTTP 429 判 rate-limited，避免猜测成功 payload 文案 |
| Auth / credential / account | N/A | PASS | anonymous discovery；credential kind `none`；注入 client auth/Cookie 不外泄 | visitor SUB 仅进程内存，不是用户登录态 |
| Extension task / cookie sync | N/A | PASS | manifest/source/exclusion tests 均无微博 host 权限、task、Cookie/native writeback | none |
| Profile bootstrap/incremental | N/A | PASS | discovery-only；guided init 和 profile sinks 明确排除并有 exact tests | none |
| Formal discover / leases / admission | required | PASS | 双关键词轨、空池画像回落、部分 handoff、normalize/enqueue 故障回滚、最终留存后计费 | 进程崩溃依赖共享 lease reclaim 语义 |
| Eval / publication / recommendation | required | PASS | UTC/future/invalid 时间、真实商汤正式 evaluator、推荐 DTO/卡片 `share_count` | 模型评分具有非确定性 |
| Config / API / status convergence | required | PASS | defaults/load/save/API/disk/runtime；桌面页面保存后 API 与磁盘回读一致 | none |
| Desktop surface | required | PASS | 来源筛选/计数、no-cover text card、saved local-only 的真实 DOM/截图 | none |
| Mobile surface | required | PASS | 身份、作者、share、no-cover、saved local-only 的真实 DOM/截图 | 全产品移动端均无 per-platform filter，规格明确排除 |
| Extension popup surface | required | PASS | 360/420px 导航命中区、无竖排/溢出、no-cover、saved identity/local-only 真实 DOM/截图 | popup 全产品均无 per-platform filter，规格明确排除 |
| Native save | N/A | PASS | 微博 membership 创建即 terminal `local_only_source`；三端无同步/重试按钮；`native_save_tasks=0` | open/remove/feedback 仍可用 |
| Image delivery | required | PASS | `sinaimg.cn` allowlist、CN direct、Weibo Referer、逐跳 host revalidation 测试 | 共享代理仍以受控 host allowlist 为核心边界，未做 DNS pinning；见下方保留风险 |
| Chrome + Firefox artifacts | required | PASS | Chrome/Firefox 双 build、manifest 检查、zip/tree hash；Firefox 真浏览器启动 smoke | Firefox 只做启动 smoke，交互 DOM 由共享 popup 源码的 Chrome E2E 与 Node 测试覆盖 |
| Focused + full verification | required | PASS | focused Python 275 passed；extension 1273 passed；mypy/ruff/typecheck 通过；全量 Python 结果见命令表 | FastAPI `on_event` 仅有既有弃用 warning |
| Safe real E2E | required | PASS | 真实微博 search/hot/creator；isolated DB；真实 UI；真实商汤日日新 evaluator | upstream 和模型调用均会消耗网络/模型额度 |
| State-changing upstream actions | N/A | PASS | contract `mutating_actions=[]`；实际运行 0 次上游写动作 | none |
| Documentation / release-readiness | required | PASS | README、模块、架构、API/CLI/config/runtime/extension/saved/storage/LLM/changelog 同步 | 本轮不发版、不改版本 |

## 数据、能力和隐私边界

- Primary / fallback owner: backend / none。
- 每次微博请求剥离 ambient `Authorization` 和用户 `Cookie`，自建 client 使用 `trust_env=false`；只允许该 client 内存中的匿名 visitor `SUB`。
- Stable identity: `weibo:<post_id>`；URL 优先 `weibo.com/<uid>/<bid>`，缺字段时回落 `m.weibo.cn/detail/<post_id>`。
- Empty 只接受明确 zero-total 或已知空容器；未知非空 card、total mismatch、challenge HTML、坏 JSON/JSONP 均不会伪装成健康空结果。
- Engagement: `view / like / comment / share` 映射；`favorite / danmaku` 结构性 unavailable，不显示伪值。
- 收藏只保存 OpenBiliClaw 本地 membership；不创建 native-save task，不向微博写任何状态。

## 命令与自动验证

| Command | Exit | Summary / artifact |
| --- | ---: | --- |
| `audit_platform_source.py --contract ... --check --json` | 0 | 34 PASS / 0 MISSING / 14 N/A / 12 MANUAL；required missing=0 |
| 微博 client/producer/wiring/contract/API/CLI/web/saved/platform/policy/event/image focused pytest | 0 | 275 passed |
| full `pytest -q` | 0 | 7651 passed / 99 skipped / 0 failed；614.92s |
| `npm test`（extension） | 0 | 1273 passed |
| extension TypeScript typecheck | 0 | passed |
| `mypy src/` | 0 | Success: 246 source files |
| full Ruff check + format check + `git diff --check` | 0 | passed |
| Chrome final zip | 0 | SHA-256 `bb725d95157d3dbee1811a7abb3352b846ec2d8e64f902c2579a5e74b8695749` |
| Firefox final zip | 0 | SHA-256 `97e434d5688c4d0a2bf2b678034019a6e27c99a87aed7fa17debebc58b6165d5` |
| Firefox `web-ext lint` | 0 | 0 errors / 0 notices；20 条既有 unsafe-innerHTML warnings |
| Firefox real startup smoke | expected termination | `web-ext run` 从 final `dist-firefox` 启动；3.5 秒后主动终止隔离进程组 |

两个 final manifest 都是 MV3 / `0.3.201`，且没有显式 `weibo` / `sinaimg` host permission。Chrome final dist tree SHA-256 为 `bbca06c98babb3508bffff81e7a11369f3acf12374b0e5a5739d29ade565446d`；Firefox final dist tree SHA-256 为 `c8d0552fe92cd6fa152b24f62e2820bded546212fd7beae04de476fce376d969`。

## 真实 E2E

| Scenario | Applicability | Status | Evidence |
| --- | --- | --- | --- |
| Anonymous search | required | PASS | `OpenAI` 2/2；最终商汤链路 `人工智能 大模型` 3/3；用户 Cookie 读取 0、本地副作用 0 |
| Hot → real post | required | PASS | hot seed 后抓到 3/3 条真实微博；不是把热词伪装成内容 |
| Public creator timeline | required | PASS | UID `2803301701` 返回 2/2 条真实微博 |
| Real LLM evaluation | required | PASS | SenseNova host；`openai_compatible` / `deepseek-v4-flash`；正式 `ContentDiscoveryEngine.evaluate_content` |
| Recommendation render | required | PASS | desktop/mobile/popup 都出现微博身份、作者、正文和 `share_count`，无 B 站假 URL |
| Local saved membership | required | PASS | API `local_only_source`；三端无 sync/retry；DB `native_save_tasks=0` |
| UI config round-trip | required | PASS | desktop 将 interval 3→4、share 1→2；保存后 `/api/config`、磁盘和 runtime effective 值一致 |
| Upstream mutations | N/A | PASS | follow/like/comment/repost/native-save 等写操作 0 |

### 商汤日日新实测记录

- 真实微博匿名搜索关键词：`人工智能 大模型`；返回 3、规范化 3。
- 候选：微博 id `5329372229931405`，作者“环球时报”，`share_count=22`。
- 正式 evaluator：9.222 秒，score `0.55`，reason“涉及AI应用，但缺乏技术细节，偏新闻快讯”，topic `人工智能`，style `curiosity_spark`。
- 路由：instance `openai_compatible`，provider `openai_compatible`，model `deepseek-v4-flash`，caller `discovery.evaluate_single`。
- Usage：prompt 2046、completion 819、total 2865 tokens。
- 配置只读加载；报告不保存 base URL、API key 或原始响应；用户微博 Cookie 未提供。

### 真实批量推荐与浏览器 E2E（2026-08-10）

- 上游：匿名真实搜索 `OpenAI`、`人工智能`、`大模型`，共返回 36 条，规范化去重后 35 条；为视觉覆盖选取 18 条，其中无封面 6 条、带封面 12 条。
- 正式管线：`DiscoveryCandidatePipeline` 插入/评估 18 条，11 条进入推荐池、7 条拒绝；`RecommendationEngine` 为 11 条生成推荐文案并计算惊喜分，最终 API 返回 5 条普通推荐和 6 条惊喜推荐（其中 1 条真实无封面）。
- 正式模型：商汤日日新 `openai_compatible` / `deepseek-v4-flash`，实际调用 `discovery.evaluate_batch` 与 `recommendation.write_expression`；合计记录 13,613 tokens（prompt 9,378、completion 4,235），没有手工分数或模拟推荐文案。
- 插件：Chrome for Testing 加载当前 worktree unpacked extension，隔离 profile 将 backend endpoint 指向本轮 `127.0.0.1:8000`。420px 与 360px 均无横向溢出；微博来源、作者、正文、`share_count` 和动作区可见；惊喜推荐导航、展开区、带图与无封面文字 fallback 均可用。
- 移动端：390x844 真实页面显示 5 条微博普通推荐和 6 条惊喜推荐；带图惊喜图片经 `/api/image-proxy` 成功解码（naturalWidth 1920），无封面惊喜与普通微博均为真实正文卡；动作按钮在卡片内，页面无横向溢出。
- 浏览器日志：插件端和移动端均为 0 console error / 0 warning；隔离后实际业务请求全部命中 8000 且返回 200。测试 profile 初次按产品默认值误连 8420，仅执行 GET 并立即排除，该阶段截图和数据不计入验收。
- 安全边界：用户微博 Cookie 读取 0、微博上游写操作 0、用户 `config.toml` 写入 0。日日新密钥只复制到一次性隔离目录，测试结束后销毁。

### 视觉证据

- `output/playwright/weibo-e2e/popup-420-after-fix.png`：420px popup，无逐字竖排、无横向溢出。
- `output/playwright/weibo-e2e/popup-360-after-fix.png`：360px popup，导航命中区与正文/动作均在 viewport 内。
- `output/playwright/weibo-e2e/popup-saved-local-only-360-after-fix.png`：popup 收藏身份与 local-only 能力。
- `output/playwright/weibo-e2e/desktop-weibo-no-cover-after-fix.png`：桌面 no-cover 正文卡。
- `output/playwright/weibo-e2e/desktop-saved-local-only-after-fix.png`：桌面收藏正文卡，无误导同步动作。
- `output/playwright/weibo-e2e/mobile-weibo-no-cover-after-fix.png`：移动 no-cover 正文卡。
- `output/playwright/weibo-e2e/mobile-saved-local-only-after-fix.png`：移动收藏 local-only。
- `output/playwright/weibo-live-sensenova/popup-420-real-sensenova.png`：真实微博 + 日日新，420px 插件普通推荐与带图惊喜推荐。
- `output/playwright/weibo-live-sensenova/popup-420-recommendation-real-sensenova.png`：420px 插件真实正文推荐卡、微博身份与互动指标。
- `output/playwright/weibo-live-sensenova/popup-360-no-cover-real-sensenova.png`：360px 插件真实无封面惊喜推荐文字 fallback。
- `output/playwright/weibo-live-sensenova/mobile-delight-cover-real-sensenova.png`：390px 移动端真实带图惊喜推荐。
- `output/playwright/weibo-live-sensenova/mobile-delight-no-cover-real-sensenova.png`：390px 移动端真实无封面惊喜推荐文字 fallback。
- `output/playwright/weibo-live-sensenova/mobile-recommendation-text-real-sensenova.png`：390px 移动端真实微博正文推荐卡与完整动作区。

## 保留风险

1. 微博 H5 / visitor 接口不是稳定公开 API；schema、callback 或风控策略变化时会按 `schema_changed / blocked / upstream_error` 降级，不会冒充 empty。
2. 共享 image proxy 对每个 URL/redirect hop 做 scheme、userinfo、精确 suffix allowlist、跳数、MIME 和大小校验；当前没有把 DNS 解析结果 pin 到实际连接地址。由于这里只允许项目硬编码、非用户可控的 CDN 域名，这不是微博新增的任意 URL SSRF，但若要把该共享边界提升到 DNS-rebinding hardened，需要独立设计 address pinning 与 env-proxy 兼容策略。
3. 工作树基线落后 main 且有 47 个重叠路径；合入前必须人工 rebase/merge 并重跑本报告的 full gates。

## 最终结论

- Verdict: `PASS for the discovery-only Weibo adapter in this worktree`。
- Intentional exclusions: contract 中的 profile import/incremental、extension task/cookie、setup、credentials、favorite/danmaku、native deep-link、native-save。
- Registration inventory 的 12 个 MANUAL 项已由本报告中的语义测试、真实 upstream、真实 LLM、浏览器和 artifact 证据人工裁定；共享 DNS pinning 风险明确保留，不伪称已经解决。
- Release mutations performed: none。
