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

The target extension has no cross-site provider execution, Cookie extraction, behavior collection, or task dispatch. Its manifest requests only local backend host access and storage. AMO credentials are read from `AMO_JWT_ISSUER` / `AMO_JWT_SECRET` by `scripts/extension_release.py` and are never written to artifacts or logs.
