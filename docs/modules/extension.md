# Extension（目标 Vue shell）

扩展当前源码位于 `frontend/apps/extension/`，由 Vue 3、Pinia、TypeScript 与 Vite 构建。`extension/` 仅保留 Chrome/Firefox manifest、图标和商店元数据；不再是源码/package workspace。

## Scope

扩展保留两项 presentation/host 能力：

- popup/sidebar 配置 loopback backend URL 与 opaque device token；
- 显示 bounded connection state，并通过 typed backend API 使用共享 presentation contract。

浏览器站点 Cookie、登录态抓取、MAIN-world tap、provider task dispatch、browser-session execution 与跨站行为采集已删除。未来浏览器观察必须通过 Plan 08 文档中的签名/device-authenticated `ObservationProvider` 契约重新进入，不能在 presentation shell 中恢复。

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


## Legacy contract disposition

This cutover intentionally removes the former native-save/session paths. Historical status keys such as `unsupported_content_type` remain documented in `docs/modules/saved-sync.md`, but the target extension does not execute them. 登录态只存在于已安装扩展 was the legacy rule; the target extension stores no website login state at all. It is not a backend bypass of browser login state（不是后端绕过浏览器登录态）because the capability was removed rather than emulated.

User releases are published on the `openbiliclaw-v*` aggregate page. Maintainer component tags remain `extension-v*` / `desktop-v*` / `backend-v*`; 后端源码更新仍只通过 `backend-v*` tag 标记.


Historical native-save statuses (`synced`, `already_synced`, `login_required`, `rate_limited`, `unsupported_content_type`, `unsupported_adapter_missing`, `extension_required`, `failed`) are retained only for audit/document links; the target shell emits none of them. The legacy exact authorization remains documented as 精确命名授权. 桌面安装包仍由 `desktop-v*` workflow 构建.
Historical saved-state rendering was 后端状态驱动; that responsibility now belongs to the shared target web/presentation host rather than this extension shell.
