/** Lean vNext extension runtime: passive activity ingestion plus generic browser tasks. */

import { computeActionBadge } from "./badge.ts";
import { enqueueBufferedEvent, shouldFlushImmediately } from "./buffer.ts";
import {
  BROWSER_SOURCE_OPERATIONS,
  executeBrowserSourceTask,
} from "./browser-source-executor.ts";
import {
  createSourceTaskDispatcher,
  type SourceTaskDispatcher,
  type SourceTaskTransport,
} from "./generic-source-task-dispatcher.ts";
import {
  createApiClient,
  type ActivityEvent,
  type ReadinessResponse,
  type SourceId,
} from "../shared/api-client.ts";
import { normalizeActivityEvent } from "../shared/activity-event.ts";
import { authenticatedFetch, clearSession, ensureSession } from "../shared/auth.ts";
import { getBackendOrigin, onBackendEndpointChange } from "../shared/backend-endpoint.ts";
import type { BehaviorEvent } from "../shared/types.ts";

const EVENT_BUFFER_MAX = 50;
const FLUSH_ALARM = "openbiliclaw-v1-flush";
const SOURCE_TASK_ALARM = "openbiliclaw-v1-source-tasks";
const FLUSH_PERIOD_MINUTES = 0.5;
const SOURCE_TASK_PERIOD_MINUTES = 0.5;

let eventBuffer: BehaviorEvent[] = [];
let flushInFlight: Promise<void> | null = null;
let pollInFlight: Promise<void> | null = null;
let backendReachable: boolean | null = null;
let nextDispatcherIndex = 0;

async function getApiClient() {
  return createApiClient({
    baseUrl: await getBackendOrigin(),
    fetchImpl: authenticatedFetch,
  });
}

const taskTransport: SourceTaskTransport = {
  async claim(sourceId) {
    return (await getApiClient()).request("v1_source_tasks_claim", {
      query: { source_id: sourceId, wait_seconds: 0 },
    });
  },
  async complete(taskId, leaseToken, result) {
    await (await getApiClient()).request("v1_source_tasks_complete", {
      path: { task_id: taskId },
      body: {
        lease_token: leaseToken,
        result: { operation: result.operation, items: result.items },
      },
    });
  },
};

const sourceTaskDispatchers: SourceTaskDispatcher[] = Object.entries(BROWSER_SOURCE_OPERATIONS)
  .filter((entry): entry is [SourceId, NonNullable<(typeof entry)[1]>] => Boolean(entry[1]))
  .map(([sourceId, operations]) => createSourceTaskDispatcher({
    sourceId,
    operations,
    transport: taskTransport,
    execute: executeBrowserSourceTask,
  }));

async function ingestActivity(event: ActivityEvent): Promise<void> {
  await (await getApiClient()).request("v1_events_ingest", { body: event });
}

async function flushEvents(): Promise<void> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = (async () => {
    const pending = eventBuffer;
    eventBuffer = [];
    const failed: BehaviorEvent[] = [];
    for (const behavior of pending) {
      const event = normalizeActivityEvent(behavior);
      if (!event) continue;
      try {
        await ingestActivity(event);
        backendReachable = true;
      } catch {
        failed.push(behavior);
        backendReachable = false;
      }
    }
    if (failed.length > 0) eventBuffer.unshift(...failed);
    renderBadge();
  })().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

async function pollSourceTasks(): Promise<void> {
  if (pollInFlight) return pollInFlight;
  pollInFlight = (async () => {
    for (let offset = 0; offset < sourceTaskDispatchers.length; offset += 1) {
      const index = (nextDispatcherIndex + offset) % sourceTaskDispatchers.length;
      const dispatcher = sourceTaskDispatchers[index]!;
      try {
        const handled = await dispatcher.pollOnce();
        backendReachable = true;
        if (handled) {
          nextDispatcherIndex = (index + 1) % sourceTaskDispatchers.length;
          break;
        }
      } catch (error) {
        console.warn(
          `[OpenBiliClaw] ${dispatcher.sourceId} browser task failed:`,
          error instanceof Error ? error.name : "UnknownError",
        );
      }
    }
    renderBadge();
  })().finally(() => {
    pollInFlight = null;
  });
  return pollInFlight;
}

async function probeBackend(): Promise<void> {
  try {
    const readiness = await (await getApiClient()).request<ReadinessResponse>(
      "v1_system_readiness",
    );
    backendReachable = readiness.ready;
  } catch {
    backendReachable = false;
  }
  renderBadge();
}

function renderBadge(): void {
  const view = computeActionBadge(backendReachable, false);
  void chrome.action.setBadgeText({ text: view.text }).catch(() => undefined);
  if (view.color) {
    void chrome.action.setBadgeBackgroundColor({ color: view.color }).catch(() => undefined);
  }
  void chrome.action.setTitle({ title: view.title }).catch(() => undefined);
}

function ensureAlarms(): void {
  chrome.alarms.create(FLUSH_ALARM, { periodInMinutes: FLUSH_PERIOD_MINUTES });
  chrome.alarms.create(SOURCE_TASK_ALARM, { periodInMinutes: SOURCE_TASK_PERIOD_MINUTES });
}

async function startRuntime(): Promise<void> {
  ensureAlarms();
  await ensureSession();
  await Promise.all([probeBackend(), pollSourceTasks()]);
}

chrome.runtime.onInstalled.addListener(() => {
  void startRuntime();
});

chrome.runtime.onStartup.addListener(() => {
  void startRuntime();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM) void flushEvents();
  if (alarm.name === SOURCE_TASK_ALARM) void pollSourceTasks();
});

chrome.runtime.onMessage.addListener((message: Record<string, unknown>) => {
  if (message.action === "BEHAVIOR_EVENT") {
    const event = message.data as BehaviorEvent;
    eventBuffer = enqueueBufferedEvent(eventBuffer, event, EVENT_BUFFER_MAX);
    if (eventBuffer.length >= EVENT_BUFFER_MAX || shouldFlushImmediately(event)) {
      void flushEvents();
    }
    return false;
  }
  if (message.action === "XHS_URLS_OBSERVED") {
    void ingestXhsObservations(message.data);
    return false;
  }
  return false;
});

async function ingestXhsObservations(value: unknown): Promise<void> {
  if (!value || typeof value !== "object") return;
  const observation = value as Record<string, unknown>;
  const notes = Array.isArray(observation.notes) ? observation.notes : [];
  const urls = Array.isArray(observation.urls) ? observation.urls : [];
  const rows = notes.length > 0 ? notes : urls.map((url) => ({ url }));
  for (const raw of rows) {
    const row = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
    const rawUrl = String(row.url ?? "");
    const externalId = String(row.note_id ?? noteIdFromUrl(rawUrl) ?? "");
    if (!rawUrl && !externalId) continue;
    const event: ActivityEvent = {
      source_id: "xiaohongshu",
      kind: "import",
      occurred_at: new Date(Number(observation.observed_at) || Date.now()).toISOString(),
      content_external_id: externalId || null,
      url: safeObservedUrl(rawUrl),
      title: typeof row.title === "string" ? row.title : null,
      metadata: { page_type: String(observation.page_type ?? "other") },
    };
    try {
      await ingestActivity(event);
    } catch {
      return;
    }
  }
}

function noteIdFromUrl(value: string): string | null {
  return value.match(/\/(?:explore|discovery\/item)\/([0-9a-z]+)/i)?.[1] ?? null;
}

function safeObservedUrl(value: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || !isSourceHost(url.hostname, "xiaohongshu.com")) return null;
    url.searchParams.delete("xsec_token");
    url.searchParams.delete("xsec_source");
    return url.href;
  } catch {
    return null;
  }
}

function isSourceHost(hostname: string, expected: string): boolean {
  return hostname === expected || hostname.endsWith(`.${expected}`);
}

chrome.action.onClicked.addListener((tab) => {
  const chromeApi = chrome as unknown as Record<string, unknown>;
  const sidePanel = chromeApi[["side", "Panel"].join("")] as
    | { open?: (options: { windowId: number }) => Promise<void> }
    | undefined;
  if (sidePanel?.open && typeof tab.windowId === "number") {
    void sidePanel.open({ windowId: tab.windowId });
  }
});

onBackendEndpointChange(() => {
  void clearSession().then(startRuntime);
});

ensureAlarms();
void startRuntime();

console.log("[OpenBiliClaw] vNext service worker initialized");
