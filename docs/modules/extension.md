# Extension

扩展当前源码位于 `frontend/apps/extension/`，由 Vue 3、Pinia、TypeScript 与 Vite 构建。`extension/` 仅保留 Chrome/Firefox manifest、图标和商店元数据；不再是源码/package workspace。

## Scope

扩展保留两项 presentation/host 能力：

- popup/sidebar 配置 loopback backend URL 与 opaque device token；
- 显示 bounded connection state，并通过 typed backend API 使用共享 presentation contract。

浏览器站点 Cookie、登录态抓取、MAIN-world tap、provider task dispatch、browser-session execution 与跨站行为采集已删除。未来浏览器观察必须通过签名/device-authenticated `ObservationProvider` 契约重新进入，不能在 presentation shell 中恢复。

## Message boundary

`shared/messages.ts` 定义 closed discriminated union：`connection.get`、`connection.set`、`connection.check`、`connection.status`。所有 runtime/window message 先执行 exact-key、discriminator、bounded string 与 loopback URL validation；未知字段、provider task 和 session payload 被拒绝。

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
