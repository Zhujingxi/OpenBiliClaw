# Network Outbound-Proxy Config — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: superpowers:executing-plans (execute this plan task-by-task).
> **Spec:** [`2026-07-11-network-proxy-config-spec.md`](./2026-07-11-network-proxy-config-spec.md)
> **Status:** r1 — awaiting adversarial review
> **Execution order:** Task 1 → 2 → 3 → 4 → 5 → 6 → 7(Wave A = 1–3,Wave B = 4–5,Wave C = 6–7)
> **Tech:** Python 3.11+/`.venv/bin/python`(本机为 3.14 venv,系统 python 无依赖);
> 测试 `.venv/bin/python -m pytest <file> -q`;lint `.venv/bin/python -m ruff check src/ tests/`;
> format `.venv/bin/python -m ruff format src/ tests/`;类型 `.venv/bin/python -m mypy src/`。

**Invariants that MUST hold — re-read before each task:**

- **CN 直连隔离:** 设置 `[network].proxy` 后,bilibili/douyin/ollama(provider、diagnostics、
  supervisor)/image_cache CN 分支的 httpx 构造不含该代理且 `trust_env=False` 不变。
- **空值零漂移:** `network.proxy == ""` 时所有被改造构造点不注入任何代理相关参数,
  行为与改造前 byte-equivalent。
- **保存时拒绝非法值:** scheme 白名单 `{http, https, socks5, socks5h}` + 必须有 host;
  PUT 非法 → 400 且 config.toml 不变;load 非法 → WARNING + 置空,不 crash。
- **失败带真因:** probe 返回错误分类;400 消息为可直显的中文原因。
- **文档同步:** config 模块文档 + changelog 与代码同 PR。

### Task 1: NetworkConfig dataclass、校验与 toml/env 链路

**Files:** modify `src/openbiliclaw/config.py`;test `tests/test_config.py`。

**Interfaces:** Consumes: raw toml dict、`OPENBILICLAW_NETWORK_PROXY`。
Produces: `Config.network: NetworkConfig`、`normalize_outbound_proxy(value) -> str`
(非法抛 `ValueError`,消息中文可直显)。

**Steps:**

- [ ] 在 `tests/test_config.py` 写失败测试:四合法 scheme 通过并规范化(scheme 小写、
      去首尾空白)、`https` 通过、`ftp://` / 缺 host / 纯乱码抛 ValueError、空串直通、
      `[network]` toml round-trip(save→load 保值)、`OPENBILICLAW_NETWORK_PROXY` 覆盖、
      load 非法值→WARNING(caplog)+ `config.network.proxy == ""`。
- [ ] `.venv/bin/python -m pytest tests/test_config.py -q -k network` 确认 FAIL
      (缺属性/缺函数,而非 import error 以外的意外原因)。
- [ ] 实现:`NetworkConfig` dataclass(注释写明"仅海外出口 + spec 链接",阈值/白名单
      的出处写进注释——踩坑规则 3);`normalize_outbound_proxy`(urllib.parse,白名单
      `{http, https, socks5, socks5h}`);`_build_config` 读 `raw.get("network", {})`
      并做 load 侧 WARNING+置空;`_render_config_toml` 渲染 `[network]` 段。
- [ ] 重跑确认 PASS 无警告。
- [ ] `.venv/bin/python -m pytest tests/test_config.py -q && .venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m mypy src/`。

**Acceptance:**

- Numeric gate:新增 ≥8 条断言全绿;`mypy src/` 0 error。
- Reproduce: `.venv/bin/python -m pytest tests/test_config.py -q`;结果记入 PR。

### Task 2: `network.py` helper(进程级单一事实源)

**Files:** add `src/openbiliclaw/network.py`;test `tests/test_network.py`(新)。

**Interfaces:** Consumes: `set_outbound_proxy(url)`(由 config 应用点调用)。
Produces: `outbound_proxy_url() -> str | None`、`outbound_httpx_kwargs() -> dict[str, Any]`
(`{}` 或 `{"proxy": url}`)。

**Steps:**

- [ ] 写失败测试:默认 None/`{}`;set 后返回值正确;set("") 复位;测试间用 fixture 复位
      (提供 `reset_outbound_proxy_for_tests()` 或 autouse fixture,防测试串扰)。
- [ ] 确认 FAIL 后实现(模块级私有变量 + 三函数,≤40 行)。
- [ ] 在 `api/app.py` `create_app()`、`cli.py` 各命令入口的 config 加载点、
      `update_config` 保存成功路径调用 `set_outbound_proxy(config.network.proxy)`
      (本 task 只接 create_app 与 CLI;update_config 在 Task 4)。
- [ ] 重跑 PASS;lint/mypy。

**Acceptance:**

- Numeric gate:helper 测试全绿;grep 确认 `set_outbound_proxy` 在 create_app 与 CLI
  入口各 ≥1 个调用点。
- Reproduce: `.venv/bin/python -m pytest tests/test_network.py -q`。

### Task 3: LLM registry/providers 接线 + CN 隔离守卫测试

**Files:** modify `src/openbiliclaw/llm/registry.py`、`llm/openai_provider.py`、
`llm/claude_provider.py`、`llm/gemini_provider.py`;test `tests/test_llm_registry.py`、
`tests/test_llm_providers.py`、add `tests/test_network_proxy_isolation.py`。

**Interfaces:** Consumes: `network.outbound_proxy_url()`。Produces: 各海外 provider 构造器
新形参 `proxy: str = ""`;OpenAI/Claude 经 `http_client=httpx.AsyncClient(proxy=...)`,
Gemini 经 `http_options["client_args"/"async_client_args"]={"proxy": ...}`。

**Steps:**

- [ ] 失败测试 1(注入):monkeypatch `httpx.AsyncClient` 捕获 kwargs,构造
      OpenAI/Claude provider(proxy 非空)断言 `proxy=` 传入;Gemini 断言 http_options
      含 client_args/async_client_args;`_maybe_openai/claude/gemini/deepseek/openrouter/
      openai_compatible_provider` 在 helper set 后产出带代理的 provider。
- [ ] 失败测试 2(空值零漂移):proxy="" 时 OpenAI/Claude 构造 **不传** `http_client`
      键、Gemini http_options 无 client_args 键。
- [ ] 失败测试 3(CN 隔离守卫,`tests/test_network_proxy_isolation.py`):set 全局代理后
      monkeypatch `httpx.AsyncClient.__init__` 捕获,分别驱动 `BilibiliAPIClient` 会话
      构造、`douyin_direct` client 构造、`OllamaProvider` 请求路径、`_maybe_ollama_provider`,
      断言捕获集中无 `proxy` 与全局代理值相等的项,且 `trust_env=False`。
- [ ] 逐一确认 FAIL(守卫测试需临时把代理接进一个 CN 构造点验证"能变红",再还原)。
- [ ] 实现 registry 工厂与三个 provider 构造器;`_maybe_ollama_provider` 不读 helper。
- [ ] 重跑三组测试 PASS;跑 `tests/test_llm_providers.py` 全量回归;lint/mypy。

**Acceptance:**

- Numeric gate:6 个海外工厂路径 + 3 个空值路径 + ≥4 个 CN 隔离断言全绿。
- Reproduce: `.venv/bin/python -m pytest tests/test_llm_registry.py tests/test_llm_providers.py tests/test_network_proxy_isolation.py -q`。
- 守卫测试的 FAIL-first 验证过程在 PR 里写一句话记录。

### Task 4: API 暴露(GET/PUT/400/热重载/遮蔽)

**Files:** modify `src/openbiliclaw/api/models.py`、`src/openbiliclaw/api/app.py`;
test `tests/test_api_config_guards.py`。

**Interfaces:** Consumes: `ConfigUpdateIn.network`(dict)。Produces:
`ConfigResponse.network: NetworkConfigOut`;非法值 400 + 中文原因;保存成功后
`set_outbound_proxy` + LLM runtime 热重建;GET 对 URL userinfo 段 `***` 遮蔽,
PUT 收到遮蔽回显视为未修改(复用 `_is_masked_echo` 语义)。

**Steps:**

- [ ] 失败测试:GET 含 network 且 userinfo 被遮蔽(`socks5://u:p@h:1` →
      `socks5://***@h:1` 之类);PUT 合法值落盘且响应携带;PUT `ftp://x` → 400、
      toml 内容不变(读文件对比);PUT 遮蔽回显 → 保留旧值;PUT 成功后
      `outbound_proxy_url()` 已更新。
- [ ] 确认 FAIL;实现 models + `_config_to_response` + `update_config` network 分支
      (结构对齐既有 bilibili 分支 `app.py:9536` 附近;ValueError 文案透传为 400 detail)。
- [ ] 确认 update_config 现有的 runtime 重建路径会重跑 registry 工厂;若不覆盖 LLM
      provider 重建,补最小重建调用并加断言测试。
- [ ] 重跑 PASS;跑 `tests/test_api_config_transactional.py` 回归;lint/mypy。

**Acceptance:**

- Numeric gate:上述 5 类断言全绿;transactional 回归 0 失败。
- Reproduce: `.venv/bin/python -m pytest tests/test_api_config_guards.py tests/test_api_config_transactional.py -q`。

### Task 5: 桌面 Web UI + probe 代理探测

**Files:** modify `src/openbiliclaw/web/desktop/index.html`、
`src/openbiliclaw/web/desktop/assets/js/app.js`、`src/openbiliclaw/api/app.py`
(probe)、`src/openbiliclaw/api/models.py`(probe 模型);
test `tests/test_api_config_probe.py`、`tests/test_desktop_web_config_probe.py`。

**Interfaces:** Consumes: 设置-通用面板(`index.html:580`)、`restoreSettings`/collect
payload 既有模式。Produces: 输入框 + hint(「仅作用于海外 AI 服务/YouTube/更新检查;
B 站等国内请求始终直连」)+「测试」按钮;probe 目标 `network_proxy`:经待测代理 GET
`https://www.gstatic.com/generate_204`,5s 超时,返回 ok/`proxy_unreachable`/
`proxy_rejected`/`timeout` 分类,不写盘。

**Steps:**

- [ ] 失败测试(probe 后端):mock transport——204 → ok;connect 拒绝 →
      `proxy_unreachable`;超时 → `timeout`;非法 URL → 400。
- [ ] 确认 FAIL;实现 probe 分支(探测用 client 显式 `trust_env=False` + 待测 proxy,
      不受进程 env 干扰)。
- [ ] 前端:index.html 通用面板加 `settings-field`(input id、hint、测试按钮);
      app.js restore 加 `setInput`,collect 加 `payload.network`,400 文案走既有错误
      展示;probe 按钮沿用既有 probe 交互模式。
- [ ] `tests/test_desktop_web_config_probe.py` 模式适用则补前端契约断言;不适用则在
      PR 记录手工 E2E:非法值 400 文案可见、合法值保存后 `openbiliclaw config-show`
      可见、测试按钮三态。
- [ ] 全部重跑 PASS;lint/mypy。

**Acceptance:**

- Numeric gate:probe 4 分类断言全绿;手工 E2E 3 项通过并记录在 PR。
- Reproduce: `.venv/bin/python -m pytest tests/test_api_config_probe.py tests/test_desktop_web_config_probe.py -q`。

### Task 6: 长尾挂载 — yt-dlp / codex_auth / updater

**Files:** modify `src/openbiliclaw/youtube/client.py`、`src/openbiliclaw/llm/codex_auth.py`、
`src/openbiliclaw/runtime/updater.py`;test `tests/test_youtube_producer.py`(或
youtube 既有测试归位处)、`tests/test_runtime_updater.py`。

**Interfaces:** Consumes: `network.outbound_httpx_kwargs()` / `outbound_proxy_url()`。
Produces: YoutubeDL options 经统一 `_ytdlp_options()` helper 注入 `"proxy"`;
codex_auth 与 updater 的 AsyncClient 构造点注入 kwargs(与 `verify_tls` 共存)。

**Steps:**

- [ ] 失败测试:set 代理后 `_ytdlp_options()` 含 `proxy`,空值不含;updater client
      构造捕获 kwargs 含 proxy 且 `verify` 参数不受影响;codex_auth 构造点同理。
- [ ] 确认 FAIL;实现(youtube 两处 `:109,167` 收敛到 helper;updater 文件内逐个
      AsyncClient 构造点核对——GitHub 元数据与资产下载两类都要覆盖)。
- [ ] 重跑 + `tests/test_runtime_updater.py` 全量回归;lint/mypy。

**Acceptance:**

- Numeric gate:3 个挂载点 × (注入+空值) = 6 断言全绿;updater 回归 0 失败。
- Reproduce: `.venv/bin/python -m pytest tests/test_runtime_updater.py tests/test_youtube_producer.py -q`。

### Task 7: 文档同步

**Files:** modify `docs/modules/config.md`、`docs/changelog.md`;核对 `docs/modules/llm.md`
是否列 provider 构造签名(列则同步)。

**Steps:**

- [ ] `docs/modules/config.md` 增 `[network]` 段:字段表、白名单、"仅海外出口"语义、
      与 `[bilibili].proxy` 的区别说明。
- [ ] `docs/changelog.md` 当前版本块加 bullet(引用 issue #89)。
- [ ] 核对 `docs/modules/llm.md` 公共 API 表;架构图确认不触发(spec 已论证)。
- [ ] 全量收尾:`.venv/bin/python -m pytest -q`(全量)、ruff、mypy。

**Acceptance:**

- Numeric gate:全量 pytest 0 failure;文档 3 项核对完成。
- Reproduce: `.venv/bin/python -m pytest -q`。

## Verification after merge

发版后(或 main 部署到本机 8420 正式后端后)观察:在设置-通用配置本机代理
(如 `http://127.0.0.1:7890`),触发一轮 discovery,确认 `openbiliclaw.log` 中海外 LLM
调用成功且无 B 站请求异常/风控日志;观察 24h。Owner: white。回滚触发条件:B 站侧出现
v_voucher/风控激增或 LLM 调用成功率下降 → 设置清空代理立即恢复(空值零漂移保证),
必要时 revert PR。注意 8420 正式后端重启需遵循本机 serve-api 拓扑(先查端口占用)。

## Explicitly out of scope

- `[bilibili].proxy` 暴露到 UI(独立 issue)。
- extension popup 内设置代理;openclaw aiohttp 集成。
- 按 provider 粒度代理、PAC、代理凭据管理 UI。
- README 📌 highlights(随下次发版评估)。
