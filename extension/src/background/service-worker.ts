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
  openExtensionUi,
  buildChromeNotificationOptions,
  buildCognitionNotificationId,
  buildDelightNotificationId,
  buildNotificationId,
  parseDelightBvid,
  parseNotificationBvid,
  parseCognitionUpdateId,
} from "./notifications.js";
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
const RUNTIME_STREAM_URL = "ws://127.0.0.1:8420/api/runtime-stream";
const WS_RECONNECT_DELAY = 5_000;
type PendingNotification = import("./notifications.js").PendingNotification;
type PendingCognitionUpdate = import("./notifications.js").PendingCognitionUpdate;
type PendingDelight = import("./notifications.js").PendingDelight;

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

async function checkPendingNotification(): Promise<void> {
  try {
    const item = await fetchPendingNotification();
    if (item?.bvid) {
      await chrome.notifications.create(
        buildNotificationId(item.bvid),
        buildChromeNotificationOptions(item),
      );
      await acknowledgeNotificationSent(item.bvid);
      return;
    }
    const cognition = await fetchPendingCognitionUpdate();
    if (cognition?.id) {
      await chrome.notifications.create(
        buildCognitionNotificationId(cognition.id),
        buildChromeNotificationOptions(cognition),
      );
      await acknowledgeCognitionUpdateSeen(cognition.id);
    }
  } catch {
    console.warn("[OpenBiliClaw] Pending notification check failed");
  }
}

// ---------------------------------------------------------------------------
// WebSocket — runtime stream for delight push notifications
// ---------------------------------------------------------------------------

let runtimeSocket: WebSocket | null = null;
let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

function handleRuntimeEvent(event: Record<string, unknown>): void {
  const eventType = String(event.type ?? "");
  if (eventType !== "delight.candidate") return;

  const bvid = String(event.bvid ?? "");
  if (!bvid) return;

  const delight: PendingDelight = {
    bvid,
    title: String(event.title ?? ""),
    delight_reason: String(event.delight_reason ?? ""),
    delight_score: Number(event.delight_score ?? 0),
    delight_hook: String(event.delight_hook ?? ""),
    cover_url: String(event.cover_url ?? ""),
  };

  void chrome.notifications.create(
    buildDelightNotificationId(delight.bvid),
    buildChromeNotificationOptions(delight),
  );
  void acknowledgeDelightSent(delight.bvid);
}

function connectRuntimeStream(): void {
  if (runtimeSocket !== null) return;

  try {
    runtimeSocket = new WebSocket(RUNTIME_STREAM_URL);
  } catch {
    scheduleWsReconnect();
    return;
  }

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
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectRuntimeStream();
  }, WS_RECONNECT_DELAY);
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
});

chrome.runtime.onStartup.addListener(() => {
  ensureFlushAlarm();
  connectRuntimeStream();
});

chrome.action.onClicked.addListener((tab) => {
  void openExtensionUi(chrome, {
    windowId: tab.windowId,
    tab: "recommend",
  });
});

chrome.runtime.onMessage.addListener((message) => {
  if (message.action !== "BEHAVIOR_EVENT") return;

  eventBuffer = enqueueBufferedEvent(eventBuffer, message.data as BehaviorEvent, BUFFER_MAX_SIZE);

  if (eventBuffer.length >= BUFFER_MAX_SIZE || shouldFlushImmediately(message.data as BehaviorEvent)) {
    void flushEvents();
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM_NAME) {
    if (eventBuffer.length > 0) {
      void flushEvents();
      return;
    }
    void checkPendingNotification();
  }
});

chrome.notifications.onClicked.addListener((notificationId) => {
  const bvid = parseNotificationBvid(notificationId);
  if (bvid) {
    void openExtensionUi(chrome, { tab: "recommend" });
    void chrome.notifications.clear(notificationId);
    return;
  }
  const delightBvid = parseDelightBvid(notificationId);
  if (delightBvid) {
    void openExtensionUi(chrome, { tab: "recommend" });
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

console.log("[OpenBiliClaw] Service worker initialized");
