/**
 * Backend API client for mobile web.
 * Mirrors extension popup-api.js but without Chrome-specific code.
 */

// Derived from the page origin, so every request stays same-origin and the
// HttpOnly session cookie (and WebSocket handshake) is carried automatically
// when the password gate is enabled. See
// docs/plans/2026-05-30-web-password-auth-design.md §4.3.
const BASE_URL = `${location.protocol}//${location.host}/api`;
const DEFAULT_READ_TIMEOUT_MS = 12_000;
const QUICK_READ_TIMEOUT_MS = 5_000;
const CONFIG_WRITE_TIMEOUT_MS = 60_000;
const MODEL_WRITE_TIMEOUT_MS = 60_000;
const SAVED_READ_TIMEOUT_MS = 10_000;
const SAVED_MUTATION_TIMEOUT_MS = 10_000;
const CSRF_HEADER = "X-OBC-Auth";

export interface RequestJsonOptions extends Omit<RequestInit, "signal"> {
  timeoutMs?: number;
  signal?: AbortSignal | null;
}

// TODO(types): backend payloads vary per endpoint; callers treat responses as
// opaque objects. Tighten to per-endpoint interfaces when the API contract is
// documented.
export type JsonPayload = Record<string, unknown>;

export interface RequestError extends Error {
  status?: number;
  details?: unknown;
}

/** Notify the shell that the session is gone so it can show the login view. */
function signalAuthRequired(): void {
  try {
    window.dispatchEvent(new CustomEvent("obc:auth-required"));
  } catch { /* non-browser env */ }
}

function abortError(message = "Request aborted"): Error {
  if (typeof DOMException === "function") {
    return new DOMException(message, "AbortError");
  }
  const error = new Error(message);
  error.name = "AbortError";
  return error;
}

interface TimeoutHandle {
  signal: AbortSignal | undefined;
  cleanup(): void;
}

function withTimeout(signal: AbortSignal | null | undefined, timeoutMs: number | undefined): TimeoutHandle {
  const hasTimeout = Number.isFinite(timeoutMs) && (timeoutMs as number) > 0;
  if (!hasTimeout && !signal) return { signal: undefined, cleanup() {} };
  if (!hasTimeout) return { signal: signal ?? undefined, cleanup() {} };

  const controller = new AbortController();
  let tid: ReturnType<typeof setTimeout> | null = null;
  const abort = (reason?: unknown) => { if (!controller.signal.aborted) controller.abort(reason || abortError()); };
  const onCaller = () => abort(signal?.reason);

  if (signal?.aborted) abort(signal.reason);
  else if (signal) signal.addEventListener("abort", onCaller, { once: true });
  tid = setTimeout(() => abort(abortError("Request timed out")), timeoutMs);

  return {
    signal: controller.signal,
    cleanup() {
      if (tid !== null) clearTimeout(tid);
      if (signal) signal.removeEventListener("abort", onCaller);
    },
  };
}

export async function requestJson(path: string, options: RequestJsonOptions = {}): Promise<unknown> {
  const { timeoutMs, signal, ...fetchOptions } = options;
  const init: RequestInit = { ...fetchOptions };
  const timeout = withTimeout(signal, timeoutMs);
  if (timeout.signal) init.signal = timeout.signal;
  // Send the session cookie on every request; add the CSRF header on EVERY
  // request (incl. GET) so state-changing GETs like /api/recommendations are
  // covered. Only fetch() carries it — <img>/WebSocket don't and don't hit
  // CSRF-gated paths. Required by the gate, §4.8.
  init.credentials = "same-origin";
  init.headers = { ...(init.headers || {}), [CSRF_HEADER]: "1" };
  try {
    const res = await fetch(`${BASE_URL}${path}`, init);
    if (!res.ok) {
      let details: unknown = null;
      try { details = await res.json(); } catch { details = null; }
      if (res.status === 401) signalAuthRequired();
      const err: RequestError = new Error(`${path} failed: ${res.status}`);
      err.status = res.status;
      err.details = details;
      throw err;
    }
    return res.json();
  } finally {
    timeout.cleanup();
  }
}

// ── Auth (password gate) ────────────────────────────────────
export interface AuthStatus {
  enabled: boolean;
  authenticated: boolean;
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  try {
    return await requestJson("/auth/status", { timeoutMs: QUICK_READ_TIMEOUT_MS }) as AuthStatus;
  } catch {
    // Treat an unreachable backend as "not gated" so the normal offline UI shows.
    return { enabled: false, authenticated: true };
  }
}

export interface LoginResult {
  ok: boolean;
  status: number;
  data: JsonPayload | null;
}

export async function login(password: string): Promise<LoginResult> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  let data: JsonPayload | null = null;
  try { data = await res.json() as JsonPayload; } catch { data = null; }
  return { ok: res.ok && Boolean(data?.ok), status: res.status, data };
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${BASE_URL}/auth/logout`, { method: "POST", credentials: "same-origin" });
  } catch { /* best-effort cookie clear */ }
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// ── Health ──────────────────────────────────────────────────
export async function fetchHealth(): Promise<unknown> {
  return requestJson("/health");
}

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/health`, { method: "GET" });
    return res.ok;
  } catch { return false; }
}

export async function fetchConfig(timeoutMs = DEFAULT_READ_TIMEOUT_MS): Promise<unknown> {
  return requestJson("/config", { timeoutMs });
}

export async function updateConfig(data: unknown, timeoutMs = CONFIG_WRITE_TIMEOUT_MS): Promise<unknown> {
  return requestJson("/config", {
    method: "PUT",
    timeoutMs,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ── Model configuration ─────────────────────────────────────
export async function fetchModelConfig(timeoutMs = DEFAULT_READ_TIMEOUT_MS): Promise<unknown> {
  return requestJson("/model-config", { timeoutMs });
}

export async function fetchModelConnectionTypes(
  capability = "",
  timeoutMs = DEFAULT_READ_TIMEOUT_MS,
): Promise<unknown> {
  if (!capability) {
    return requestJson("/model-connection-types", { timeoutMs });
  }
  const query = new URLSearchParams({ capability: String(capability) });
  return requestJson(`/model-connection-types?${query}`, { timeoutMs });
}

export async function updateModelConfig(data: unknown, timeoutMs = MODEL_WRITE_TIMEOUT_MS): Promise<unknown> {
  return requestJson("/model-config", {
    method: "PUT",
    timeoutMs,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function probeModelConnection(data: unknown, timeoutMs = MODEL_WRITE_TIMEOUT_MS): Promise<unknown> {
  return requestJson("/model-config/probe", {
    method: "POST",
    timeoutMs,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ── Recommendations ─────────────────────────────────────────
// TODO(types): recommendation items are normalized by view-models.js.
export async function fetchRecommendations(): Promise<unknown[]> {
  const data = await requestJson("/recommendations", { timeoutMs: DEFAULT_READ_TIMEOUT_MS }) as { items?: unknown };
  return Array.isArray(data.items) ? data.items : [];
}

export interface RecommendationsPage {
  items: unknown[];
  [key: string]: unknown;
}

export async function reshuffleRecommendations(): Promise<RecommendationsPage> {
  const data = await requestJson("/recommendations/reshuffle", { method: "POST" }) as RecommendationsPage;
  return { ...data, items: Array.isArray(data.items) ? data.items : [] };
}

export async function appendRecommendations(excludedBvids: string[] = []): Promise<RecommendationsPage> {
  const data = await requestJson("/recommendations/append", json({ excluded_bvids: excludedBvids })) as RecommendationsPage;
  return { ...data, items: Array.isArray(data.items) ? data.items : [] };
}

export async function reportClick(payload: unknown): Promise<boolean> {
  try {
    await requestJson("/recommendation-click", json(payload));
    return true;
  } catch { return false; }
}

// ── Runtime Status ──────────────────────────────────────────
export async function fetchRuntimeStatus(): Promise<unknown> {
  return requestJson("/runtime-status", { timeoutMs: QUICK_READ_TIMEOUT_MS });
}

// ── Delight ─────────────────────────────────────────────────
export async function fetchDelightBatch(limit: number | null = null): Promise<unknown[]> {
  const params = new URLSearchParams();
  if (typeof limit === "number" && Number.isFinite(limit)) {
    params.set("limit", String(Math.max(1, Math.min(100, Math.floor(limit)))));
  }
  const qs = params.toString();
  const data = await requestJson(`/delight/pending-batch${qs ? `?${qs}` : ""}`, { timeoutMs: DEFAULT_READ_TIMEOUT_MS }) as { items?: unknown } | null;
  return Array.isArray(data?.items) ? data.items as unknown[] : [];
}

export async function respondToDelight(bvid: string, responseType: string, title = "", message = ""): Promise<unknown> {
  return requestJson("/delight/respond", {
    ...json({ bvid, response: responseType, title, message }),
    timeoutMs: 35_000,
  });
}

// ── Profile ─────────────────────────────────────────────────
export interface ProfileSummaryQuery {
  limit?: number;
  cursor?: string;
}

export async function fetchProfileSummary({ limit, cursor }: ProfileSummaryQuery = {}): Promise<unknown> {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (typeof cursor === "string" && cursor.trim()) params.set("cursor", cursor.trim());
  const qs = params.toString();
  return requestJson(`/profile-summary${qs ? `?${qs}` : ""}`);
}

export async function fetchEditState(): Promise<unknown> {
  return requestJson("/profile/edit-state");
}

export interface ProfileEditInput {
  target: string;
  op: string;
  value?: unknown;
  parent?: string;
  weight?: number | null;
}

export async function submitProfileEdit({ target, op, value = null, parent = "", weight = null }: ProfileEditInput): Promise<unknown> {
  return requestJson("/profile/edit", {
    ...json({ target, op, value, parent, weight }),
    timeoutMs: 35_000,
  });
}

export async function submitInsightFeedback(hypothesis: string, signal: string): Promise<unknown> {
  return requestJson("/insights/feedback", {
    ...json({ hypothesis, signal }),
    timeoutMs: 35_000,
  });
}

// ── Notifications ───────────────────────────────────────────
export async function fetchPendingNotifications(): Promise<unknown> {
  return requestJson("/notifications/pending");
}

export async function ackNotification(bvid: string): Promise<unknown> {
  return requestJson("/notifications/sent", json({ bvid }));
}

// ── Activity Feed ───────────────────────────────────────────
export interface ActivityFeedQuery {
  limit?: number;
  before?: string;
}

export async function fetchActivityFeed({ limit, before }: ActivityFeedQuery = {}): Promise<unknown> {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (before) params.set("before", before);
  const qs = params.toString();
  return requestJson(`/activity-feed${qs ? `?${qs}` : ""}`, { timeoutMs: QUICK_READ_TIMEOUT_MS });
}

// ── Chat ────────────────────────────────────────────────────
export interface ChatTurnInput {
  turnId?: string;
  session?: string;
  scope?: string;
  subjectId?: string;
  subjectTitle?: string;
  message: string;
}

export async function startChatTurn({ turnId = "", session = "mobile", scope = "chat", subjectId = "", subjectTitle = "", message }: ChatTurnInput): Promise<unknown> {
  return requestJson("/chat/turns", json({
    turn_id: turnId,
    session,
    scope,
    subject_id: subjectId,
    subject_title: subjectTitle,
    message,
  }));
}

export async function fetchChatTurn(turnId: string): Promise<unknown> {
  return requestJson(`/chat/turns/${encodeURIComponent(turnId)}`);
}

export interface ChatTurnsQuery {
  session?: string;
  scope?: string;
  limit?: number;
}

export async function fetchChatTurns({ session = "mobile", scope = "", limit = 50 }: ChatTurnsQuery = {}): Promise<unknown> {
  const params = new URLSearchParams();
  params.set("session", session);
  if (scope) params.set("scope", scope);
  if (typeof limit === "number") params.set("limit", String(Math.max(1, Math.floor(limit))));
  return requestJson(`/chat/turns?${params.toString()}`);
}

// ── Feedback ───────────────────────────────────────────────
export async function submitFeedback(payload: unknown): Promise<unknown> {
  return requestJson("/feedback", json(payload));
}

// ── Delight Ack ────────────────────────────────────────────
export async function markDelightSent(bvid: string): Promise<unknown> {
  return requestJson("/delight/sent", json({ bvid }));
}

// ── Refresh ────────────────────────────────────────────────
export async function refreshRecommendations(): Promise<unknown> {
  return requestJson("/recommendations/refresh", { method: "POST" });
}

// ── Interest Probes ─────────────────────────────────────────
export async function fetchPendingProbes(): Promise<unknown[]> {
  const data = await requestJson("/interest-probes/pending") as { items?: unknown } | null;
  return Array.isArray(data?.items) ? data.items as unknown[] : [];
}

export interface ProbeRespondOptions {
  message?: string;
  surface?: string;
  confirmation_source?: string;
}

export async function respondToProbe(domain: string, responseType: string, options: string | ProbeRespondOptions = {}): Promise<unknown> {
  const payload: Record<string, unknown> = { domain, response: responseType, message: "" };
  if (typeof options === "string") {
    payload.message = options;
  } else if (options && typeof options === "object") {
    payload.message = options.message || "";
    if (options.surface) payload.surface = options.surface;
    if (options.confirmation_source) payload.confirmation_source = options.confirmation_source;
  }
  return requestJson("/interest-probes/respond", {
    ...json(payload),
    timeoutMs: 35_000,
  });
}

// ── Avoidance Probes ────────────────────────────────────────
export async function fetchPendingAvoidanceProbes(): Promise<unknown[]> {
  const data = await requestJson("/avoidance-probes/pending") as { items?: unknown } | null;
  return Array.isArray(data?.items) ? data.items as unknown[] : [];
}

export async function respondToAvoidanceProbe(domain: string, responseType: string, message = ""): Promise<unknown> {
  return requestJson("/avoidance-probes/respond", {
    ...json({ domain, response: responseType, message }),
    timeoutMs: 35_000,
  });
}

// ── Saved lists (platform-neutral /saved/{kind}) ─────────────

function savedListPath(listKind: string): string {
  if (listKind !== "favorite" && listKind !== "watch_later") {
    throw new TypeError(`Unknown saved list: ${listKind}`);
  }
  return `/saved/${listKind}`;
}

// TODO(types): saved items come from heterogeneous platform adapters.
export interface SavedItemInput {
  source_platform?: unknown;
  platform?: unknown;
  bvid?: unknown;
  content_id?: unknown;
  content_url?: unknown;
  url?: unknown;
  content_type?: unknown;
  title?: unknown;
  author_name?: unknown;
  up_name?: unknown;
  author?: unknown;
  cover_url?: unknown;
  note?: unknown;
  [key: string]: unknown;
}

export interface NormalizedSavedItem {
  source_platform: string;
  content_id: string;
  content_url: string;
  content_type: string;
  title: string;
  author_name: string;
  cover_url: string;
  note: string;
}

export function normalizeSavedItemInput(item: SavedItemInput = {}): NormalizedSavedItem {
  const sourcePlatform = String(item.source_platform || item.platform || "bilibili").trim();
  const legacyId = String(item.bvid || "").trim();
  const contentId = String(
    item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : ""),
  ).trim();
  return {
    source_platform: sourcePlatform,
    content_id: contentId,
    content_url: String(item.content_url || item.url || "").trim(),
    content_type: String(
      item.content_type || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
    ).trim(),
    title: String(item.title || "").trim(),
    author_name: String(item.author_name || item.up_name || item.author || "").trim(),
    cover_url: String(item.cover_url || "").trim(),
    note: String(item.note || "").trim(),
  };
}

export async function saveItem(listKind: string, item: SavedItemInput, timeoutMs = SAVED_MUTATION_TIMEOUT_MS): Promise<unknown> {
  return requestJson(savedListPath(listKind), {
    ...json(normalizeSavedItemInput(item)), timeoutMs,
  });
}

export async function removeSavedItem(listKind: string, itemKey: unknown, timeoutMs = SAVED_MUTATION_TIMEOUT_MS): Promise<unknown> {
  return requestJson(`${savedListPath(listKind)}/remove`, {
    ...json({ item_key: String(itemKey || "").trim() }), timeoutMs,
  });
}

export async function fetchSavedItems(listKind: string, limit = 50, offset = 0, timeoutMs = SAVED_READ_TIMEOUT_MS): Promise<unknown> {
  return requestJson(
    `${savedListPath(listKind)}?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
    { timeoutMs },
  );
}

export async function savedItemStatus(listKind: string, itemKey: unknown, timeoutMs = SAVED_READ_TIMEOUT_MS): Promise<unknown> {
  const query = new URLSearchParams({ item_key: String(itemKey || "").trim() });
  return requestJson(`${savedListPath(listKind)}/status?${query}`, { timeoutMs });
}

export async function syncSavedItems(listKind: string, itemKeys: unknown[] = [], timeoutMs = SAVED_MUTATION_TIMEOUT_MS): Promise<unknown> {
  return requestJson(`${savedListPath(listKind)}/sync`, {
    ...json({
      item_keys: Array.from(new Set(itemKeys.map((key) => String(key || "").trim()).filter(Boolean))),
    }),
    timeoutMs,
  });
}

export async function pollSavedSyncTask(taskId: unknown, timeoutMs = SAVED_READ_TIMEOUT_MS): Promise<unknown> {
  return requestJson(`/saved-sync/tasks/${encodeURIComponent(String(taskId || "").trim())}`, {
    timeoutMs,
  });
}
