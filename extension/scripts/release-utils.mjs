// @ts-check

/**
 * Normalize a release tag or bare version string to the canonical `vX.Y.Z`
 * form used for archive names.
 *
 * Accepts inputs like `1.2.3`, `v1.2.3`, or `release-2026-07-19-v1.2.3`
 * (everything after the final `-v` marker is treated as the version).
 *
 * @param {string} tagOrVersion - Tag or bare version string.
 * @returns {string} Version prefixed with `v`.
 */
export function normalizeReleaseVersion(tagOrVersion) {
  if (tagOrVersion.includes("-v")) {
    const [, suffix] = tagOrVersion.split(/-v(.+)/, 2);
    return `v${suffix}`;
  }

  return tagOrVersion.startsWith("v") ? tagOrVersion : `v${tagOrVersion}`;
}

/**
 * Build the Chrome/unsigned Firefox archive file name for a release.
 *
 * @param {string} tagOrVersion - Tag or bare version string.
 * @returns {string} Archive name, e.g. `openbiliclaw-extension-v1.2.3.zip`.
 */
export function makeExtensionArchiveName(tagOrVersion) {
  return `openbiliclaw-extension-${normalizeReleaseVersion(tagOrVersion)}.zip`;
}

/**
 * Build the signed Firefox `.xpi` file name for a release.
 *
 * @param {string} tagOrVersion - Tag or bare version string.
 * @returns {string} XPI name, e.g. `openbiliclaw-extension-v1.2.3-firefox.xpi`.
 */
export function makeFirefoxSignedXpiName(tagOrVersion) {
  return `openbiliclaw-extension-${normalizeReleaseVersion(tagOrVersion)}-firefox.xpi`;
}
