/** Lean vNext extension runtime: passive activity ingestion plus generic browser tasks. */

import { computeActionBadge } from "./badge.ts";
import { deliverActivityEvent } from "./activity-delivery.ts";
import { shouldFlushImmediately } from "./buffer.ts";
import {
  browserOperationsFromManifests,
  executeBrowserSourceTask,
} from "./browser-source-executor.ts";
import {
  createSourceTaskDispatcher,
  type SourceTaskDispatcher,
  type SourceTaskTransport,
} from "./generic-source-task-dispatcher.ts";
import { createDurableOutbox } from "./durable-outbox.ts";
import { xhsObservationEvents } from "./xhs-observation-events.ts";
import {
  deliverTaskCompletion,
  type PendingTaskCompletion,
} from "./task-completion.ts";
import {
  createApiClient,
  type ActivityEvent,
  type ReadinessResponse,
  type SourceId,
  type SourceManifest,
} from "../shared/api-client.ts";
import {
  normalizeActivityEvent,
  type IdentifiedActivityEvent,
} from "../shared/activity-event.ts";
import { authenticatedFetch, clearSession, ensureSession } from "../shared/auth.ts";
import { getBackendOrigin, onBackendEndpointChange } from "../shared/backend-endpoint.ts";
import type { BehaviorEvent } from "../shared/types.ts";

const FLUSH_ALARM = "openbiliclaw-v1-flush";
const SOURCE_TASK_ALARM = "openbiliclaw-v1-source-tasks";
const FLUSH_PERIOD_MINUTES = 0.5;
const SOURCE_TASK_PERIOD_MINUTES = 0.5;

let flushInFlight: Promise<void> | null = null;
let pollInFlight: Promise<void> | null = null;
let backendReachable: boolean | null = null;
let nextDispatcherIndex = 0;

const activityOutbox = createDurableOutbox<IdentifiedActivityEvent>({
  storage: chrome.storage.local,
  storageKey: "vnext_activity_outbox",
});

type DeadLetterActivity = {
  readonly id: string;
  readonly event: IdentifiedActivityEvent;
  readonly status: number;
  readonly deadLetteredAt: string;
};

const activityDeadLetters = createDurableOutbox<DeadLetterActivity>({
  storage: chrome.storage.local,
  storageKey: "vnext_activity_dead_letters",
});

const taskCompletionOutbox = createDurableOutbox<PendingTaskCompletion>({
  storage: chrome.storage.local,
  storageKey: "vnext_source_task_completion_outbox",
});

type DeadLetterTaskCompletion = {
  readonly id: string;
  readonly completion: PendingTaskCompletion;
  readonly status: number;
  readonly deadLetteredAt: string;
};

const taskCompletionDeadLetters = createDurableOutbox<DeadLetterTaskCompletion>({
  storage: chrome.storage.local,
  storageKey: "vnext_source_task_completion_dead_letters",
});

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
    await taskCompletionOutbox.enqueue({ id: taskId, leaseToken, outcome: { result } });
    await flushTaskCompletions();
  },
  async fail(taskId, leaseToken, failure) {
    await taskCompletionOutbox.enqueue({ id: taskId, leaseToken, outcome: { failure } });
    await flushTaskCompletions();
  },
};

async function flushTaskCompletions(): Promise<void> {
  await taskCompletionOutbox.flush(async (completion) => {
    await deliverTaskCompletion(completion, async (body) => {
      await (await getApiClient()).request("v1_source_tasks_complete", {
        path: { task_id: completion.id },
        body,
      });
    }, async (terminalCompletion, status) => {
      await taskCompletionDeadLetters.enqueue({
        id: terminalCompletion.id,
        completion: terminalCompletion,
        status,
        deadLetteredAt: new Date().toISOString(),
      });
    });
  });
}

let sourceTaskDispatchers: SourceTaskDispatcher[] = [];

async function refreshSourceTaskDispatchers(): Promise<void> {
  const manifests = await (await getApiClient()).request<ReadonlyArray<SourceManifest>>(
    "v1_sources_list",
  );
  const operationsBySource = browserOperationsFromManifests(manifests);
  sourceTaskDispatchers = Object.entries(operationsBySource)
    .filter((entry): entry is [SourceId, NonNullable<(typeof entry)[1]>] => Boolean(entry[1]))
    .map(([sourceId, operations]) => createSourceTaskDispatcher({
    sourceId,
    operations,
    transport: taskTransport,
    execute: executeBrowserSourceTask,
    }));
  if (sourceTaskDispatchers.length === 0) nextDispatcherIndex = 0;
  else nextDispatcherIndex %= sourceTaskDispatchers.length;
}

async function ingestActivity(event: ActivityEvent): Promise<void> {
  await (await getApiClient()).request("v1_events_ingest", { body: event });
}

async function flushEvents(): Promise<void> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = (async () => {
    try {
      await activityOutbox.flush((event) => deliverActivityEvent(
        event,
        ingestActivity,
        async (terminalEvent, status) => {
          await activityDeadLetters.enqueue({
            id: terminalEvent.id,
            event: terminalEvent,
            status,
            deadLetteredAt: new Date().toISOString(),
          });
        },
      ));
      backendReachable = true;
    } catch {
      backendReachable = false;
    }
    renderBadge();
  })().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

async function pollSourceTasks(): Promise<void> {
  if (pollInFlight) return pollInFlight;
  pollInFlight = (async () => {
    try {
      await flushTaskCompletions();
    } catch {
      backendReachable = false;
      renderBadge();
      return;
    }
    try {
      await refreshSourceTaskDispatchers();
      backendReachable = true;
    } catch {
      sourceTaskDispatchers = [];
      backendReachable = false;
      return;
    }
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
  await Promise.all([flushEvents(), probeBackend(), pollSourceTasks()]);
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

chrome.runtime.onMessage.addListener((message: Record<string, unknown>, _sender, sendResponse) => {
  if (message.action === "BEHAVIOR_EVENT") {
    const event = message.data as BehaviorEvent;
    const normalized = normalizeActivityEvent(event);
    if (!normalized?.id) return false;
    void activityOutbox.enqueue(normalized)
      .then(() => shouldFlushImmediately(event) ? flushEvents() : undefined)
      .then(() => sendResponse({ accepted: true }))
      .catch(() => {
        backendReachable = false;
        renderBadge();
        sendResponse({ accepted: false });
      });
    return true;
  }
  if (message.action === "XHS_URLS_OBSERVED") {
    void ingestXhsObservations(message.data)
      .then(() => sendResponse({ accepted: true }))
      .catch(() => sendResponse({ accepted: false }));
    return true;
  }
  return false;
});

async function ingestXhsObservations(value: unknown): Promise<void> {
  for (const event of xhsObservationEvents(value)) await activityOutbox.enqueue(event);
  await flushEvents();
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
