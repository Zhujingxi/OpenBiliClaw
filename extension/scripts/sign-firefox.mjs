import { execFileSync, execSync } from "node:child_process";
import { createHmac, randomUUID } from "node:crypto";
import { cp, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import {
  makeFirefoxSignedXpiName,
  normalizeReleaseVersion,
} from "./release-utils.mjs";

/**
 * Submit the Firefox build to Mozilla AMO for unlisted signing.
 *
 * Usage:
 *   AMO_JWT_ISSUER=... AMO_JWT_SECRET=... node scripts/sign-firefox.mjs
 *   AMO_JWT_ISSUER=... AMO_JWT_SECRET=... node scripts/sign-firefox.mjs --no-build
 *
 * The output .xpi is signed by Mozilla and can be installed directly in
 * regular Firefox Release/Beta builds. The unsigned -firefox.zip remains only
 * for about:debugging temporary loading or AMO submission input.
 */

const root = resolve(import.meta.dirname, "..");
const distDir = resolve(root, "dist-firefox");
const artifactsDir = resolve(root, "web-ext-artifacts", "firefox-signed");
const skipBuild = process.argv.includes("--no-build");
const archiveVersionFlag = process.argv.indexOf("--archive-version");
const archiveVersionInput =
  archiveVersionFlag === -1 ? null : process.argv[archiveVersionFlag + 1];

if (archiveVersionFlag !== -1 && !archiveVersionInput) {
  throw new Error("--archive-version requires a value");
}

const apiKey = process.env.AMO_JWT_ISSUER;
const apiSecret = process.env.AMO_JWT_SECRET;

if (!apiKey || !apiSecret) {
  throw new Error(
    "Firefox signing requires AMO_JWT_ISSUER and AMO_JWT_SECRET environment variables",
  );
}

if (!skipBuild) {
  console.log("Building Firefox extension before signing...");
  execSync("npm run build:firefox", { cwd: root, stdio: "inherit" });
}

const manifest = JSON.parse(await readFile(resolve(root, "manifest.json"), "utf-8"));
const version = normalizeReleaseVersion(archiveVersionInput ?? manifest.version);
const outName = makeFirefoxSignedXpiName(version);
const outPath = resolve(root, outName);

await rm(artifactsDir, { recursive: true, force: true });
await rm(outPath, { force: true });

function amoJwt() {
  const b64u = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const unsigned = `${b64u({ alg: "HS256", typ: "JWT" })}.${b64u({
    iss: apiKey,
    jti: randomUUID(),
    iat: now,
    exp: now + 300,
  })}`;
  const signature = createHmac("sha256", apiSecret).update(unsigned).digest("base64url");
  return `${unsigned}.${signature}`;
}

async function amoGet(url) {
  const response = await fetch(url, { headers: { Authorization: `JWT ${amoJwt()}` } });
  if (!response.ok) {
    throw new Error(`AMO API ${url} responded ${response.status}`);
  }
  return response.json();
}

/**
 * Recover the signed .xpi when a previous `web-ext sign` run uploaded this
 * exact version but died before collecting the artifact (e.g. an AMO 503
 * during the validation poll). AMO refuses to accept the version number
 * again, so the only way forward with the same version is to download the
 * already-signed file it holds.
 */
async function recoverSignedXpiFromAmo(geckoId) {
  // AMO stores the raw manifest version ("0.3.174"); `version` carries the
  // archive-name normalization ("v0.3.174"), so strip the prefix for lookup.
  const amoVersion = version.replace(/^v/, "");
  const listUrl =
    "https://addons.mozilla.org/api/v5/addons/addon/" +
    `${encodeURIComponent(geckoId)}/versions/?filter=all_with_unlisted&page_size=50`;
  const maxAttempts = 10;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const page = await amoGet(listUrl);
    const match = (page.results ?? []).find((entry) => entry.version === amoVersion);
    const file = match?.file;
    if (file?.url && file.status === "public") {
      console.log(`AMO already holds signed build for ${version}; downloading...`);
      const download = await fetch(file.url, {
        headers: { Authorization: `JWT ${amoJwt()}` },
      });
      if (!download.ok) {
        throw new Error(`AMO signed file download responded ${download.status}`);
      }
      const bytes = Buffer.from(await download.arrayBuffer());
      if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
        throw new Error("AMO download is not a zip archive (unexpected content)");
      }
      await mkdir(artifactsDir, { recursive: true });
      await writeFile(resolve(artifactsDir, `recovered-${version}.xpi`), bytes);
      return;
    }
    const state = match ? `file status: ${match.file?.status ?? "missing"}` : "version not listed";
    console.log(
      `AMO signed build for ${version} not ready (${state}); ` +
        `attempt ${attempt}/${maxAttempts}, retrying in 30s...`,
    );
    await new Promise((resolveSleep) => setTimeout(resolveSleep, 30_000));
  }
  throw new Error(
    `AMO never exposed a signed public file for version ${version}; ` +
      "check the submission state in the AMO developer hub",
  );
}

console.log(`\nSigning Firefox extension as unlisted AMO package...`);
try {
  execFileSync(
    process.platform === "win32" ? "npx.cmd" : "npx",
    [
      "web-ext",
      "sign",
      "--channel=unlisted",
      `--source-dir=${distDir}`,
      `--artifacts-dir=${artifactsDir}`,
      `--api-key=${apiKey}`,
      `--api-secret=${apiSecret}`,
    ],
    { cwd: root, stdio: "inherit" },
  );
} catch (signError) {
  console.warn(
    "web-ext sign failed; checking whether AMO already holds a signed " +
      `build for ${version} (interrupted earlier upload)...`,
  );
  const firefoxManifest = JSON.parse(
    await readFile(resolve(root, "manifest.firefox.json"), "utf-8"),
  );
  const geckoId = firefoxManifest?.browser_specific_settings?.gecko?.id;
  if (!geckoId) {
    throw signError;
  }
  await recoverSignedXpiFromAmo(geckoId);
}

const signedFiles = (await readdir(artifactsDir))
  .filter((entry) => entry.endsWith(".xpi"))
  .sort();

if (signedFiles.length !== 1) {
  throw new Error(
    `Expected exactly one signed Firefox .xpi in ${artifactsDir}, found ${signedFiles.length}`,
  );
}

await cp(resolve(artifactsDir, signedFiles[0]), outPath);

const stats = await stat(outPath);
const sizeKB = (stats.size / 1024).toFixed(1);
console.log(`\nDone: ${outName} (${sizeKB} KB)`);
