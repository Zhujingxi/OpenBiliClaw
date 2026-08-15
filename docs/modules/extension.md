# Extension

扩展当前源码位于 `frontend/apps/extension/`，由 Vue 3、Pinia、TypeScript 与 Vite 构建。`extension/` 仅保留 Chrome/Firefox manifest、图标和商店元数据；不再是源码/package workspace。

## Scope

扩展保留 presentation/host 与通用凭据抓取能力：

- popup/sidebar 配置 loopback backend URL 与 opaque extension token，并显示 bounded connection state；
- 通过 typed backend API 使用共享 presentation contract；
- 从后端发现代码内置的 provider access recipe，在用户逐 origin 批准后，只读取 recipe 点名的 Cookie 或 local/session-storage 值，并仅携带 extension token 回传到该 loopback backend。

扩展不包含 provider-specific 分支、远程 provider task 代码、后台浏览自动化、任意页面内容/行为采集或第三方凭据传输。每个 provider 域名权限都是 optional host permission，必须由用户在连接时批准。

## API and recipe boundary

`popup/access-flow.ts` 对 `/v1/sources` 与 `/v1/sources/{id}/access-recipe` 响应执行 bounded 结构校验，并拒绝非规范域名、未声明 artifact、非 HTTPS warmup URL 与未知 artifact kind。`POST /v1/sources/{id}/access-material` 只接受该冻结 recipe 声明的材料；请求携带 opaque extension bearer token，目标 URL 由已验证的 loopback connection setting 构造。扩展不使用 runtime/window message protocol。

## Build and package

```bash
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
python scripts/extension_release.py package --no-build
python scripts/extension_release.py package --firefox --no-build
```

Vite output under `frontend/apps/extension/dist/` is ignored generated JavaScript. Python packaging copies it with declarative manifests/icons into `artifacts/extension/` and creates release archives. Store status/sign/upload commands use `scripts/extension_release.py`; credentials are environment-only and never logged.


## Removed capabilities

Native-save/session execution, website login state, provider tasks, and historical saved-state rendering are not extension capabilities. They were deleted rather than emulated; the shared Web/presentation host owns retained product state.

User releases are published on the `openbiliclaw-v*` aggregate page. Maintainer component tags remain `extension-v*`, `desktop-v*`, and `backend-v*`; backend source updates use `backend-v*`.
