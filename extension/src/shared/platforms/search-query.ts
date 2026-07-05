/**
 * Shared helpers for deriving a search query from a result-page URL.
 * Each adapter's `extractSearchQuery` follows its own `detectPageType`
 * search patterns (query param and/or path segment); a query-less search
 * page returns null so the kernel emits nothing.
 */

/**
 * Search dedup window: a URL-derived capture and the Enter-key capture of
 * the same query typically fire within a second of each other (the nav
 * follows the keypress). Collapse identical normalized queries seen within
 * this window into one event.
 */
export const SEARCH_DEDUP_WINDOW_MS = 10_000;

export function normalizeSearchQuery(query: string): string {
  return query.trim().toLowerCase();
}

/**
 * True when `query` repeats the last-emitted search (same normalized text)
 * within the dedup window. Pure so the kernel's shared Enter/URL guard is
 * testable without a DOM.
 */
export function isDuplicateSearch(
  last: { query: string; ts: number } | null,
  query: string,
  nowMs: number,
  windowMs: number = SEARCH_DEDUP_WINDOW_MS,
): boolean {
  if (last === null) return false;
  return last.query === normalizeSearchQuery(query) && nowMs - last.ts < windowMs;
}

/** Read and trim a query param; null when absent or empty. */
export function queryParam(url: string, name: string): string | null {
  let value: string | null;
  try {
    value = new URL(url).searchParams.get(name);
  } catch {
    return null;
  }
  const trimmed = (value ?? "").trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Read the search term from a path segment that follows a `search`
 * marker (Douyin: `/search/<encoded>`, `/jingxuan/search/<encoded>`).
 * Falls back to null when the segment after `search` is missing/empty.
 */
export function searchPathSegment(url: string): string | null {
  let pathname: string;
  try {
    pathname = new URL(url).pathname;
  } catch {
    return null;
  }
  const segments = pathname.split("/").filter((segment) => segment.length > 0);
  const searchIndex = segments.lastIndexOf("search");
  if (searchIndex === -1 || searchIndex === segments.length - 1) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(segments[searchIndex + 1]);
  } catch {
    decoded = segments[searchIndex + 1];
  }
  const trimmed = decoded.trim();
  return trimmed.length > 0 ? trimmed : null;
}
