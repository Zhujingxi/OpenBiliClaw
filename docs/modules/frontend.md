# Target Frontend Workspace

`frontend/` 是 Presentation Contract 的 npm workspace。共享契约、responsive Vue web shell 与精简 extension shell 已落地。

## 已落地

- 根 workspace 声明 `packages/api-client`、`packages/presentation`、`apps/web`、`apps/extension`，四个 package 的依赖一次写入 lockfile。
- TypeScript 开启 `strict`、`allowJs=false`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`、`noImplicitOverride`、`useUnknownInCatchVariables`。
- `api-client/generated/schema.ts` 由 deterministic FastAPI OpenAPI 经 workspace 固定的 `openapi-typescript@7.10.1` 生成；`scripts/generate_api_client.py` 不下载临时工具。
- `ApiClient` 通过 caller-supplied type guard 验证未知 JSON；HTTP、network、invalid response 为安全 typed failure，typed API error envelope 的安全 message 会传给 UI；SSE event envelope 在边界做 runtime validation，并保留 server `retry:` hint 给 reconnect owner。浏览器 mutation 包括 generated `/v1/feedback` operation，并统一附带 device/CSRF headers。
- Presentation package 复用 generated API projection types，定义 provider view、card descriptor/action、pagination 与 availability UI contract。
- video/image/article/discussion/fallback Vue cards 共用可访问 outer frame、可触发 `like`/`dismiss` event 的反馈 controls 与 provider/status 呈现。Web recommendation store 把 feed `shown_id` 保留在 card view model 中，使用原生 `IntersectionObserver` 记录 viewport exposure，并在 dismiss feedback 携带 `exposed`；等待 `/v1/feedback` 成功后才显示结果，404/409 delivery expiry 作为可见错误呈现。Canonical links 仅允许 HTTP(S)；card 与 content-detail 的 HTTPS image URL 一律重写为同源 `/v1/media?url=…`，浏览器不直接请求 provider CDN（无 Referer/访问 IP 泄漏，provider hotlink header 由 backend manifest 提供）。文本由 Vue escaping；renderer 只接受 build-time `generic` 标识，不接受 backend HTML/CSS/component name/code。
- Python CI test 扫描新 workspace，拒绝 checked-in `.js`/`.mjs`/`.cjs`；所有 `dist/` 均在 `.gitignore`。

## 开发命令

```bash
npm --prefix frontend install
PYTHONPATH=src:. .venv/bin/python scripts/generate_api_client.py
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
```

生成的 `schema.ts` 可提交、必须可重现；Vite `dist/` 是 ignored build output，不提交。

## Web shell（Phase 14b）

`apps/web/` 是单一 responsive Vue app，而非两套 desktop/mobile 应用。desktop sidebar 和 mobile bottom navigation 保持不同密度与导航方式，共享 recommendations、provider tabs、search/content detail、profile、Assistant、source connection、settings 和 runtime health views。

Pinia 按 durable concern 拆为 session、sources、recommendations、content、profile、assistant、runtime/jobs、models catalog/configuration、auth 与 host-local preferences；每个 server store 明确暴露 idle/loading/success/empty/error，使用 AbortController 取消旧请求。auth store 拥有启动 probe（200 无 token = 未启用密码；401 → `#/login`；其他失败 fail-closed 到 login）、token 持久化（localStorage）与 authenticatedFetch wrapper（逐请求注入 `Authorization: Bearer`，任何 401 清 token 并跳 login；wrapper 绑定 globalThis receiver，避免 Chrome detached-fetch "Illegal invocation"）。Login 路由不受 guard 保护，其余 hash route 在 auth required 时重定向到 `#/login`。Content search/detail 使用独立 phase/error/request owner，避免跨 view 状态泄漏；detail hash 携带 JSON `ContentRef` 并在同 route 参数变化时重取。Runtime store 是 event stream 的唯一 owner，优先采用 server `retry:` hint，缺失时使用 100ms→500ms→1s→2s bounded reconnect backoff，并保留最多 50 个 event envelope。Profile edit 等 mutation 不做无 rollback 的 optimistic update，服务端响应始终 authoritative。

Settings 的 Model 区从 `/v1/models/catalog` 搜索/浏览 provider 和 model，显示 catalog protocol/env metadata，支持 endpoint override 与 write-only password key，并以 `secret_configured` 显示已配置状态；保存后明确提示当前需要重启。高级 custom provider 表单要求 protocol、endpoint 和完整 capabilities，和后端 escape hatch 保持一致。加载、空、错误、保存中与保存失败均有可访问状态。

可访问性包括 skip link、desktop/mobile nav labels、`aria-current`、form labels、loading/error live announcements、route heading focus、Alt+Left keyboard navigation、明显 focus ring、high-contrast palette 以及系统/用户 reduced-motion policy。390px mobile navigation uses a three-column wrapping grid and form controls shrink within the viewport. Recommendations expose an explicit refresh action; connection success is view-submission scoped; Assistant shows submitted user text and safely renders model output as escaped plain text with preserved line breaks (only `**` emphasis delimiters are removed—no raw HTML renderer). Stale Assistant conversation IDs are cleared after 404. Store tests 不 mount app；component tests覆盖 navigation、announcements 和 keyboard path。

## Extension shell 与 packaging

`apps/extension` 通过 shared typed API client 连接 `127.0.0.1:8420`，使用 shared presentation fallback card，并只持久化 backend URL 与 opaque device token。popup 通过 shared client 直接请求本地后端，不经过 `chrome.runtime` message relay；它不注册 background/content script、不申请 storage/Cookie 权限、不采集页面行为、不执行 provider task。Chrome/Firefox manifest 使用 module service worker；Python release tool 将生成物统一输出到 ignored `artifacts/extension/`。Web Vite artifact 由 production host 作为 SPA 提供，installer 与 Docker pipeline 都会先构建该 artifact。SPA `index.html`/route fallback 使用 `Cache-Control: no-cache`，而 Vite `/assets/*` fingerprint artifact 使用一年 immutable cache，避免 rebuild 后旧 HTML 继续引用 stale bundle。
