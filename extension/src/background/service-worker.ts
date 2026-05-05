/**
 * OpenBiliClaw — Background Service Worker
 *
 * Receives behavior events from content scripts,
 * buffers them, and forwards to the backend API.
 *
 * Delight (surprise) notifications are delivered via WebSocket push
 * from the runtime-stream, not HTTP polling.
 */

import { enqueueBufferedEvent, shouldFlushImmediately } from "./buffer.js";
import {
  startXhsTaskPolling,
  handleXhsTaskAlarm,
  handleTaskResult,
  type XhsTaskResult,
} from "./xhs-task-dispatcher.js";
import {
  openExtensionUi,
  parseDelightBvid,
  parseNotificationBvid,
  parseCognitionUpdateId,
} from "./notifications.js";
import {
  startCookieSync,
  handleCookieSyncAlarm,
  handleCookieSyncRuntimeEvent,
} from "./cookie-sync.js";
import type { BehaviorEvent } from "../shared/types.js";

let eventBuffer: BehaviorEvent[] = [];
const BUFFER_FLUSH_INTERVAL = 30_000;
const BUFFER_MAX_SIZE = 50;
const FLUSH_ALARM_NAME = "openbiliclaw-flush-events";
const BACKEND_URL = "http://localhost:8420/api/events";
const NOTIFICATION_POLL_URL = "http://127.0.0.1:8420/api/notifications/pending";
const NOTIFICATION_ACK_URL = "http://127.0.0.1:8420/api/notifications/sent";
const COGNITION_POLL_URL = "http://127.0.0.1:8420/api/cognition-updates/pending";
const COGNITION_ACK_URL = "http://127.0.0.1:8420/api/cognition-updates/seen";
const DELIGHT_ACK_URL = "http://127.0.0.1:8420/api/delight/sent";
const XHS_OBSERVED_URLS_URL = "http://127.0.0.1:8420/api/sources/xhs/observed-urls";
const XHS_TOKENS_URL = "http://127.0.0.1:8420/api/sources/xhs/tokens";
const RUNTIME_STREAM_URL = "ws://127.0.0.1:8420/api/runtime-stream?client=background";
// v0.3.17+: exponential backoff capped at 60s. When the daemon is
// down for minutes, the previous fixed-5s reconnect flooded console
// with 12 ERR_CONNECTION_REFUSED per minute. Backoff doubles on each
// failure (5s → 10s → 20s → 40s → 60s capped); resets on successful
// onopen so transient blips stay fast-recover.
const WS_RECONNECT_BASE_DELAY = 5_000;
const WS_RECONNECT_MAX_DELAY = 60_000;
let wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
type PendingNotification = import("./notifications.js").PendingNotification;
type PendingCognitionUpdate = import("./notifications.js").PendingCognitionUpdate;

// ---------------------------------------------------------------------------
// HTTP helpers (recommendation & cognition — still polled)
// ---------------------------------------------------------------------------

async function acknowledgeNotificationSent(bvid: string): Promise<void> {
  if (!bvid) return;
  await fetch(NOTIFICATION_ACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

async function fetchPendingNotification(): Promise<PendingNotification | null> {
  const response = await fetch(NOTIFICATION_POLL_URL, { method: "GET" });
  if (!response.ok) {
    throw new Error(`pending notifications failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingNotification | null };
  return payload.item ?? null;
}

async function fetchPendingCognitionUpdate(): Promise<PendingCognitionUpdate | null> {
  const response = await fetch(COGNITION_POLL_URL, { method: "GET" });
  if (!response.ok) {
    throw new Error(`pending cognition updates failed: ${response.status}`);
  }
  const payload = (await response.json()) as { item?: PendingCognitionUpdate | null };
  return payload.item ?? null;
}

async function acknowledgeCognitionUpdateSeen(id: string): Promise<void> {
  if (!id) return;
  await fetch(COGNITION_ACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
}

// ---------------------------------------------------------------------------
// Delight ACK (HTTP POST after WS push triggers notification)
// ---------------------------------------------------------------------------

async function acknowledgeDelightSent(bvid: string): Promise<void> {
  if (!bvid) return;
  await fetch(DELIGHT_ACK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bvid }),
  });
}

// ---------------------------------------------------------------------------
// Polling — recommendation & cognition only (delight is WS-pushed)
// ---------------------------------------------------------------------------

/**
 * v0.3.16+: OS-level Chrome toasts are disabled by user request.
 *
 * The popup / side panel already surfaces every recommendation,
 * cognition update, delight candidate and interest probe — duplicating
 * them as Chrome toasts at the bottom-right of the screen is intrusive
 * (and tripped a recurring "Unable to download all specified images"
 * Chromium bug that polluted the service-worker console for weeks).
 *
 * We still poll ``/api/notifications/pending`` and call the ack
 * endpoints so the backend's pending queue drains. Functionally this
 * just hides the OS toast surface; popup state is unchanged.
 */
async function checkPendingNotification(): Promise<void> {
  try {
    const item = await fetchPendingNotification();
    if (item?.bvid) {
      await acknowledgeNotificationSent(item.bvid);
      return;
    }
    const cognition = await fetchPendingCognitionUpdate();
    if (cognition?.id) {
      await acknowledgeCognitionUpdateSeen(cognition.id);
    }
  } catch (err) {
    console.warn(
      "[OpenBiliClaw] Pending notification ack failed:",
      err instanceof Error ? err.message : String(err),
    );
  }
}

// ---------------------------------------------------------------------------
// WebSocket — runtime stream for delight push notifications
// ---------------------------------------------------------------------------

let runtimeSocket: WebSocket | null = null;
let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

function handleRuntimeEvent(event: Record<string, unknown>): void {
  if (handleCookieSyncRuntimeEvent(event)) return;

  const eventType = String(event.type ?? "");

  // v0.3.16+: OS-level Chrome toasts are disabled by user request.
  // Both interest.probe and delight.candidate surface inside the
  // popup via its own runtime-stream WS handler — no chrome
  // notification toast at the bottom-right of the screen.
  if (eventType === "interest.probe") {
    return;
  }

  if (eventType !== "delight.candidate") return;

  const bvid = String(event.bvid ?? "");
  if (!bvid) return;

  // Still ack the backend so the same bvid isn't re-pushed forever.
  void acknowledgeDelightSent(bvid);
}

function connectRuntimeStream(): void {
  if (runtimeSocket !== null) return;

  try {
    runtimeSocket = new WebSocket(RUNTIME_STREAM_URL);
  } catch {
    scheduleWsReconnect();
    return;
  }

  runtimeSocket.onopen = () => {
    // v0.3.17+: reset backoff on successful connect so a transient
    // blip after a long outage still recovers immediately.
    wsReconnectDelay = WS_RECONNECT_BASE_DELAY;
  };

  runtimeSocket.onmessage = (msg) => {
    try {
      const payload = JSON.parse(String(msg.data)) as Record<string, unknown>;
      handleRuntimeEvent(payload);
    } catch {
      // Ignore malformed payloads.
    }
  };

  runtimeSocket.onclose = () => {
    runtimeSocket = null;
    scheduleWsReconnect();
  };

  runtimeSocket.onerror = () => {
    runtimeSocket?.close();
  };
}

function scheduleWsReconnect(): void {
  if (wsReconnectTimer !== null) return;
  const delay = wsReconnectDelay;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectRuntimeStream();
  }, delay);
  // Double for next failure, capped. Resets in onopen above.
  wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX_DELAY);
}

// ---------------------------------------------------------------------------
// Event buffer flush
// ---------------------------------------------------------------------------

async function flushEvents(): Promise<void> {
  if (eventBuffer.length === 0) return;

  const events = [...eventBuffer];
  eventBuffer = [];

  try {
    const response = await fetch(BACKEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events }),
    });

    if (!response.ok) {
      console.warn("[OpenBiliClaw] Backend returned", response.status);
      eventBuffer.unshift(...events);
      return;
    }
    await checkPendingNotification();
  } catch {
    console.warn("[OpenBiliClaw] Backend not available, buffering events");
    eventBuffer.unshift(...events);
  }
}

// ---------------------------------------------------------------------------
// Alarm & lifecycle
// ---------------------------------------------------------------------------

function ensureFlushAlarm(): void {
  chrome.alarms.create(FLUSH_ALARM_NAME, {
    periodInMinutes: BUFFER_FLUSH_INTERVAL / 60_000,
  });
}

chrome.runtime.onInstalled.addListener(() => {
  ensureFlushAlarm();
  connectRuntimeStream();
  startXhsTaskPolling();
  startCookieSync();
});

chrome.runtime.onStartup.addListener(() => {
  ensureFlushAlarm();
  connectRuntimeStream();
  startXhsTaskPolling();
  startCookieSync();
});

chrome.action.onClicked.addListener((tab) => {
  void openExtensionUi(chrome, {
    windowId: tab.windowId,
    tab: "recommend",
  });
});

async function postXhsObservedUrls(payload: Record<string, unknown>): Promise<void> {
  try {
    await fetch(XHS_OBSERVED_URLS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Best-effort — missing a batch just means less enrichment coverage.
  }
}

async function postXhsTokens(
  payload: { pairs: Array<{ note_id: string; xsec_token: string }> },
): Promise<void> {
  if (!payload?.pairs || payload.pairs.length === 0) return;
  try {
    await fetch(XHS_TOKENS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Best-effort — tokens that don't land just stay as bare URLs for now.
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.action === "XHS_URLS_OBSERVED") {
    void postXhsObservedUrls(message.data as Record<string, unknown>);
    return;
  }
  if (message.action === "XHS_TOKENS_OBSERVED") {
    void postXhsTokens(
      message.data as { pairs: Array<{ note_id: string; xsec_token: string }> },
    );
    return;
  }
  if (message.action === "XHS_TASK_RESULT") {
    void handleTaskResult(message.data as XhsTaskResult)
      .then(() => {
        sendResponse({ ok: true });
      })
      .catch((error: unknown) => {
        sendResponse({ ok: false, error: String(error) });
      });
    return true;
  }
  if (message.action !== "BEHAVIOR_EVENT") return;

  eventBuffer = enqueueBufferedEvent(eventBuffer, message.data as BehaviorEvent, BUFFER_MAX_SIZE);

  if (eventBuffer.length >= BUFFER_MAX_SIZE || shouldFlushImmediately(message.data as BehaviorEvent)) {
    void flushEvents();
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  handleXhsTaskAlarm(alarm.name);
  if (handleCookieSyncAlarm(alarm.name)) {
    return;
  }
  if (alarm.name === FLUSH_ALARM_NAME) {
    if (eventBuffer.length > 0) {
      void flushEvents();
      return;
    }
    void checkPendingNotification();
  }
});

chrome.notifications.onClicked.addListener((notificationId) => {
  if (notificationId.startsWith("openbiliclaw-probe:")) {
    void openExtensionUi(chrome, { tab: "profile" });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const bvid = parseNotificationBvid(notificationId);
  if (bvid) {
    void openExtensionUi(chrome, { tab: "recommend" });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const delightBvid = parseDelightBvid(notificationId);
  if (delightBvid) {
    void openExtensionUi(chrome, { tab: "recommend", delightBvid });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const cognitionId = parseCognitionUpdateId(notificationId);
  if (!cognitionId) {
    return;
  }
  void openExtensionUi(chrome, { tab: "profile" });
  void chrome.notifications.clear(notificationId);
});

ensureFlushAlarm();
connectRuntimeStream();
startCookieSync();

console.log("[OpenBiliClaw] Service worker initialized");
