/**
 * Harvest xhs cover URLs and bytes in the page context at scrape time.
 *
 * Two failure modes motivated this module (2026-07 「小红书都没头图」 field
 * report — the affected backend fetched hdslb/douyinpic covers fine but
 * never attempted a single xhscdn fetch, meaning it never HAD a usable
 * cover URL):
 *
 * 1. **Background tabs never load lazy images.** The search/creator task
 *    dispatcher scrapes in background tabs, where card ``<img>`` elements
 *    keep their inline ``data:`` placeholder forever — DOM extraction
 *    yields no usable cover URL. ``backfillCoverUrlsFromState`` recovers
 *    the real CDN URL from ``__INITIAL_STATE__`` instead.
 *
 * 2. **Server-side fetch is a race against the rotating token and the
 *    backend's own network.** xhscdn URLs carry a short-lived
 *    ``{timestamp}/{token}`` prefix; the backend prefetch only wins while
 *    the token is fresh and its egress to the CDN cooperates. Fetching the
 *    bytes here — in the page, at the moment the URL is freshest, over the
 *    user's own browser session — removes both variables. The bytes ride
 *    the existing note-metadata payloads as base64 and land in the
 *    backend's disk image cache (``save_extension_cover``), which serves
 *    them forever after via ``/api/image-proxy`` (the cache key ignores
 *    the rotating token).
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

/**
 * True for lazy-load placeholder "covers" that must never be stored.
 *
 * xhs cards below the fold carry an inline ``data:image/png`` placeholder
 * until an IntersectionObserver upgrades them — which never happens in the
 * background tabs the search/creator task dispatcher uses. Storing the
 * placeholder as ``cover_url`` produces cards that can never render a cover
 * (the backend proxy rejects non-http(s) URLs) with zero log evidence.
 */
export function isPlaceholderCoverUrl(raw: string): boolean {
  const value = raw.trim().toLowerCase();
  return value === "" || value.startsWith("data:") || value.startsWith("blob:");
}

const NOTE_ID_KEYS = ["note_id", "noteId", "id"] as const;
const STATE_COVER_PATHS: readonly (readonly string[])[] = [
  ["cover", "urlDefault"],
  ["cover", "url_default"],
  ["cover", "url"],
  ["cover", "src"],
  ["coverUrl"],
  ["cover_url"],
  ["imageList", "0", "urlDefault"],
  ["imageList", "0", "url"],
  ["image_list", "0", "url_default"],
  ["image_list", "0", "url"],
];
const STATE_WALK_MAX_DEPTH = 12;
const STATE_WALK_MAX_NODES = 50_000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Minimal Vue-reactive unwrap — mirrors bootstrap.ts semantics. */
function unwrap(value: unknown): unknown {
  let current = value;
  const seen = new Set<unknown>();
  while (isRecord(current) && !seen.has(current)) {
    seen.add(current);
    if ("_rawValue" in current) {
      current = (current as { _rawValue: unknown })._rawValue;
      continue;
    }
    if ("_value" in current) {
      current = (current as { _value: unknown })._value;
      continue;
    }
    break;
  }
  return current;
}

function coverUrlFromObject(obj: unknown): string {
  for (const path of STATE_COVER_PATHS) {
    let current: unknown = obj;
    for (const key of path) {
      current = unwrap(current);
      if (Array.isArray(current)) {
        current = current[Number(key)];
      } else if (isRecord(current)) {
        current = current[key];
      } else {
        current = undefined;
        break;
      }
    }
    current = unwrap(current);
    if (typeof current === "string") {
      const url = current.trim();
      if (url.startsWith("//")) return `https:${url}`;
      if (url.startsWith("http://") || url.startsWith("https://")) return url;
    }
  }
  return "";
}

/**
 * Shape-agnostic scan of ``__INITIAL_STATE__`` for note cover URLs.
 *
 * Search/feed state shapes drift constantly, so instead of hard-coding a
 * path to the results array we walk the whole state (cycle-safe, depth- and
 * node-bounded) looking for objects whose id key matches a wanted note id,
 * then read a cover URL off that object or its ``noteCard`` child. This is
 * the reliable cover source in background tabs, where lazy-loaded ``<img>``
 * elements never upgrade past their ``data:`` placeholder.
 */
export function extractCoverUrlsFromState(
  state: unknown,
  noteIds: ReadonlySet<string>,
): Map<string, string> {
  const found = new Map<string, string>();
  if (noteIds.size === 0) return found;
  const seen = new Set<unknown>();
  let visited = 0;

  const visit = (value: unknown, depth: number): void => {
    if (found.size >= noteIds.size || depth > STATE_WALK_MAX_DEPTH) return;
    if (visited >= STATE_WALK_MAX_NODES) return;
    const node = unwrap(value);
    if (!isRecord(node) || seen.has(node)) return;
    seen.add(node);
    visited += 1;

    if (!Array.isArray(node)) {
      for (const idKey of NOTE_ID_KEYS) {
        const rawId = unwrap(node[idKey]);
        if (typeof rawId !== "string" || !noteIds.has(rawId) || found.has(rawId)) continue;
        const cover =
          coverUrlFromObject(node) ||
          coverUrlFromObject(node.noteCard) ||
          coverUrlFromObject(node.note_card);
        if (cover) found.set(rawId, cover);
      }
    }

    const children = Array.isArray(node) ? node : Object.values(node);
    for (const child of children) {
      if (typeof child === "object" && child !== null) visit(child, depth + 1);
    }
  };

  visit(state, 0);
  return found;
}

function noteIdFromNoteUrl(noteUrl: string): string {
  try {
    const path = new URL(noteUrl).pathname.replace(/\/+$/, "");
    return path.slice(path.lastIndexOf("/") + 1);
  } catch {
    return "";
  }
}

/**
 * Fill missing/placeholder ``cover_url`` values from ``__INITIAL_STATE__``.
 * Mutates notes in place; notes the state doesn't know keep their value.
 */
export function backfillCoverUrlsFromState(
  notes: readonly XhsNoteMetadata[],
  state: unknown,
): number {
  const wanted = new Map<string, XhsNoteMetadata[]>();
  for (const note of notes) {
    if (!isPlaceholderCoverUrl(note.cover_url)) continue;
    const id = noteIdFromNoteUrl(note.url);
    if (!id) continue;
    const bucket = wanted.get(id);
    if (bucket) bucket.push(note);
    else wanted.set(id, [note]);
  }
  if (wanted.size === 0) return 0;
  const covers = extractCoverUrlsFromState(state, new Set(wanted.keys()));
  let filled = 0;
  for (const [id, cover] of covers) {
    for (const note of wanted.get(id) ?? []) {
      note.cover_url = cover;
      filled += 1;
    }
  }
  return filled;
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
