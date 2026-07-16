/**
 * Xiaohongshu MAIN-world action tap.
 *
 * Pattern (mirrors `x-graphql-tap.ts` + `bili-interact-tap.ts`): wrap
 * `window.fetch` and `XMLHttpRequest` in MAIN world to **observe** the user's
 * own like / collect writes (and their withdrawals) on xiaohongshu.com, then
 * `postMessage({ source: "obc-xhs-action", ... })` back to the isolated-world
 * content script (`content/xiaohongshu.ts`), which forwards them as
 * like / favorite / retraction BEHAVIOR_EVENTs.
 *
 * Why MAIN world? Content scripts run in an isolated JS context, so overriding
 * `window.fetch` there doesn't intercept the page's own fetches. A MAIN-world
 * script shares state with the page and wraps the same `fetch` /
 * `XMLHttpRequest` the xhs React app uses.
 *
 * Why a network tap at all? xhs like/collect controls are icon-only buttons
 * with unstable, text-less DOM — the old keyword-matching adapter silently
 * missed icon buttons and could not reliably tell a positive from a withdrawal.
 * The write endpoint is the ground truth for what the user actually did.
 *
 * What we capture (the user's own strong signals only):
 *   - POST …/v1/note/like      → like
 *   - POST …/v1/note/dislike   → retraction (withdrew a like)
 *   - POST …/v1/note/collect   → favorite
 *   - POST …/v1/note/uncollect → retraction (withdrew a favorite)
 *
 * Isolation from the token sniffer: this tap posts under
 * `source: "obc-xhs-action"`, while `xhs-token-sniffer.ts` posts under
 * `source: "obc-xhs-sniffer"`. The two never cross-talk — a note-detail /
 * search API response scanned by the sniffer for `(note_id, xsec_token)` is
 * not one of the write endpoints above, and vice versa.
 *
 * CRITICAL constraints:
 *   1. Observation-only: the page's (input, init) are forwarded byte-identical
 *      to the original fetch; only a `Response.clone()` is read. Requests are
 *      never mutated.
 *   2. Network success = business success (invariant 7b): a captured write is
 *      only reported when the response JSON reports success (`success === true`
 *      or `code === 0`). An HTTP 2xx carrying an xhs business error is dropped.
 *
 * Fixture / field shapes are modelled on the public community-documented xhs
 * web APIs (host `edith.xiaohongshu.com`, `{"success":true,"code":0,...}`
 * response) pending a real end-to-end capture (see PR notes). The parser only
 * depends on the endpoint path, a 24-hex note id recoverable from the request
 * body, and the success gate.
 *
 * The module does NOT auto-install when imported under node:test (the
 * side-effect block is guarded by `typeof window`). `classifyXhsActionUrl` and
 * `parseXhsAction` are pure and unit-tested directly.
 */

const POST_MESSAGE_SOURCE = "obc-xhs-action";

/** The strong signal a captured write represents. */
export type XhsActionType = "like" | "favorite" | "retraction";

/** The positive action a retraction withdraws. */
export type XhsRetractedAction = "like" | "favorite";

/** A request as the tap observes it: URL + raw request/response bodies. */
export interface CapturedXhsRequest {
  url: string;
  requestBody: string;
  responseBody: string;
}

/** Parsed action posted to the content script. */
export interface XhsAction {
  type: XhsActionType;
  /** 24-hex note id the action targeted. */
  note_id: string;
  /** Present for a retraction only — the withdrawn positive action. */
  retracted_action?: XhsRetractedAction;
}

interface EndpointRule {
  /** Path suffix (query ignored) that identifies the write. */
  re: RegExp;
  type: XhsActionType;
  /** Set only for retraction endpoints. */
  retracted?: XhsRetractedAction;
}

// Write endpoints → action. Paths matched against the URL path (query string
// stripped). Modelled on public community docs, pending real end-to-end
// validation.
const ENDPOINT_RULES: readonly EndpointRule[] = [
  { re: /\/note\/like$/, type: "like" },
  { re: /\/note\/dislike$/, type: "retraction", retracted: "like" },
  { re: /\/note\/collect$/, type: "favorite" },
  { re: /\/note\/uncollect$/, type: "retraction", retracted: "favorite" },
];

/**
 * Classify a request URL into the action it represents, or null. Matches the
 * endpoint path (query string ignored). Exported for tests.
 */
export function classifyXhsActionUrl(url: string): EndpointRule | null {
  if (!url) return null;
  const path = url.split("?", 1)[0] ?? "";
  for (const rule of ENDPOINT_RULES) {
    if (rule.re.test(path)) return rule;
  }
  return null;
}

function safeJsonParse(text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * True when an xhs JSON response reports business success. xhs write APIs
 * return `{"success":true,"code":0,...}`; treat either signal as success so a
 * schema tweak on one field doesn't silently drop every event.
 */
function xhsResponseOk(responseBody: string): boolean {
  const parsed = safeJsonParse(responseBody);
  if (!parsed || typeof parsed !== "object") return false;
  const obj = parsed as { success?: unknown; code?: unknown };
  return obj.success === true || obj.code === 0;
}

const NOTE_ID_RE = /^[0-9a-f]{24}$/i;
// Keys xhs uses (or has used) to carry the target note id in write bodies.
const NOTE_ID_KEYS = new Set([
  "note_id",
  "noteId",
  "note_oid",
  "noteOid",
  "oid",
  "target_note_id",
  "id",
]);

/**
 * Depth-first scan a JSON blob for the target 24-hex note id. Prefers a value
 * under a known note-id key; falls back to the first bare 24-hex string
 * anywhere (xhs response shapes drift, so we don't hard-code a path).
 */
function findNoteId(node: unknown): string {
  let fallback = "";
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === null || typeof current !== "object") continue;
    if (Array.isArray(current)) {
      for (const child of current) stack.push(child);
      continue;
    }
    const obj = current as Record<string, unknown>;
    for (const [k, v] of Object.entries(obj)) {
      if (typeof v === "string" && NOTE_ID_RE.test(v)) {
        if (NOTE_ID_KEYS.has(k)) return v.toLowerCase();
        if (!fallback) fallback = v.toLowerCase();
      }
    }
    for (const v of Object.values(obj)) {
      if (v !== null && typeof v === "object") stack.push(v);
    }
  }
  return fallback;
}

/**
 * Extract an `XhsAction` from a captured request/response, or null when the
 * endpoint isn't a write we capture, the business gate fails, or no note id is
 * recoverable. Pure — no DOM, no side effects.
 */
export function parseXhsAction(captured: CapturedXhsRequest): XhsAction | null {
  const { url, requestBody, responseBody } = captured;
  const rule = classifyXhsActionUrl(url);
  if (!rule) return null;
  // Business gate (invariant 7b): HTTP 2xx alone is not success.
  if (!xhsResponseOk(responseBody)) return null;

  const note_id =
    findNoteId(safeJsonParse(requestBody)) || findNoteId(safeJsonParse(responseBody));
  if (!note_id) return null;

  const action: XhsAction = { type: rule.type, note_id };
  if (rule.type === "retraction" && rule.retracted) {
    action.retracted_action = rule.retracted;
  }
  return action;
}

// ── MAIN-world install (observation-only) ────────────────────────────────

function emit(target: Window, action: XhsAction): void {
  try {
    target.postMessage(
      { source: POST_MESSAGE_SOURCE, action },
      target.location?.origin ?? "*",
    );
  } catch {
    // best effort — never break the page
  }
}

function urlFromInput(input: unknown): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  if (input && typeof input === "object" && "url" in input) {
    const u = (input as { url?: unknown }).url;
    return typeof u === "string" ? u : "";
  }
  return "";
}

function requestBodyFromInit(init: unknown): string {
  if (init && typeof init === "object" && "body" in init) {
    const body = (init as { body?: unknown }).body;
    if (typeof body === "string") return body;
  }
  return "";
}

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type TaggedXhr = XMLHttpRequest & { __obcXhsActionUrl?: string; __obcXhsActionBody?: string };

/**
 * Wrap `target.fetch` and `target.XMLHttpRequest` so every captured like /
 * collect write is parsed and posted back. Observation-only: the page's
 * (input, init) are forwarded byte-identical and only a `Response.clone()` is
 * read. Returns a disposer that restores the originals (handy for tests).
 */
export function installXhsActionTap(target: Window): () => void {
  const w = target as unknown as {
    fetch: FetchLike;
    XMLHttpRequest: { prototype: XMLHttpRequest };
  };

  // ── fetch ──────────────────────────────────────────────────────────
  const originalFetch = w.fetch;
  const wrappedFetch: FetchLike = function wrappedFetch(input, init) {
    const result = originalFetch.call(target, input, init);
    try {
      const url = urlFromInput(input);
      if (url && classifyXhsActionUrl(url)) {
        const requestBody = requestBodyFromInit(init);
        void result
          .then((resp) => {
            try {
              return resp.clone().text();
            } catch {
              return "";
            }
          })
          .then((responseBody) => {
            const action = parseXhsAction({ url, requestBody, responseBody });
            if (action) emit(target, action);
          })
          .catch(() => {
            /* swallow — never surface a rejection into the page */
          });
      }
    } catch {
      // never break the page's fetch
    }
    return result;
  };
  w.fetch = wrappedFetch;

  // ── XMLHttpRequest ─────────────────────────────────────────────────
  const proto = w.XMLHttpRequest.prototype;
  type OpenLike = (
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null,
  ) => void;
  type SendLike = (body?: Document | XMLHttpRequestBodyInit | null) => void;
  const originalOpen = proto.open as unknown as OpenLike;
  const originalSend = proto.send as unknown as SendLike;

  (proto as unknown as { open: OpenLike }).open = function patchedOpen(
    this: TaggedXhr,
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null,
  ): void {
    this.__obcXhsActionUrl = typeof url === "string" ? url : url.toString();
    return originalOpen.call(this, method, url, async ?? true, user ?? null, password ?? null);
  };

  (proto as unknown as { send: SendLike }).send = function patchedSend(
    this: TaggedXhr,
    body?: Document | XMLHttpRequestBodyInit | null,
  ): void {
    const url = this.__obcXhsActionUrl ?? "";
    if (url && classifyXhsActionUrl(url)) {
      const requestBody = typeof body === "string" ? body : "";
      this.addEventListener("load", () => {
        try {
          let responseBody = "";
          if (this.responseType === "" || this.responseType === "text") {
            responseBody = this.responseText ?? "";
          } else if (this.responseType === "json" && this.response) {
            responseBody = JSON.stringify(this.response);
          }
          const action = parseXhsAction({ url, requestBody, responseBody });
          if (action) emit(target, action);
        } catch {
          // never throw inside the XHR listener
        }
      });
    }
    return originalSend.call(this, body ?? null);
  };

  return (): void => {
    w.fetch = originalFetch;
    (proto as unknown as { open: OpenLike }).open = originalOpen;
    (proto as unknown as { send: SendLike }).send = originalSend;
  };
}

// Auto-install only in a real browser MAIN-world context. Guard on
// `typeof window` so node:test importing this module for the pure helpers
// doesn't wrap anything. Mirrors x-graphql-tap.ts / bili-interact-tap.ts.
if (typeof window !== "undefined" && typeof XMLHttpRequest !== "undefined") {
  installXhsActionTap(window);
  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] xhs action tap installed (MAIN world)");
}
