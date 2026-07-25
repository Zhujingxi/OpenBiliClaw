/**
 * Bilibili MAIN-world interact tap.
 *
 * Pattern (mirrors `x-graphql-tap.ts` + `xhs-token-sniffer.ts`): wrap
 * `window.fetch` and `XMLHttpRequest` in MAIN world to **observe** the user's
 * own danmaku, comment, like, favorite, and coin writes on bilibili.com, then
 * `postMessage({ source: "obc-bili-interact", ... })` back to the
 * isolated-world content script (`content/bilibili.ts`), which forwards them
 * as unified BEHAVIOR_EVENTs.
 *
 * Why MAIN world? Content scripts run in an isolated JS context, so overriding
 * `window.fetch` there doesn't intercept the page's own fetches. A MAIN-world
 * script shares state with the page and wraps the same `fetch` /
 * `XMLHttpRequest` the bilibili app uses.
 *
 * What we capture:
 *   - POST …/x/v2/dm/post   (form-encoded) → danmaku  (text in `msg`)
 *   - POST …/x/v2/reply/add (form-encoded) → comment  (text in `message`)
 *   - POST …/x/web-interface/archive/like (`like=1|2`) → like / retraction
 *   - POST …/x/v3/fav/resource/deal (`add_media_ids|del_media_ids`) →
 *     favorite / retraction
 *   - POST …/x/web-interface/coin/add → coin
 *
 * CRITICAL constraints:
 *   1. Observation-only: the page's (input, init) are forwarded byte-identical
 *      to the original fetch; only a `Response.clone()` is read. Requests are
 *      never mutated.
 *   2. Network success = business success (invariant 7b): a captured write is
 *      only reported for HTTP 2xx when the response JSON's top-level
 *      `code === 0`. An HTTP 2xx with a bilibili business error (e.g.
 *      `code: -412`) is dropped.
 *
 * Fixture / field shapes are modelled on the community-documented bilibili
 * write APIs (bilibili-API-collect) pending real-device validation. The parser
 * only depends on the endpoint path, documented form field names, HTTP 2xx,
 * and the `code===0` gate.
 *
 * The module does NOT auto-install when imported under node:test (the
 * side-effect block is guarded by `typeof window`). `classifyBiliInteractUrl`
 * and `parseBiliInteract` are pure and unit-tested directly.
 */

const POST_MESSAGE_SOURCE = "obc-bili-interact";

export type BiliInteractionKind =
  | "danmaku"
  | "comment"
  | "like"
  | "favorite"
  | "coin"
  | "retraction";

export type BiliInteractEndpoint =
  | "danmaku"
  | "comment"
  | "archive-like"
  | "favorite-deal"
  | "coin";

/** A request as the tap observes it: URL + raw request/response bodies. */
export interface CapturedBiliRequest {
  url: string;
  requestBody: string;
  responseStatus: number;
  responseBody: string;
}

/** Parsed interaction posted to the content script. */
export interface BiliInteraction {
  kind: BiliInteractionKind;
  /** The user's own danmaku / comment text (raw — the content script sanitizes). */
  text?: string;
  /** Video `oid` (cid for danmaku, aid for comment) — best effort context. */
  oid?: string;
  /** `bvid` when the write carried one (danmaku does) — best effort context. */
  bvid?: string;
  /** Positive action neutralized by a retraction. */
  retracted_action?: "like" | "favorite";
}

/**
 * Classify a request URL into the interaction kind we capture, or null.
 * Matches the endpoint path (query string ignored). Exported for tests.
 */
export function classifyBiliInteractUrl(url: string): BiliInteractEndpoint | null {
  if (!url) return null;
  const path = url.split("?", 1)[0] ?? "";
  if (/\/x\/v2\/dm\/post$/.test(path)) return "danmaku";
  if (/\/x\/v2\/reply\/add$/.test(path)) return "comment";
  if (/\/x\/web-interface\/archive\/like$/.test(path)) return "archive-like";
  if (/\/x\/v3\/fav\/resource\/deal$/.test(path)) return "favorite-deal";
  if (/\/x\/web-interface\/coin\/add$/.test(path)) return "coin";
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

/** True when a bilibili JSON response reports business success (`code === 0`). */
function biliResponseOk(responseBody: string): boolean {
  const parsed = safeJsonParse(responseBody);
  if (!parsed || typeof parsed !== "object") return false;
  return (parsed as { code?: unknown }).code === 0;
}

function formField(body: string, name: string): string {
  if (!body) return "";
  try {
    return new URLSearchParams(body).get(name) ?? "";
  } catch {
    return "";
  }
}

/**
 * Extract a `BiliInteraction` from a captured request/response, or null when
 * the endpoint isn't one we capture, the business code isn't 0, or no text is
 * recoverable. Pure — no DOM, no side effects.
 */
export function parseBiliInteract(captured: CapturedBiliRequest): BiliInteraction | null {
  const { url, requestBody, responseStatus, responseBody } = captured;
  const endpoint = classifyBiliInteractUrl(url);
  if (!endpoint) return null;
  if (responseStatus < 200 || responseStatus >= 300) return null;
  // Business gate (invariant 7b): HTTP 2xx alone is not success.
  if (!biliResponseOk(responseBody)) return null;

  let interaction: BiliInteraction;
  if (endpoint === "danmaku" || endpoint === "comment") {
    const text =
      endpoint === "danmaku"
        ? formField(requestBody, "msg")
        : formField(requestBody, "message");
    if (!text) return null;
    interaction = { kind: endpoint, text };
  } else if (endpoint === "archive-like") {
    const value = formField(requestBody, "like");
    if (value === "1") interaction = { kind: "like" };
    else if (value === "2") {
      interaction = { kind: "retraction", retracted_action: "like" };
    } else return null;
  } else if (endpoint === "favorite-deal") {
    const adds = formField(requestBody, "add_media_ids");
    const deletes = formField(requestBody, "del_media_ids");
    // Both populated is ambiguous and neither populated is not an action.
    if (adds && !deletes) interaction = { kind: "favorite" };
    else if (deletes && !adds) {
      interaction = { kind: "retraction", retracted_action: "favorite" };
    } else return null;
  } else {
    interaction = { kind: "coin" };
  }

  const oid = formField(requestBody, "oid");
  if (oid) interaction.oid = oid;
  const bvid = formField(requestBody, "bvid");
  if (bvid) interaction.bvid = bvid;
  return interaction;
}

// ── MAIN-world install (observation-only) ────────────────────────────────

function emit(target: Window, interaction: BiliInteraction): void {
  try {
    target.postMessage(
      { source: POST_MESSAGE_SOURCE, interaction },
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

function bodyToString(body: unknown): string {
  if (typeof body === "string") return body;
  try {
    if (typeof URLSearchParams !== "undefined" && body instanceof URLSearchParams) {
      return body.toString();
    }
    if (typeof FormData !== "undefined" && body instanceof FormData) {
      const params = new URLSearchParams();
      body.forEach((value, key) => {
        params.append(key, typeof value === "string" ? value : "");
      });
      return params.toString();
    }
  } catch {
    // unsupported / cross-realm body — no payload evidence
  }
  return "";
}

function requestBodyFromInit(init: unknown): string {
  if (init && typeof init === "object" && "body" in init) {
    const body = (init as { body?: unknown }).body;
    return bodyToString(body);
  }
  return "";
}

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
type TaggedXhr = XMLHttpRequest & { __obcBiliUrl?: string; __obcBiliBody?: string };

/**
 * Wrap `target.fetch` and `target.XMLHttpRequest` so every captured danmaku /
 * comment write is parsed and posted back. Observation-only: the page's
 * (input, init) are forwarded byte-identical and only a `Response.clone()` is
 * read. Returns a disposer that restores the originals (handy for tests).
 */
export function installBiliInteractTap(target: Window): () => void {
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
      if (url && classifyBiliInteractUrl(url)) {
        const requestBody = requestBodyFromInit(init);
        void result
          .then(async (resp) => {
            try {
              return {
                responseStatus: resp.status,
                responseBody: await resp.clone().text(),
              };
            } catch {
              return { responseStatus: resp.status, responseBody: "" };
            }
          })
          .then(({ responseStatus, responseBody }) => {
            const interaction = parseBiliInteract({
              url,
              requestBody,
              responseStatus,
              responseBody,
            });
            if (interaction) emit(target, interaction);
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
    this.__obcBiliUrl = typeof url === "string" ? url : url.toString();
    return originalOpen.call(this, method, url, async ?? true, user ?? null, password ?? null);
  };

  (proto as unknown as { send: SendLike }).send = function patchedSend(
    this: TaggedXhr,
    body?: Document | XMLHttpRequestBodyInit | null,
  ): void {
    const url = this.__obcBiliUrl ?? "";
    if (url && classifyBiliInteractUrl(url)) {
      const requestBody = bodyToString(body);
      this.addEventListener("load", () => {
        try {
          let responseBody = "";
          if (this.responseType === "" || this.responseType === "text") {
            responseBody = this.responseText ?? "";
          } else if (this.responseType === "json" && this.response) {
            responseBody = JSON.stringify(this.response);
          }
          const interaction = parseBiliInteract({
            url,
            requestBody,
            responseStatus: this.status,
            responseBody,
          });
          if (interaction) emit(target, interaction);
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
// doesn't wrap anything. Mirrors x-graphql-tap.ts / xhs-token-sniffer.ts.
if (typeof window !== "undefined" && typeof XMLHttpRequest !== "undefined") {
  installBiliInteractTap(window);
  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] bilibili interact tap installed (MAIN world)");
}
