/**
 * Bangumi (bgm.tv / bangumi.tv) content script — identity-only.
 *
 * Bangumi is NOT a behaviour-collection platform for OpenBiliClaw: this
 * script deliberately collects nothing about what the user browses. Its
 * single job is zero-config account identification for the Bangumi
 * source: combine the page's public ``CHOBITS_UID`` (delivered by the
 * MAIN-world ``bgm-identity-bridge``, because isolated worlds cannot
 * read page globals) with the nav's own ``/user/<username>`` link, and
 * report ``{uid, username}`` to the backend so guided init can resolve
 * "who are you" without a token or a typed username.
 *
 * Privacy: uid and username are both public (they form the user's
 * profile URL). No cookies, no tokens, no browsing signals.
 */

const BGM_IDENTITY_MESSAGE_SOURCE = "obc-bgm-identity";

// Own-user nav anchors — ONLY regions that exclusively render the logged-in
// user's own links: the header identity badge (idBadgerNeue) and the classic
// dock. Generic ``a.avatar[href*='/user/']`` fallbacks were removed after a
// real-page E2E (2026-07-18) showed they match TIMELINE STRANGER avatars on
// the bgm.tv homepage (/user/yuzzyu, /user/474349), which would report a
// stranger's username as the user's own. When these own-only regions miss,
// we report ``username: ""`` and let the backend resolve the username
// authoritatively from the uid via ``GET /v0/users/{uid}`` (default-slug
// users) or verify a later DOM report against the API's ``id`` field.
const OWN_USER_ANCHOR_SELECTORS = [
  "#headerNeue2 .idBadgerNeue a[href*='/user/']",
  ".idBadgerNeue a[href*='/user/']",
  "#dock a[href*='/user/']",
];

/**
 * Extract the username segment from a Bangumi profile href.
 * ``/user/sai`` and ``/user/sai/collections`` both yield ``"sai"``.
 * Returns "" for malformed or non-user paths.
 */
export function usernameFromProfileHref(href: string, baseUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(href, baseUrl);
  } catch {
    return "";
  }
  const host = parsed.hostname.toLowerCase();
  if (!host.endsWith("bgm.tv") && !host.endsWith("bangumi.tv")) return "";
  const match = /^\/user\/([^/?#]+)/.exec(parsed.pathname);
  if (!match) return "";
  let segment = "";
  try {
    segment = decodeURIComponent(match[1]).trim();
  } catch {
    segment = match[1].trim();
  }
  // Defensive: usernames are short slugs; drop anything that violates the
  // backend's validate_bangumi_username contract instead of reporting junk.
  if (!segment || segment.length > 128) return "";
  if (
    segment.includes("/") ||
    Array.from(segment).some((ch) => {
      const code = ch.charCodeAt(0);
      return code < 32 || code === 127;
    })
  ) {
    return "";
  }
  return segment;
}

/**
 * Find the logged-in user's own username from the page nav.
 * Returns "" when no own-profile anchor is present (e.g. logged out).
 */
export function extractBangumiUsernameFromDocument(doc: Document, baseUrl: string): string {
  for (const selector of OWN_USER_ANCHOR_SELECTORS) {
    let anchors: NodeListOf<HTMLAnchorElement>;
    try {
      anchors = doc.querySelectorAll<HTMLAnchorElement>(selector);
    } catch {
      continue;
    }
    for (const anchor of Array.from(anchors)) {
      const username = usernameFromProfileHref(anchor.getAttribute("href") || "", baseUrl);
      if (username) return username;
    }
  }
  return "";
}

/** Parse the bridge message payload; returns a positive uid or 0. */
export function uidFromBridgeMessage(data: unknown): number {
  if (data === null || typeof data !== "object") return 0;
  const message = data as Record<string, unknown>;
  if (message.source !== BGM_IDENTITY_MESSAGE_SOURCE) return 0;
  const uid = message.uid;
  if (typeof uid !== "number" || !Number.isFinite(uid) || !Number.isInteger(uid)) return 0;
  return uid > 0 ? uid : 0;
}

let lastReportedKey = "";

function reportIdentity(uid: number): void {
  const username = extractBangumiUsernameFromDocument(document, window.location.href);
  const key = `${uid}:${username}`;
  if (key === lastReportedKey) return;
  lastReportedKey = key;
  try {
    chrome.runtime.sendMessage({
      action: "BGM_IDENTITY_OBSERVED",
      data: { uid, username },
    });
  } catch {
    // Extension context may be invalidated mid-navigation; next page load retries.
  }
}

function installBridgeListener(): void {
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window) return;
    const uid = uidFromBridgeMessage(event.data);
    if (uid > 0) reportIdentity(uid);
  });
}

if (typeof window !== "undefined" && typeof chrome !== "undefined" && chrome.runtime?.id) {
  installBridgeListener();
}
