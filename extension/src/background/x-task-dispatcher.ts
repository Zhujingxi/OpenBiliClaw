import { apiUrl } from "../shared/backend-endpoint.ts";
import { authenticatedFetch } from "../shared/auth.ts";
import { isNativeSaveTask, type NativeSaveResult, type NativeSaveTask } from "../shared/native-save.ts";
import { runNativeSaveTask } from "./native-save-task-runner.ts";

const DEFAULT_POLL_INTERVAL_MS = 60_000;
const POLL_ALARM_NAME = "openbiliclaw-x-task-poll";
let pollInFlight = false;

export function isValidXTask(task: unknown): task is NativeSaveTask {
  return isNativeSaveTask(task) && task.platform === "twitter" && task.platform_slug === "x";
}

async function fetchNextTask(): Promise<NativeSaveTask | null> {
  try {
    const response = await authenticatedFetch(await apiUrl("/sources/x/next-task"));
    if (response.status === 204 || !response.ok) return null;
    const payload: unknown = await response.json();
    return isValidXTask(payload) ? payload : null;
  } catch {
    return null;
  }
}

async function postTaskResult(result: NativeSaveResult): Promise<void> {
  try {
    await authenticatedFetch(await apiUrl("/sources/x/task-result"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch {
    // Backend transient unavailability should not crash the service worker.
  }
}

export async function executeXTask(task: NativeSaveTask): Promise<void> {
  await runNativeSaveTask(task, "x", postTaskResult);
}

async function pollNextTask(): Promise<void> {
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const task = await fetchNextTask();
    if (task) await executeXTask(task);
  } finally {
    pollInFlight = false;
  }
}

export function startXTaskPolling(): void {
  if (typeof chrome === "undefined" || !chrome.alarms) return;
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: DEFAULT_POLL_INTERVAL_MS / 60_000 });
}

export function handleXTaskAlarm(alarmName: string): void {
  if (alarmName === POLL_ALARM_NAME) void pollNextTask();
}

export function pollXTaskNow(): void {
  void pollNextTask();
}
