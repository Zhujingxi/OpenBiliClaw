import {
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSavePlatform,
  type NativeSaveResult,
  type NativeSaveSlug,
  type NativeSaveTask,
} from "../shared/native-save.ts";
import { releaseDispatcherMutex, tryAcquireDispatcherMutex } from "./dispatcher-mutex.ts";

const DEFAULT_TIMEOUT_MS = 240_000;
const DEFAULT_READINESS_RETRY_MS = 250;
const LOAD_FALLBACK_MS = 10_000;

export interface NativeSaveRunnerOptions {
  timeoutMs?: number;
  readinessRetryMs?: number;
}

interface ActiveTask {
  task: NativeSaveTask;
  tabId: number;
  platform: NativeSavePlatform;
  complete: (outcome: unknown) => void;
  settled: boolean;
}

let activeTask: ActiveTask | null = null;

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(finish, ms);
    function finish(): void {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    }
    signal.addEventListener("abort", finish, { once: true });
  });
}

async function waitForTabLoad(tabId: number): Promise<void> {
  const current = await chrome.tabs.get(tabId).catch(() => null);
  if (current?.status === "complete") return;
  await new Promise<void>((resolve) => {
    let done = false;
    let timer: ReturnType<typeof setTimeout>;
    const finish = (): void => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    };
    const listener = (updatedId: number, info: { status?: string }): void => {
      if (updatedId === tabId && info.status === "complete") finish();
    };
    chrome.tabs.onUpdated.addListener(listener);
    timer = setTimeout(finish, LOAD_FALLBACK_MS);
  });
}

function contentResultForActiveTask(
  message: unknown,
  sender?: chrome.runtime.MessageSender,
): boolean {
  const active = activeTask;
  if (!active || active.settled || typeof message !== "object" || message === null) return false;
  const result = message as Record<string, unknown>;
  if (
    result.type !== "NATIVE_SAVE_RESULT" ||
    result.platform !== active.platform ||
    result.task_id !== active.task.id ||
    result.item_key !== active.task.item_key ||
    sender?.tab?.id !== active.tabId
  ) {
    return false;
  }
  active.settled = true;
  active.complete(result);
  return true;
}

/** Accept a result only when a runner-owned sender is supplied by chrome.runtime. */
export function handleNativeSaveContentResult(
  message: unknown,
  sender?: chrome.runtime.MessageSender,
): boolean {
  return contentResultForActiveTask(message, sender);
}

async function sendExecuteWhenReady(
  tabId: number,
  task: NativeSaveTask,
  deadline: number,
  retryMs: number,
  signal: AbortSignal,
): Promise<void> {
  while (!signal.aborted && Date.now() < deadline) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, {
        type: "NATIVE_SAVE_EXECUTE",
        task,
      });
      if (response !== undefined) {
        return;
      }
    } catch {
      // MV3 can wake the tab before its content listener is registered.
    }
    await delay(retryMs, signal);
  }
}

export async function runNativeSaveTask(
  task: NativeSaveTask,
  platformSlug: NativeSaveSlug,
  postResult: (result: NativeSaveResult) => Promise<void>,
  options: NativeSaveRunnerOptions = {},
): Promise<void> {
  if (!isNativeSaveTask(task) || task.platform_slug !== platformSlug) {
    throw new Error("native-save task does not match the platform slug");
  }
  const owner = `native-save:${platformSlug}`;
  if (!tryAcquireDispatcherMutex(owner)) return;

  let tabId: number | null = null;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  const readinessController = new AbortController();
  const listener = (message: unknown, sender: chrome.runtime.MessageSender): void => {
    handleNativeSaveContentResult(message, sender);
  };
  chrome.runtime.onMessage.addListener(listener);

  try {
    const tab = await chrome.tabs.create({ active: true, url: task.content_url });
    if (tab.id === undefined) throw new Error("native-save task tab has no ID");
    tabId = tab.id;
    await waitForTabLoad(tabId);

    const timeoutMs = Math.max(1, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    const deadline = Date.now() + timeoutMs;
    const terminal = new Promise<unknown>((resolve) => {
      activeTask = { task, tabId: tabId as number, platform: task.platform, complete: resolve, settled: false };
      timeoutId = setTimeout(
        () => resolve({ status: "failed", error_code: "native_save_timeout" }),
        timeoutMs,
      );
    });
    void sendExecuteWhenReady(
      tabId,
      task,
      deadline,
      Math.max(1, options.readinessRetryMs ?? DEFAULT_READINESS_RETRY_MS),
      readinessController.signal,
    );
    const outcome = sanitizeNativeSaveResult(await terminal);
    if (activeTask) activeTask.settled = true;
    await postResult({ task_id: task.id, item_key: task.item_key, ...outcome });
  } finally {
    readinessController.abort();
    if (timeoutId !== null) clearTimeout(timeoutId);
    activeTask = null;
    chrome.runtime.onMessage.removeListener(listener);
    if (tabId !== null) {
      try {
        await chrome.tabs.remove(tabId);
      } catch {
        // The user may have closed the task tab first.
      }
    }
    releaseDispatcherMutex(owner);
  }
}
