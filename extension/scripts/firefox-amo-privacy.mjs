import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { amoRequest } from "./amo-api.mjs";

const root = resolve(import.meta.dirname, "../..");
const manifest = JSON.parse(
  await readFile(resolve(root, "extension/manifest.firefox.json"), "utf8"),
);
const privacyPolicy = await readFile(resolve(root, "docs/privacy.md"), "utf8");
const geckoId = manifest?.browser_specific_settings?.gecko?.id;
if (!geckoId) {
  throw new Error("Firefox manifest is missing browser_specific_settings.gecko.id");
}

const path = `addons/addon/${encodeURIComponent(geckoId)}/eula_policy/`;
await amoRequest(path, {
  method: "PATCH",
  body: JSON.stringify({ privacy_policy: { "zh-CN": privacyPolicy } }),
});
const saved = await amoRequest(path);
const savedPolicy = saved?.privacy_policy?.["zh-CN"];
if (typeof savedPolicy !== "string" || savedPolicy.trim() !== privacyPolicy.trim()) {
  throw new Error("AMO privacy policy read-back did not match docs/privacy.md");
}

const digest = createHash("sha256").update(privacyPolicy).digest("hex");
console.log(`AMO privacy policy synchronized (sha256=${digest}, locale=zh-CN)`);
