# Target Frontend Workspace

`frontend/` 是目标 Presentation Contract 的 npm workspace。共享契约、responsive Vue web shell 与精简 extension shell 已落地。

## 已落地

- 根 workspace 声明 `packages/api-client`、`packages/presentation`、`apps/web`、`apps/extension`，四个 package 的依赖一次写入 lockfile。
- TypeScript 开启 `strict`、`allowJs=false`、`noUncheckedIndexedAccess`、`exactOptionalPropertyTypes`、`noImplicitOverride`、`useUnknownInCatchVariables`。
- `api-client/generated/schema.ts` 由 deterministic FastAPI OpenAPI 经 workspace 固定的 `openapi-typescript@7.10.1` 生成；`scripts/generate_api_client.py` 不下载临时工具。
- `ApiClient` 通过 caller-supplied type guard 验证未知 JSON；HTTP、network、invalid response 为安全 typed failure；SSE event envelope 在边界做 runtime validation。
- Presentation package 复用 generated API projection types，定义 provider view、card descriptor/action、pagination 与 availability UI contract。
- video/image/article/discussion/fallback Vue cards 共用可访问 outer frame、反馈 controls 与 provider/status 呈现。URL/media 仅允许 HTTP(S)，文本由 Vue escaping；renderer 只接受 build-time `generic` 标识，不接受 backend HTML/CSS/component name/code。
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

Pinia 按 durable concern 拆为 session、sources、recommendations、content、profile、assistant、runtime/jobs 与 host-local preferences；每个 server store 明确暴露 idle/loading/success/empty/error，使用 AbortController 取消旧请求。Runtime store 是 event stream 的唯一 owner，采用 100ms→500ms→1s→2s bounded reconnect backoff，并保留最多 50 个 event envelope。Profile edit 等 mutation 不做无 rollback 的 optimistic update，服务端响应始终 authoritative。

可访问性包括 skip link、desktop/mobile nav labels、`aria-current`、form labels、loading/error live announcements、route heading focus、Alt+Left keyboard navigation、明显 focus ring、high-contrast palette 以及系统/用户 reduced-motion policy。Store tests 不 mount app；component tests覆盖 navigation、announcements 和 keyboard path。

## Extension shell 与 packaging

`apps/extension` 通过 shared typed API client 连接 `127.0.0.1:8420`，使用 shared presentation fallback card，并只持久化 backend URL 与 opaque device token。popup 通过 shared client 直接请求本地后端，不经过 `chrome.runtime` message relay；它不注册 background/content script、不申请 storage/Cookie 权限、不采集页面行为、不执行 provider task。Chrome/Firefox manifest 使用 module service worker；Python release tool 将生成物统一输出到 ignored `artifacts/extension/`。Web Vite artifact 由 target host 作为 SPA 提供，installer 与 Docker pipeline 都会先构建该 artifact。
