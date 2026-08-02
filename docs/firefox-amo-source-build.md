# Firefox AMO source build instructions

This archive contains the complete human-readable source needed to reproduce the Firefox
package submitted to Mozilla Add-ons. No commercial or private build tools are required.

## Build environment

- Ubuntu 24.04 LTS (the GitHub Actions runner used for submission)
- Node.js 22
- npm, using the committed `extension/package-lock.json`

## Reproduce the submitted package

From the archive root:

```bash
cd extension
npm ci
npm run build:firefox
```

The reviewed extension is written to `extension/dist-firefox/`. The build uses TypeScript and
the open-source esbuild package declared in `extension/package.json`; it bundles readable
TypeScript entry points and copies the shared browser UI module from
`src/openbiliclaw/web/shared/`. Source maps are included in the submitted extension.

The submission workflow packages this source directly from the same Git commit, builds
`dist-firefox`, synchronizes `docs/privacy.md`, and invokes `web-ext sign --channel=listed`.
