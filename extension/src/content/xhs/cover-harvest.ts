/**
 * Harvest xhs cover-image bytes in the page context at scrape time.
 *
 * Why the extension fetches covers at all: ``sns-webpic-qc.xhscdn.com``
 * rejects non-browser clients (TLS-fingerprint hotlink protection, observed
 * 2026-07 — curl/httpx get 403 on fresh-token URLs regardless of headers,
 * while an in-page fetch returns 200 and the CDN serves CORS-readable
 * bytes). The backend can therefore never fetch these covers itself; the
 * user's browser, on the page where the card was scraped, is the only place
 * the fetch succeeds — and the ``{timestamp}/{token}`` URL prefix is
 * freshest at exactly that moment. The harvested bytes ride the existing
 * note-metadata payloads as base64 and land in the backend's disk image
 * cache (``save_extension_cover``), which serves them forever after via
 * ``/api/image-proxy`` (the cache key ignores the rotating token).
 *
 * Best-effort by design: any fetch failure, oversize image, or non-image
 * response just leaves the note without ``cover_data`` — the note itself
 * must never be delayed or dropped because of its cover.
 */

import type { XhsNoteMetadata } from "./passive.js";

/** Upload ceiling — mirrors the backend's MAX_EXTENSION_COVER_BYTES (1MB). */
export const MAX_COVER_BYTES = 1 * 1024 * 1024;

/** Per-batch cap so one scrape never fans out into dozens of CDN fetches. */
export const MAX_COVERS_PER_BATCH = 12;

const FETCH_TIMEOUT_MS = 4000;

/** Hosts whose covers are worth harvesting (token-rotating, backend-unfetchable). */
const HARVEST_HOST_RE = /(^|\.)xhscdn\.com$/i;

/** Chunked ArrayBuffer→base64 (String.fromCharCode has an argument limit). */
export function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

export function isHarvestableCoverUrl(raw: string): boolean {
  try {
    const url = new URL(raw);
    return (
      (url.protocol === "https:" || url.protocol === "http:") &&
      HARVEST_HOST_RE.test(url.hostname)
    );
  } catch {
    return false;
  }
}

async function fetchCoverBase64(
  url: string,
): Promise<{ data: string; contentType: string } | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      credentials: "omit",
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const contentType = (response.headers.get("content-type") || "").split(";")[0].trim();
    if (!contentType.startsWith("image/")) return null;
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength === 0 || buffer.byteLength > MAX_COVER_BYTES) return null;
    return { data: arrayBufferToBase64(buffer), contentType };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch covers for up to MAX_COVERS_PER_BATCH notes and attach the bytes
 * in-place as ``cover_data`` / ``cover_content_type``. Notes whose cover
 * fails to fetch are left untouched. Never throws.
 */
export async function attachCoverData(notes: readonly XhsNoteMetadata[]): Promise<void> {
  const targets = notes
    .filter((note) => note.cover_url && !note.cover_data && isHarvestableCoverUrl(note.cover_url))
    .slice(0, MAX_COVERS_PER_BATCH);
  if (targets.length === 0) return;
  await Promise.all(
    targets.map(async (note) => {
      const result = await fetchCoverBase64(note.cover_url);
      if (result) {
        note.cover_data = result.data;
        note.cover_content_type = result.contentType;
      }
    }),
  );
}
