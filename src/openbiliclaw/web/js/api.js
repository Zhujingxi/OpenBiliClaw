const BASE_URL = `${location.protocol}//${location.host}/api`;
const DEFAULT_READ_TIMEOUT_MS = 12e3;
const QUICK_READ_TIMEOUT_MS = 5e3;
const CONFIG_WRITE_TIMEOUT_MS = 6e4;
const MODEL_WRITE_TIMEOUT_MS = 6e4;
const SAVED_READ_TIMEOUT_MS = 1e4;
const SAVED_MUTATION_TIMEOUT_MS = 1e4;
const CSRF_HEADER = "X-OBC-Auth";
function signalAuthRequired() {
  try {
    window.dispatchEvent(new CustomEvent("obc:auth-required"));
  } catch {
  }
}
function abortError(message = "Request aborted") {
  if (typeof DOMException === "function") {
    return new DOMException(message, "AbortError");
  }
  const error = new Error(message);
  error.name = "AbortError";
  return error;
}
function withTimeout(signal, timeoutMs) {
  const hasTimeout = Number.isFinite(timeoutMs) && timeoutMs > 0;
  if (!hasTimeout && !signal) return { signal: void 0, cleanup() {
  } };
  if (!hasTimeout) return { signal: signal ?? void 0, cleanup() {
  } };
  const controller = new AbortController();
  let tid = null;
  const abort = (reason) => {
    if (!controller.signal.aborted) controller.abort(reason || abortError());
  };
  const onCaller = () => abort(signal?.reason);
  if (signal?.aborted) abort(signal.reason);
  else if (signal) signal.addEventListener("abort", onCaller, { once: true });
  tid = setTimeout(() => abort(abortError("Request timed out")), timeoutMs);
  return {
    signal: controller.signal,
    cleanup() {
      if (tid !== null) clearTimeout(tid);
      if (signal) signal.removeEventListener("abort", onCaller);
    }
  };
}
export async function requestJson(path, options = {}) {
  const { timeoutMs, signal, ...fetchOptions } = options;
  const init = { ...fetchOptions };
  const timeout = withTimeout(signal, timeoutMs);
  if (timeout.signal) init.signal = timeout.signal;
  init.credentials = "same-origin";
  init.headers = { ...init.headers || {}, [CSRF_HEADER]: "1" };
  try {
    const res = await fetch(`${BASE_URL}${path}`, init);
    if (!res.ok) {
      let details = null;
      try {
        details = await res.json();
      } catch {
        details = null;
      }
      if (res.status === 401) signalAuthRequired();
      const err = new Error(`${path} failed: ${res.status}`);
      err.status = res.status;
      err.details = details;
      throw err;
    }
    return res.json();
  } finally {
    timeout.cleanup();
  }
}
export async function fetchAuthStatus() {
  try {
    return await requestJson("/auth/status", { timeoutMs: QUICK_READ_TIMEOUT_MS });
  } catch {
    return { enabled: false, authenticated: true };
  }
}
export async function login(password) {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password })
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { ok: res.ok && Boolean(data?.ok), status: res.status, data };
}
export async function logout() {
  try {
    await fetch(`${BASE_URL}/auth/logout`, { method: "POST", credentials: "same-origin" });
  } catch {
  }
}
const json = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body)
});
export async function fetchHealth() {
  return requestJson("/health");
}
export async function checkHealth() {
  try {
    const res = await fetch(`${BASE_URL}/health`, { method: "GET" });
    return res.ok;
  } catch {
    return false;
  }
}
export async function fetchConfig(timeoutMs = DEFAULT_READ_TIMEOUT_MS) {
  return requestJson("/config", { timeoutMs });
}
export async function updateConfig(data, timeoutMs = CONFIG_WRITE_TIMEOUT_MS) {
  return requestJson("/config", {
    method: "PUT",
    timeoutMs,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
}
export async function fetchModelConfig(timeoutMs = DEFAULT_READ_TIMEOUT_MS) {
  return requestJson("/model-config", { timeoutMs });
}
export async function fetchModelConnectionTypes(capability = "", timeoutMs = DEFAULT_READ_TIMEOUT_MS) {
  if (!capability) {
    return requestJson("/model-connection-types", { timeoutMs });
  }
  const query = new URLSearchParams({ capability: String(capability) });
  return requestJson(`/model-connection-types?${query}`, { timeoutMs });
}
export async function updateModelConfig(data, timeoutMs = MODEL_WRITE_TIMEOUT_MS) {
  return requestJson("/model-config", {
    method: "PUT",
    timeoutMs,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
}
export async function probeModelConnection(data, timeoutMs = MODEL_WRITE_TIMEOUT_MS) {
  return requestJson("/model-config/probe", {
    method: "POST",
    timeoutMs,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  });
}
export async function fetchRecommendations() {
  const data = await requestJson("/recommendations", { timeoutMs: DEFAULT_READ_TIMEOUT_MS });
  return Array.isArray(data.items) ? data.items : [];
}
export async function reshuffleRecommendations() {
  const data = await requestJson("/recommendations/reshuffle", { method: "POST" });
  return { ...data, items: Array.isArray(data.items) ? data.items : [] };
}
export async function appendRecommendations(excludedBvids = []) {
  const data = await requestJson("/recommendations/append", json({ excluded_bvids: excludedBvids }));
  return { ...data, items: Array.isArray(data.items) ? data.items : [] };
}
export async function reportClick(payload) {
  try {
    await requestJson("/recommendation-click", json(payload));
    return true;
  } catch {
    return false;
  }
}
export async function fetchRuntimeStatus() {
  return requestJson("/runtime-status", { timeoutMs: QUICK_READ_TIMEOUT_MS });
}
export async function fetchDelightBatch(limit = null) {
  const params = new URLSearchParams();
  if (typeof limit === "number" && Number.isFinite(limit)) {
    params.set("limit", String(Math.max(1, Math.min(100, Math.floor(limit)))));
  }
  const qs = params.toString();
  const data = await requestJson(`/delight/pending-batch${qs ? `?${qs}` : ""}`, { timeoutMs: DEFAULT_READ_TIMEOUT_MS });
  return Array.isArray(data?.items) ? data.items : [];
}
export async function respondToDelight(bvid, responseType, title = "", message = "") {
  return requestJson("/delight/respond", {
    ...json({ bvid, response: responseType, title, message }),
    timeoutMs: 35e3
  });
}
export async function fetchProfileSummary({ limit, cursor } = {}) {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (typeof cursor === "string" && cursor.trim()) params.set("cursor", cursor.trim());
  const qs = params.toString();
  return requestJson(`/profile-summary${qs ? `?${qs}` : ""}`);
}
export async function fetchEditState() {
  return requestJson("/profile/edit-state");
}
export async function submitProfileEdit({ target, op, value = null, parent = "", weight = null }) {
  return requestJson("/profile/edit", {
    ...json({ target, op, value, parent, weight }),
    timeoutMs: 35e3
  });
}
export async function submitInsightFeedback(hypothesis, signal) {
  return requestJson("/insights/feedback", {
    ...json({ hypothesis, signal }),
    timeoutMs: 35e3
  });
}
export async function fetchPendingNotifications() {
  return requestJson("/notifications/pending");
}
export async function ackNotification(bvid) {
  return requestJson("/notifications/sent", json({ bvid }));
}
export async function fetchActivityFeed({ limit, before } = {}) {
  const params = new URLSearchParams();
  if (typeof limit === "number") params.set("limit", String(limit));
  if (before) params.set("before", before);
  const qs = params.toString();
  return requestJson(`/activity-feed${qs ? `?${qs}` : ""}`, { timeoutMs: QUICK_READ_TIMEOUT_MS });
}
export async function startChatTurn({ turnId = "", session = "mobile", scope = "chat", subjectId = "", subjectTitle = "", message }) {
  return requestJson("/chat/turns", json({
    turn_id: turnId,
    session,
    scope,
    subject_id: subjectId,
    subject_title: subjectTitle,
    message
  }));
}
export async function fetchChatTurn(turnId) {
  return requestJson(`/chat/turns/${encodeURIComponent(turnId)}`);
}
export async function fetchChatTurns({ session = "mobile", scope = "", limit = 50 } = {}) {
  const params = new URLSearchParams();
  params.set("session", session);
  if (scope) params.set("scope", scope);
  if (typeof limit === "number") params.set("limit", String(Math.max(1, Math.floor(limit))));
  return requestJson(`/chat/turns?${params.toString()}`);
}
export async function submitFeedback(payload) {
  return requestJson("/feedback", json(payload));
}
export async function markDelightSent(bvid) {
  return requestJson("/delight/sent", json({ bvid }));
}
export async function refreshRecommendations() {
  return requestJson("/recommendations/refresh", { method: "POST" });
}
export async function fetchPendingProbes() {
  const data = await requestJson("/interest-probes/pending");
  return Array.isArray(data?.items) ? data.items : [];
}
export async function respondToProbe(domain, responseType, options = {}) {
  const payload = { domain, response: responseType, message: "" };
  if (typeof options === "string") {
    payload.message = options;
  } else if (options && typeof options === "object") {
    payload.message = options.message || "";
    if (options.surface) payload.surface = options.surface;
    if (options.confirmation_source) payload.confirmation_source = options.confirmation_source;
  }
  return requestJson("/interest-probes/respond", {
    ...json(payload),
    timeoutMs: 35e3
  });
}
export async function fetchPendingAvoidanceProbes() {
  const data = await requestJson("/avoidance-probes/pending");
  return Array.isArray(data?.items) ? data.items : [];
}
export async function respondToAvoidanceProbe(domain, responseType, message = "") {
  return requestJson("/avoidance-probes/respond", {
    ...json({ domain, response: responseType, message }),
    timeoutMs: 35e3
  });
}
function savedListPath(listKind) {
  if (listKind !== "favorite" && listKind !== "watch_later") {
    throw new TypeError(`Unknown saved list: ${listKind}`);
  }
  return `/saved/${listKind}`;
}
export function normalizeSavedItemInput(item = {}) {
  const sourcePlatform = String(item.source_platform || item.platform || "bilibili").trim();
  const legacyId = String(item.bvid || "").trim();
  const contentId = String(
    item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : "")
  ).trim();
  return {
    source_platform: sourcePlatform,
    content_id: contentId,
    content_url: String(item.content_url || item.url || "").trim(),
    content_type: String(
      item.content_type || (sourcePlatform === "bilibili" && contentId ? "video" : "")
    ).trim(),
    title: String(item.title || "").trim(),
    author_name: String(item.author_name || item.up_name || item.author || "").trim(),
    cover_url: String(item.cover_url || "").trim(),
    note: String(item.note || "").trim()
  };
}
export async function saveItem(listKind, item, timeoutMs = SAVED_MUTATION_TIMEOUT_MS) {
  return requestJson(savedListPath(listKind), {
    ...json(normalizeSavedItemInput(item)),
    timeoutMs
  });
}
export async function removeSavedItem(listKind, itemKey, timeoutMs = SAVED_MUTATION_TIMEOUT_MS) {
  return requestJson(`${savedListPath(listKind)}/remove`, {
    ...json({ item_key: String(itemKey || "").trim() }),
    timeoutMs
  });
}
export async function fetchSavedItems(listKind, limit = 50, offset = 0, timeoutMs = SAVED_READ_TIMEOUT_MS) {
  return requestJson(
    `${savedListPath(listKind)}?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
    { timeoutMs }
  );
}
export async function savedItemStatus(listKind, itemKey, timeoutMs = SAVED_READ_TIMEOUT_MS) {
  const query = new URLSearchParams({ item_key: String(itemKey || "").trim() });
  return requestJson(`${savedListPath(listKind)}/status?${query}`, { timeoutMs });
}
export async function syncSavedItems(listKind, itemKeys = [], timeoutMs = SAVED_MUTATION_TIMEOUT_MS) {
  return requestJson(`${savedListPath(listKind)}/sync`, {
    ...json({
      item_keys: Array.from(new Set(itemKeys.map((key) => String(key || "").trim()).filter(Boolean)))
    }),
    timeoutMs
  });
}
export async function pollSavedSyncTask(taskId, timeoutMs = SAVED_READ_TIMEOUT_MS) {
  return requestJson(`/saved-sync/tasks/${encodeURIComponent(String(taskId || "").trim())}`, {
    timeoutMs
  });
}
//# sourceMappingURL=api.js.map
