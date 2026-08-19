# Extension

The current extension source is in `frontend/apps/extension/` and is built with Vue 3, Pinia, TypeScript, and Vite. `extension/` contains only Chrome/Firefox manifests, icons, and declarative store/package metadata; it is no longer a source workspace.

## Scope

The extension retains presentation/host and generic credential-capture capabilities:

- the popup/sidebar configures the loopback backend URL and opaque extension token and displays bounded connection state;
- it uses the shared presentation contract through the typed backend API;
- it discovers provider access recipes built into the backend, and after the user approves each origin, reads only the Cookie or local/session-storage values named by the recipe and sends them only to that loopback backend with the extension token.

The extension contains no provider-specific branches, remote provider-task code, background browsing automation, arbitrary page content/behavior collection, or third-party credential transmission. Every provider-domain permission is an optional host permission that the user must approve when connecting.

## API and recipe boundary

`popup/access-flow.ts` performs bounded structural validation of `/v1/sources` and `/v1/sources/{id}/access-recipe` responses and rejects non-canonical domains, undeclared artifacts, non-HTTPS warmup URLs, and unknown artifact kinds. `POST /v1/sources/{id}/access-material` accepts only material declared by the frozen recipe. Requests carry the opaque extension bearer token, and the target URL is built from the validated loopback connection setting. The extension uses no runtime/window message protocol.

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
