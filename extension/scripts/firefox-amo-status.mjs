import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { amoRequest } from "./amo-api.mjs";

const root = resolve(import.meta.dirname, "../..");
const manifest = JSON.parse(
  await readFile(resolve(root, "extension/manifest.firefox.json"), "utf8"),
);
const packageManifest = JSON.parse(
  await readFile(resolve(root, "extension/manifest.json"), "utf8"),
);
const geckoId = manifest?.browser_specific_settings?.gecko?.id;
if (!geckoId) {
  throw new Error("Firefox manifest is missing browser_specific_settings.gecko.id");
}

const versionFlag = process.argv.indexOf("--version");
const version = versionFlag === -1 ? packageManifest.version : process.argv[versionFlag + 1];
if (!version) {
  throw new Error("--version requires a value");
}

const listPath =
  `addons/addon/${encodeURIComponent(geckoId)}/versions/` +
  "?filter=all_with_unlisted&page_size=50";
const maxAttempts = 10;
let match = null;
for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
  const page = await amoRequest(listPath);
  match = (page?.results ?? []).find((entry) => entry.version === version);
  if (match?.channel === "listed") break;
  if (attempt < maxAttempts) {
    console.log(`AMO listed version ${version} not visible yet; retry ${attempt}/${maxAttempts}`);
    await new Promise((resolveSleep) => setTimeout(resolveSleep, 15_000));
  }
}
if (!match) {
  throw new Error(`AMO version ${version} was not found after submission`);
}
if (match.channel !== "listed") {
  throw new Error(`AMO version ${version} has channel ${match.channel}, expected listed`);
}

const addon = await amoRequest(`addons/addon/${encodeURIComponent(geckoId)}/`);
console.log(
  JSON.stringify(
    {
      addonStatus: addon?.status,
      channel: match.channel,
      fileStatus: match.file?.status,
      reviewUrl: addon?.review_url,
      version: match.version,
    },
    null,
    2,
  ),
);
