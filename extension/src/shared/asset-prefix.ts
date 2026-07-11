/**
 * OpenBiliClaw — runtime asset path prefix (Chrome vs Firefox layout).
 *
 * Chrome/Edge load the extension from the repo `extension/` root, so bundles
 * live under `dist/` (e.g. `dist/main/dy-fetch-tap.js`). Firefox packaged /
 * signed builds are zipped from `dist-firefox/` as the extension root, so the
 * same bundles sit at `main/…`, `content/…`, `background/…` WITHOUT the `dist/`
 * prefix (see manifest.firefox.json). Any dynamic `chrome.scripting.executeScript`
 * `files:` path or `chrome.runtime.getURL(...)` for a bundled asset must use the
 * right prefix for the current layout, or injection fails silently on Firefox.
 *
 * The build injects `__OBC_ASSET_PREFIX__` via esbuild `define` ("" for Firefox,
 * "dist/" for Chrome). Node-based unit tests don't run the esbuild define, so we
 * fall back to "dist/" when the symbol is undefined (`typeof` is safe on an
 * undeclared global — it never throws).
 */
declare const __OBC_ASSET_PREFIX__: string | undefined;

export const ASSET_PREFIX: string =
  typeof __OBC_ASSET_PREFIX__ !== "undefined" ? __OBC_ASSET_PREFIX__ : "dist/";
