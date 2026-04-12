import { execSync } from "node:child_process";
import { createWriteStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { basename, resolve } from "node:path";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";
import { Readable } from "node:stream";

/**
 * Package the extension into a .zip for Chrome Web Store or sideloading.
 *
 * Usage:
 *   node scripts/package.mjs          # build + zip
 *   node scripts/package.mjs --no-build   # zip only (skip build)
 */

const root = resolve(import.meta.dirname, "..");
const skipBuild = process.argv.includes("--no-build");

// --- 1. Build ---------------------------------------------------------
if (!skipBuild) {
  console.log("Building extension...");
  execSync("npm run build", { cwd: root, stdio: "inherit" });
}

// --- 2. Read version from manifest ------------------------------------
const manifest = JSON.parse(
  await readFile(resolve(root, "manifest.json"), "utf-8"),
);
const version = manifest.version;
const outName = `openbiliclaw-extension-v${version}.zip`;
const outPath = resolve(root, outName);

// --- 3. Collect files to include --------------------------------------
// Only ship what the browser needs: manifest, dist/, icons/, popup/
const includes = ["manifest.json", "dist", "icons", "popup"];

console.log(`\nPackaging ${outName}...`);
execSync(
  `cd "${root}" && zip -r -9 "${outPath}" ${includes.join(" ")}`,
  { stdio: "inherit" },
);

// --- 4. Report --------------------------------------------------------
const stats = await stat(outPath);
const sizeKB = (stats.size / 1024).toFixed(1);
console.log(`\nDone: ${outName} (${sizeKB} KB)`);
