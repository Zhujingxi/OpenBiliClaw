/**
 * Douyin RENDER_DATA reader — pure, no side effects on import.
 *
 * Task 4 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md).
 *
 * Douyin SSR-injects a `<script id="RENDER_DATA">` element whose
 * textContent is URL-encoded JSON. Top-level key is `app`. The
 * logged-in user's sec_uid lives at one of a few canonical paths
 * inside that tree; we try each in order and return the first hit.
 *
 * Verified empirically via chrome-devtools MCP probe 2026-05-07
 * (anonymous /jingxuan landing page returned a 181 KB payload with
 * top key `app`). The exact sub-path varies between login states
 * and Douyin React-app versions, so we accept multiple shapes.
 */

/**
 * Decode a URL-encoded JSON string into its parsed value.
 * Returns null if either decoding or parsing fails — the caller is
 * expected to treat that as "RENDER_DATA missing or malformed",
 * which is recoverable (we just skip the bootstrap).
 */
export function decodeRenderData(raw: string): unknown {
  if (!raw) return null;
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return null;
  }
  try {
    return JSON.parse(decoded);
  } catch {
    return null;
  }
}

function getNestedString(state: unknown, path: string[]): string {
  let cursor: unknown = state;
  for (const key of path) {
    if (!cursor || typeof cursor !== "object") return "";
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return typeof cursor === "string" ? cursor : "";
}

function getNestedBool(state: unknown, path: string[]): boolean | null {
  let cursor: unknown = state;
  for (const key of path) {
    if (!cursor || typeof cursor !== "object") return null;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return typeof cursor === "boolean" ? cursor : null;
}

/**
 * Find the logged-in user's sec_uid by trying each canonical path
 * in priority order. Returns "" if no path resolves to a string.
 *
 * Paths are intentionally narrow — we never treat a random string
 * field as sec_uid. If Douyin reorganizes the state shape we'd
 * rather return "" (and let the executor fall back to /user/self
 * for navigation) than confidently return the wrong value.
 */
export function extractDouyinSecUidFromState(state: unknown): string {
  const candidatePaths: string[][] = [
    ["app", "user", "userInfo", "secUid"],
    ["app", "user", "userInfo", "sec_uid"],
    ["app", "userStore", "user", "secUid"],
    ["app", "userStore", "user", "sec_uid"],
    ["app", "user", "secUid"],
    ["app", "user", "sec_uid"],
  ];
  for (const path of candidatePaths) {
    const found = getNestedString(state, path);
    if (found) return found;
  }
  return "";
}

/**
 * Resolve the logged-in user's sec_uid directly from the URL-encoded
 * ``#RENDER_DATA`` payload. Keeping the decode + narrow-path lookup in one
 * helper makes it usable by the live content executor as well as unit tests.
 */
export function extractDouyinSecUidFromRenderData(raw: string): string {
  const state = decodeRenderData(raw);
  if (!extractDouyinLoginState(state)) return "";
  return extractDouyinSecUidFromState(state);
}

/**
 * Detect whether Douyin's RENDER_DATA represents a logged-in user.
 * Conservative: only an explicit `isLogin: true` field is accepted.
 * A sec_uid can also appear in logged-out/device-scoped state, so its
 * presence is an observed identity claim, not login proof.
 *
 * Why conservative? If we hallucinate a logged-in state and run a
 * bootstrap that hits favorite/like endpoints, we'll just get empty
 * 200s and silently store nothing — but the user's daemon will
 * believe Douyin had no signals to give, which corrupts the source
 * mix calculation. Better to skip the bootstrap entirely.
 */
export function extractDouyinLoginState(state: unknown): boolean {
  const candidatePaths: string[][] = [
    ["app", "user", "userInfo", "isLogin"],
    ["app", "userStore", "user", "isLogin"],
    ["app", "user", "isLogin"],
  ];
  let sawExplicitTrue = false;
  for (const path of candidatePaths) {
    const value = getNestedBool(state, path);
    // Contradictory SSR state must fail closed. profile/self remains
    // the authoritative source used by the live executor.
    if (value === false) return false;
    if (value === true) sawExplicitTrue = true;
  }
  return sawExplicitTrue;
}

export interface DouyinAuthoritativeIdentity {
  secUid: string;
  source: "" | "profile_self";
  conflict: boolean;
  error?: string;
}

/**
 * Reconcile the SSR identity claim with `/profile/self`.
 *
 * RENDER_DATA is useful only for diagnostics and conflict detection.
 * The returned identity is never populated unless profile/self positively
 * confirms the current logged-in account. A profile response therefore wins
 * every conflict, and callers may cache only a non-empty result from here.
 */
export function reconcileDouyinSelfIdentity(input: {
  renderDataSecUid: string;
  profileSelfSecUid: string;
  profileError?: string;
}): DouyinAuthoritativeIdentity {
  const renderDataSecUid = input.renderDataSecUid.trim();
  const profileSelfSecUid = input.profileSelfSecUid.trim();
  if (!profileSelfSecUid) {
    return {
      secUid: "",
      source: "",
      conflict: false,
      error: input.profileError ?? "identity_unavailable",
    };
  }
  return {
    secUid: profileSelfSecUid,
    source: "profile_self",
    conflict: Boolean(renderDataSecUid && renderDataSecUid !== profileSelfSecUid),
  };
}
