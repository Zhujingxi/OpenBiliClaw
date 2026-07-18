/**
 * MAIN-world script for bgm.tv / bangumi.tv — reads the page's public
 * ``CHOBITS_UID`` global and ships it to the isolated content script.
 *
 * Why this exists: MV3 content scripts run in an isolated JS world and
 * cannot read globals assigned by the page's own inline scripts.
 * Bangumi's server-rendered pages set ``window.CHOBITS_UID`` to the
 * logged-in numeric user id (``0`` when logged out), which is the most
 * reliable login signal the page exposes. Mirrors the xhs-state-bridge
 * pattern (see ``xhs-state-bridge.ts``), minus the state walking — the
 * only value we need is one number.
 *
 * Privacy: the uid is public information (it appears in the user's own
 * profile URLs and every public API response about them). We never read
 * cookies, tokens, or any other page state here.
 */

export const BGM_IDENTITY_MESSAGE_SOURCE = "obc-bgm-identity";

/** Parse a CHOBITS_UID-shaped value into a positive uid, or 0. */
export function parseChobitsUid(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value > 0 && Number.isInteger(value) ? value : 0;
  }
  if (typeof value === "string") {
    const parsed = Number.parseInt(value.trim(), 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  }
  return 0;
}

let lastPostedUid = 0;

function emitOnce(): void {
  const win = window as Window & { CHOBITS_UID?: unknown };
  const uid = parseChobitsUid(win.CHOBITS_UID);
  // Only report a logged-in uid: a 0 must never overwrite a previously
  // known identity in the backend, and posting it adds nothing.
  if (uid <= 0 || uid === lastPostedUid) return;
  lastPostedUid = uid;
  try {
    window.postMessage({ source: BGM_IDENTITY_MESSAGE_SOURCE, uid }, "*");
  } catch {
    // postMessage of a plain number payload should never throw; swallow.
  }
}

// Bangumi sets CHOBITS_UID in an inline <head> script, so it is present
// well before document_idle. A short poll covers slow/partial loads.
function startPolling(): void {
  let attempts = 0;
  const tick = (): void => {
    attempts += 1;
    emitOnce();
    if (lastPostedUid > 0 || attempts >= 20) return; // 20 * 250ms = 5s budget
    window.setTimeout(tick, 250);
  };
  tick();
}

if (typeof window !== "undefined") {
  startPolling();
}
