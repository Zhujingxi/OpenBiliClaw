# Firefox AMO source build

The Firefox extension is reproducibly built from the shared target workspace.

## Prerequisites

- Node.js 22 and npm 10
- Python 3.11+

## Build

```bash
npm --prefix frontend ci
npm --prefix frontend run format:check
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
python scripts/extension_release.py package --firefox --no-build
```

Source lives under `frontend/apps/extension/`. Shared generated OpenAPI types and presentation components live under `frontend/packages/`. Declarative Firefox metadata and icons remain under `extension/`. Vite-generated JavaScript is emitted only to ignored `dist/` directories, then the Python release tool copies it into an ignored artifact tree and release ZIP.

The extension has no remote provider task code, browsing-history collection, or background browsing automation. Its generic access flow asks the user for each code-shipped recipe origin, reads only the declared Cookie or local/session-storage names, and submits them only to the configured loopback backend with the opaque extension token. The manifest therefore requests Cookie, scripting, and tab primitives plus optional HTTPS origins; it does not request the browser `storage` permission because connection settings use extension-page `localStorage`. AMO credentials are read from `AMO_JWT_ISSUER` / `AMO_JWT_SECRET` by `scripts/extension_release.py` and are never written to artifacts or logs.
