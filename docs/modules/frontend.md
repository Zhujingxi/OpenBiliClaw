# Target Frontend Workspace

`frontend/` 是 Presentation Contract 的 npm workspace。共享契约、responsive Vue web shell 与精简 extension shell 已落地。

## 已落地

- 根 workspace 声明 `packages/api-client`、`packages/presentation`、`apps/web`、`apps/extension`，四个 package 的依赖一次写入 lockfile。
- TypeScript 开启 `strict`、`allowJs=false`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`、`noImplicitOverride`、`useUnknownInCatchVariables`。
- `api-client/generated/schema.ts` 由 deterministic FastAPI OpenAPI 经 workspace 固定的 `openapi-typescript@7.10.1` 生成；`scripts/generate_api_client.py` 不下载临时工具。
- `ApiClient` 通过 caller-supplied type guard 验证未知 JSON；HTTP、network、invalid response 为安全 typed failure，typed API error envelope 的安全 message 会传给 UI；SSE event envelope 在边界做 runtime validation，并保留 server `retry:` hint 给 reconnect owner。浏览器 mutation 包括 generated `/v1/feedback` operation，并统一附带 device/CSRF headers。
- Presentation package 复用 generated API projection types，定义 provider view、card descriptor/action、pagination 与 availability UI contract。
- video/image/article/discussion/fallback Vue cards 共用可访问 outer frame、可触发 `like`/`dismiss` event 的反馈 controls 与 provider/status 呈现。Web recommendation store 把 feed `shown_id` 保留在 card view model 中，使用原生 `IntersectionObserver` 记录 viewport exposure，并在 dismiss feedback 携带 `exposed`；等待 `/v1/feedback` 成功后才显示结果，404/409 delivery expiry 作为可见错误呈现。卡片标题进入携带 encoded `ContentRef` 的站内 detail route，detail 中保留允许 HTTP(S) 的 provider canonical link；1970 epoch sentinel 不作为来源时间显示，空白或仅破折号的 placeholder summary 不渲染。card 与 content-detail 的 HTTPS image URL 一律重写为同源 `/v1/media?url=…`，浏览器不直接请求 provider CDN（无 Referer/访问 IP 泄漏，provider hotlink header 由 backend manifest 提供）。文本由 Vue escaping；renderer 只接受 build-time `generic` 标识，不接受 backend HTML/CSS/component name/code。
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

`apps/web/` 是单一 responsive Vue app，而非两套 desktop/mobile 应用。统一的 warm-neutral semantic token 层固定 card、field、badge、focus、spacing 与 responsive 行为；desktop 使用分组 sidebar + sticky workspace bar，mobile 使用五项 bottom navigation，共享 recommendations、content-source cards、search/content detail、profile、Assistant、layered source setup、settings 和 runtime health views。

Pinia 按 durable concern 拆为 session、sources、recommendations、content、profile、assistant、runtime/jobs、models catalog/configuration、auth 与 host-local preferences；每个 server store 明确暴露 idle/loading/success/empty/error，使用 AbortController 取消旧请求。auth store 拥有 runtime-health 启动 probe（避免 recommendation view cold-load 重复读取；200 无 token = 未启用密码；401 → `#/login`；其他失败 fail-closed 到 login）、token 持久化（localStorage）与 authenticatedFetch wrapper（逐请求注入 `Authorization: Bearer`；只有实际携带 stored token 的 401 才清 token 并跳 login，无 token 的 product-level 401 留给调用 view 显示；wrapper 绑定 globalThis receiver，避免 Chrome detached-fetch "Illegal invocation"）。Login 路由不受 guard 保护，其余 hash route 在 auth required 时重定向到 `#/login`。Content search/detail 使用独立 phase/error/request owner，避免跨 view 状态泄漏；detail hash 携带 JSON `ContentRef` 并在同 route 参数变化时重取。Runtime store 是 event stream 的唯一 owner，优先采用 server `retry:` hint，缺失时使用 100ms→500ms→1s→2s bounded reconnect backoff，并保留最多 50 个 event envelope。Profile edit 等 mutation 不做无 rollback 的 optimistic update，服务端响应始终 authoritative。

Settings 的 Model 区从 `/v1/models/catalog` 搜索/浏览 provider 和 model，以高密度 provider cards + explicit provider/model fields 显示 catalog protocol/env metadata，当前 provider 固定排在结果首位；支持 endpoint override 与 write-only password key。`/v1/models/current` 的 model provider/name、model 与 embedding credential presence、embedding provider/name/endpoint、`reloaded` 与 `restart_required` 在独立 Active runtime card 中显示，保存提示遵循服务端返回状态。高级 custom provider 表单要求 protocol、endpoint 和完整 capabilities，和后端 escape hatch 保持一致；Appearance 是独立 zone。加载、空、错误、保存中与保存失败均有可访问状态。当前 host contract 仍只持久化一个 active chat model；UI 不伪造多-key profile registry。

可访问性包括不遮挡 brand 的 skip link、desktop/mobile nav labels、desktop/mobile 明显的 `aria-current` active treatment、每 route 的 document title、unknown-route fallback notice、form labels、loading/error live announcements、route heading focus、Alt+Left keyboard navigation、明显 focus ring、high-contrast palette 以及系统/用户 reduced-motion policy。390px mobile navigation 固定为五个核心入口，form controls 与 card grids 在 viewport 内收缩。Recommendations 的 secondary refresh action 先调用 supervised `/v1/recommendations/refresh` 再读取 authoritative feed；空 feed/provider/search/profile/content-detail state 给出原因与 Connect/Search CTA。Content sources 用 provider cards 显示 account、connection method 与 capabilities；Connect 将 provider、access method、credential 拆成三个清晰层级，连接前后都在可访问 live status list 显示全部状态。Assistant 是 full-height chat window：完整 server/local turn history 使用角色 bubble，空会话提供 prompt suggestions，等待响应有 typing feedback，turn failure 就地显示，缺少 capability 时链接 Settings，composer 保持在底部并支持 Enter / Shift+Enter；server-hydrated structured output 与 live output 使用同一安全 plain-text projection，不显示 persisted raw JSON 或 opaque recommendation ID。Runtime 显示 health badge、检查时间、supervised jobs、可展开 recent events 和手动 refresh。Store/component/view tests 覆盖上述状态与 navigation/keyboard path。

## Extension shell 与 packaging

`apps/extension` 连接 loopback backend，并只持久化 backend URL 与 `openbiliclaw ext-token` 生成的 opaque token。popup 不经过 provider-specific background/content script：它从 `/v1/sources` 枚举 provider、对每个 provider 请求 declarative recipe（404 即跳过），按 recipe 动态请求 HTTPS optional host permission，用 generic `chrome.cookies` 或 tab-scoped `chrome.scripting` 读取明确声明的 cookie/local-storage/session-storage key，再携 bearer + device/CSRF headers 提交 material。manifest 只申请 generic `cookies`/`scripting`/`tabs`/`storage` primitives 与 `https://*/*` optional host permission；没有 provider ID、cookie name、刷新调度、in-page fetch/signing 或行为采集。Python release tool 将生成物统一输出到 ignored `artifacts/extension/`。Web Vite artifact 由 production host 作为 SPA 提供，installer 与 Docker pipeline 都会先构建该 artifact。SPA `index.html`/route fallback 使用 `Cache-Control: no-cache`，而 Vite `/assets/*` fingerprint artifact 使用一年 immutable cache，避免 rebuild 后旧 HTML 继续引用 stale bundle。
