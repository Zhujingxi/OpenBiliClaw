# Network Outbound-Proxy Config Spec — WebUI 可配置的海外出口代理(issue #89)

**Created:** 2026-07-11
**Scope:** 新增 `[network].proxy` 配置(http/https/socks5/socks5h),作用于**海外出口客户端**
(OpenAI/Claude/Gemini/DeepSeek/OpenRouter/openai_compatible 的 chat+embedding SDK、Codex OAuth
token 刷新、YouTube yt-dlp、updater GitHub 元数据/下载),并在桌面 Web 设置-通用暴露输入框 +
连通性探测。涉及模块:`config`、`llm`(registry/providers)、`api`(config endpoints)、
`web/desktop`、`youtube`、`runtime/updater`。
**Out of scope:**
- Bilibili 代理(`[bilibili].proxy` 已存在,语义独立,不合并、不在本次暴露到 UI)。
- 所有 CN 直连 / localhost 客户端(bilibili、douyin、ollama、图片缓存 CN 分支)——不但不接入,
  还要用守卫测试钉死"永不接入"。
- 扩展 popup 内的代理设置(popup 无设置面板;代理是后端行为,四端契约中声明此排除)。
- openclaw 集成的 aiohttp 客户端(非核心路径,继承 env 现状保留)。
- 按 provider 粒度的代理开关(单字段全局海外代理够用,YAGNI)。
- PAC / 代理自动发现 / 认证代理的凭据管理 UI(URL userinfo 形式 `socks5://user:pass@host` 天然支持)。

## Goal

**现状成本:** 海外 LLM/YouTube 请求只能靠进程级 `HTTP_PROXY/HTTPS_PROXY` env 走代理。桌面安装、
LaunchAgent、Docker 场景下用户没有可发现的配置入口(memory: LaunchAgent 缺 proxy env 直接 403);
而无脑设置系统代理又会命中踩坑规则 1 的反面——CN 客户端被代理劫持触发 B 站风控(`df626f3f`)。

**目标结果:**
1. 用户在 设置-通用 填 `socks5://127.0.0.1:1080` 保存后,所有海外 LLM 调用经代理出站,
   B 站/抖音/Ollama 请求字节级不变(仍 `trust_env=False` 且无 proxy 参数)。
2. 留空(默认)时行为与当前 main 完全一致(SDK 继承 env 的现状保留,Docker 代理探测不受影响)。
3. 非法值(错误 scheme / 无 host)在保存时被 400 拒绝,不落盘(踩坑规则 7)。

**验证命令:**
```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_llm_registry.py \
  tests/test_api_config_guards.py tests/test_api_config_probe.py -q
.venv/bin/python -m mypy src/
.venv/bin/python -m ruff check src/ tests/
```

## Design invariants (MUST hold in every phase)

1. **CN 直连隔离:** 设置 `[network].proxy` 后,`BilibiliAPIClient`(`bilibili/api.py:230-248`)、
   `douyin_direct.py:132`、`ollama_provider.py:207`、`ollama_diagnostics.py`、
   `ollama_supervisor.py:161`、`image_cache.py` CN 分支的 httpx 客户端构造参数不含该代理且
   `trust_env=False` 不变。验证面:守卫测试逐一断言这些构造点(monkeypatch 捕获 kwargs)。
2. **空值零漂移:** `network.proxy == ""` 时,每个被改造的构造点收到的 kwargs 与改造前等价
   (不传 `http_client`/`proxy`/`client_args` 键,或传 `None` 且 SDK 语义等价——以"不传键"为准)。
   验证面:registry/provider 单测断言空值路径不注入任何代理相关参数。
3. **保存时拒绝非法值:** scheme 白名单 `{http, https, socks5, socks5h}`,必须有 host;
   PUT `/api/config` 携带非法值返回 400 且 config.toml 不变。验证面:API 守卫测试。
   加载时(config.toml 手改成非法值)不 crash:log WARNING 并按空值处理(踩坑规则 4 的
   clamp-to-default 语义)。
4. **失败带真因:** 代理连不通时,provider 报错信息必须能区分"代理拒绝/不可达"与"上游 API 错误"
   (至少 probe 端点做到:返回错误分类字段)。验证面:probe 单测 mock 连接拒绝。
5. **文档同步:** `docs/modules/config.md` 新字段、`docs/changelog.md` 当前版本 bullet,
   与代码同 PR 合入(仓库文档纪律)。

## Current diagnosis

### D1. 海外客户端代理能力 = 进程 env,进程内无配置

- `openai_provider.py:91-96` `AsyncOpenAI(...)` 未传 `http_client` → httpx 默认继承 env。
  DeepSeek(`registry.py:500`)、OpenRouter(`registry.py:615`)、openai_compatible
  (`registry.py:632`)均复用此构造器。
- `claude_provider.py:44-48` `AsyncAnthropic(...)` 同上。
- `gemini_provider.py:84-86` `genai.Client(http_options=...)`;google-genai>=1.66 的
  `HttpOptions` 已含 `client_args`/`async_client_args`(本机 1.66 实测存在),可注入
  `{"proxy": url}` 到底层 httpx 客户端。
- `youtube/client.py:109,167` `YoutubeDL(_YTDLP_FLAT_OPTIONS)`(选项常量在 `:61-69`)
  无 `proxy` 键;yt-dlp 原生支持 `"proxy"` option。
- `codex_auth.py:165` `httpx.AsyncClient()` 裸构造(token 刷新打 openai.com,海外出口)。
- `runtime/updater.py:725` `httpx.AsyncClient(timeout=30, verify=verify_tls)`(GitHub tags 元数据);
  下载路径同文件内其余 AsyncClient 构造点。GitHub 在部分网络环境同样需代理。
以上全部为确认事实(已逐点读源码)。

### D2. CN 客户端已按踩坑规则 1 显式直连,新代理绝不能波及

- `bilibili/api.py:230-248`:`httpx.AsyncClient(trust_env=False, proxy=self._proxy)`,
  `self._proxy` 来自 `[bilibili].proxy`(`config.py:371-375`,默认空)。
- `douyin_direct.py:132`、`ollama_provider.py:207,256-258`、`ollama_diagnostics.py:261,358`、
  `ollama_supervisor.py:161`、`cli.py:1347,1367`:全部 `trust_env=False` 无 proxy。
- `image_cache.py:411-414`:`trust_env=_is_direct_fetch_host(host)` 按 host 分流
  (CN CDN 直连,海外 CDN 继承 env)。
历史证据:继承代理曾破坏 B 站请求并掩盖登录诊断(`df626f3f`)。

### D3. 配置与 API 的既有扩展模式完整可复用

- config 为 `@dataclass` 体系:section dataclass → `_build_config`(`config.py:926+`)→
  `save_config`/`_render_config_toml`(`config.py:2299,2402` 附近有 bilibili.proxy 渲染先例)。
- env override 泛化机制 `_apply_env_overrides`(`config.py:862`):`OPENBILICLAW_NETWORK_PROXY`
  自动映射到 `network.proxy`(单词 key,无需专门 handler)。
- API:GET `/api/config` → `_config_to_response`(`app.py:8905`)+ `ConfigResponse`
  (`models.py:1305`);PUT → `update_config`(`app.py:9470`)按 section apply;
  probe 端点 `/api/config/probe-service`(`app.py:9453`)可扩展代理探测。
- 前端:设置-通用面板 `index.html:580-592`;`app.js` restore(约 `:4838`)/ collect
  (约 `:5556`)成对扩展。
- socks 依赖已就绪:`pyproject.toml:28` `httpx[socks]>=0.27`,零新依赖。

### D4. 覆盖现状

`tests/test_config.py`、`tests/test_llm_registry.py`、`tests/test_llm_providers.py`、
`tests/test_api_config_guards.py`、`tests/test_api_config_probe.py`、
`tests/test_runtime_updater.py` 均存在,新测试按既有文件归位,无新测试基建。

## Priority classification

| Phase | Content | Tier | Why |
| --- | --- | --- | --- |
| 0 | `NetworkConfig` + 校验/规范化 + load/save/env + helper 模块 | **MUST** | 一切挂载点的单一事实源;校验是踩坑规则 7 的门 |
| 1 | LLM registry/providers 接线 + CN 隔离守卫测试 | **MUST** | issue 的核心价值;守卫测试防回归 B 站风控 |
| 2 | API 暴露(GET/PUT + 400 拒绝 + 热重载)| **MUST** | 无此则 UI 无从谈起 |
| 3 | 桌面 Web 设置-通用 UI + probe 代理探测 | **MUST**(probe 为 RECOMMENDED)| issue 的字面诉求 |
| 4 | 长尾挂载:yt-dlp / codex_auth / updater | RECOMMENDED | 一致性;不阻塞主价值 |
| 5 | 文档 | **MUST** | 仓库合并纪律 |

依赖:0 → {1,2} → 3;4 仅依赖 0;5 收尾。
**Wave A** = Phase 0+1(可独立合入:config 字段 + LLM 生效,env/toml 手改即可用)。
**Wave B** = Phase 2+3(UI 化)。**Wave C** = Phase 4+5。
安全停点:Wave A 后即可停(功能可用但无 UI);Wave B 后 issue 关闭;Wave C 补全。

## Phase designs

### Phase 0 — NetworkConfig 与规范化 helper

**接口:**
```python
# config.py
@dataclass
class NetworkConfig:
    # Outbound proxy for OVERSEAS clients only (LLM SDKs, YouTube, GitHub
    # updater, Codex OAuth). CN-direct clients (bilibili/douyin/ollama/...)
    # never use it — see docs/plans/2026-07-11-network-proxy-config-spec.md.
    proxy: str = ""  # "" = disabled; http:// | https:// | socks5:// | socks5h://

def normalize_outbound_proxy(value: str) -> str:
    """Return the normalized proxy URL, or raise ValueError with a
    user-facing reason (bad scheme / missing host). "" passes through."""
```
- `Config` 增加 `network: NetworkConfig` 字段;`_build_config` 读 `raw["network"]`,
  非法值 log WARNING 后置空(invariant 3 的 load 侧);`_render_config_toml` 渲染
  `[network]` 段(带"仅海外出口"注释)。
- 新模块 `src/openbiliclaw/network.py`:
```python
def outbound_proxy_url() -> str | None: ...      # "" → None
def set_outbound_proxy(url: str) -> None: ...    # 进程级单一事实源
def outbound_httpx_kwargs() -> dict[str, Any]:   # {} 或 {"proxy": url}
```
  由 `create_app()`、CLI 入口、`update_config` 热重载路径调用 `set_outbound_proxy`。
  选 helper 而非全链路穿参:codex_auth/updater 等构造点没有 config 引用,穿参改动面大
  且易漏;helper 的 setter 只在三个 config 应用点调用,测试可直接 set/reset。
- **错误行为:** `normalize_outbound_proxy` 抛 `ValueError`,消息含非法原因
  (中文,面向 UI 直显)。
- **测试:** `tests/test_config.py` — 合法四 scheme 通过、大小写规范化、非法 scheme /
  缺 host 抛错、toml round-trip、`OPENBILICLAW_NETWORK_PROXY` env override 生效、
  load 非法值 WARNING+置空。
- **验收门:** 上述测试全绿;`mypy src/` 0 error。

### Phase 1 — LLM 接线 + CN 隔离守卫

- `registry.py` 各海外 `_maybe_*` 工厂把代理传入 provider:
  - `OpenAIProvider.__init__` 新增 `proxy: str = ""`;非空时构造
    `http_client=httpx.AsyncClient(proxy=proxy, timeout=timeout)` 传给 `AsyncOpenAI`
    (DeepSeek/OpenRouter/openai_compatible 经此复用)。空值不传 `http_client`(invariant 2)。
  - `ClaudeProvider.__init__` 同构:`AsyncAnthropic(http_client=...)`。
  - `GeminiProvider.__init__` 新增 `proxy`;非空时 `http_options` 增
    `client_args={"proxy": p}, async_client_args={"proxy": p}`。
  - `_maybe_ollama_provider`(`registry.py:536`)**不传**(CN/localhost)。
- 工厂读值统一走 `network.outbound_proxy_url()`。
- **测试:**
  - `tests/test_llm_registry.py`:set proxy 后,各海外 provider 的底层 client 拿到代理
    (断言 SDK client 的 `_client`/transport 构造参数,或 monkeypatch httpx.AsyncClient
    捕获 kwargs);ollama 构造点无代理。
  - `tests/test_llm_providers.py`:空值路径构造参数与 main 现状一致。
  - **CN 隔离守卫**(invariant 1,新增 `tests/test_network_proxy_isolation.py`):
    set proxy 后 monkeypatch `httpx.AsyncClient.__init__` 捕获全部构造,驱动
    bilibili/douyin/ollama 客户端构造路径,断言无一收到 proxy 且 `trust_env=False`。
- **验收门:** 守卫测试对"未来有人把 helper 接进 CN 客户端"必须 FAIL-first 验证过
  (临时注入代理跑一次确认测试确实能红)。

### Phase 2 — API 暴露与热重载

- `models.py`:`NetworkConfigOut(proxy: str)`;`ConfigResponse` 增 `network` 字段;
  `ConfigUpdateIn` 已是泛化 dict section,增 `network: dict[str, object] | None = None`。
- `app.py`:`_config_to_response` 映射;`update_config` 增 network apply 分支——
  `normalize_outbound_proxy` 抛错 → 400(结构与既有 bilibili 分支 `:9536` 一致,
  错误消息透传 ValueError 文案);保存成功后调 `set_outbound_proxy` 并热重建 LLM runtime
  (复用现有 update_config 重建路径,确认 registry 工厂重跑即可生效)。
- 代理值非密钥,不做 mask-echo 处理(URL userinfo 里可能有密码——GET 返回时对
  userinfo 做 `***` 遮蔽,PUT 收到遮蔽回显时视为未修改,复用 `_is_masked_echo` 语义)。
- **测试:** `tests/test_api_config_guards.py` — 合法保存落盘 + 响应含新值;非法 400 且
  toml 不变;masked 回显不覆盖;保存后 registry 重建拿到新代理。
- **验收门:** 事务性测试(`tests/test_api_config_transactional.py` 模式)通过。

### Phase 3 — 桌面 Web UI + 探测

- `index.html` 设置-通用面板(`:580`)增字段:label「海外出口代理」、
  placeholder `socks5://127.0.0.1:1080`、hint 明示「仅作用于海外 AI 服务 / YouTube /
  更新检查;B 站等国内请求始终直连」。
- `app.js`:restore(`setInput`)+ collect(payload.network = {proxy})成对补齐;
  保存失败 400 时展示后端中文错误消息(复用既有错误展示路径)。
- probe:`/api/config/probe-service` 增 `network_proxy` 目标——用待测代理请求
  `https://www.gstatic.com/generate_204`(5s 超时),返回 ok / 错误分类
  (`proxy_unreachable` / `proxy_rejected` / `timeout`),不写盘。UI 加「测试」按钮,
  沿用现有 probe 按钮模式(`tests/test_desktop_web_config_probe.py` 有先例)。
- **四端契约声明:** desktop 与 mobile web 共用此设置页;CLI 经 `config-show` 可见 +
  toml 直改;extension popup 无设置面板,PR 描述声明排除(踩坑规则 5)。
- **测试:** `tests/test_api_config_probe.py` probe 分类;
  `tests/test_desktop_web_config_probe.py` 前端契约(若该文件模式适用)。
- **验收门:** 手工 E2E 一次:填非法值→400 文案可见;填合法值→保存→`config-show` 可见。

### Phase 4 — 长尾挂载(yt-dlp / codex_auth / updater)

- `youtube/client.py`:构造 YoutubeDL options 时非空代理注入 `{"proxy": url}`
  (两处 `:109,167`,统一经一个 `_ytdlp_options()` helper 收敛)。
- `codex_auth.py:165`:`httpx.AsyncClient(**outbound_httpx_kwargs())`。
- `runtime/updater.py`:文件内 AsyncClient 构造点(`:725` 及下载路径)同样注入;
  注意与 `verify_tls` 参数共存。
- **测试:** 各自现有测试文件补一条"代理注入/空值不注入"断言;
  `test_runtime_updater.py` 确认 TLS 降级路径与代理共存不冲突。
- **验收门:** 全量 pytest 回归绿。

### Phase 5 — 文档

见下节清单;与 Wave C 代码同 PR。

## Expected impact

| Lever | Measured effect |
| --- | --- |
| Phase 1 | LaunchAgent/桌面场景无需 env 注入即可让 LLM 走代理(消除 memory 中 403 一类工单) |
| Phase 1 守卫测试 | "代理波及 CN 客户端"回归从人肉审查变为 CI 强制 |
| Phase 3 | issue #89 字面诉求关闭;非法配置由静默失败变为保存时 400+文案 |
| Phase 4 | YouTube/更新检查在受限网络下可用性与 LLM 对齐 |

## Documentation obligations

- `docs/modules/config.md` — `[network]` 段新字段表(MUST)。
- `docs/changelog.md` — 当前版本块加 bullet(MUST)。
- `docs/modules/llm.md` — provider 构造新增 proxy 参数,若该文档列公共 API(按现状核对)。
- 架构图:不涉及跨模块接线/新依赖块,**不触发**(`network.py` 为 config 附属 helper)。
- README 📌 callout:留待下次发版时评估,不在本 PR(功能未随版发布前不进 teaser)。
- `docs/modules/cli.md` / installer 文档:无 CLI/安装流变化,不触发。
