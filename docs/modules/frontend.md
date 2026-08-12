# Target Frontend Workspace（Phase A）

`frontend/` 是目标 Presentation Contract 的 npm workspace；当前仅落地共享契约与组件，尚未替换生产 web / extension shell。

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

## 明确未落地

Web views/stores/router 属于 Phase 14b；extension shell、legacy JS 删除与 packaging 属于 Phase 14c。两个 app 仅有可编译 placeholder，不能作为产品 UI 使用。Pinia stores、event reconnection ownership、browser E2E 也等待各 host phase。
