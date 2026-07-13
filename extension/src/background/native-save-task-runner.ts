import {
  isAllowedNativeSavePageUrl,
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSavePlatform,
  type NativeSaveResult,
  type NativeSaveSlug,
  type NativeSaveTask,
  type SanitizedNativeSaveOutcome,
} from "../shared/native-save.ts";
import { releaseDispatcherMutex, tryAcquireDispatcherMutex } from "./dispatcher-mutex.ts";

const DEFAULT_TIMEOUT_MS = 240_000;
const DEFAULT_READINESS_RETRY_MS = 250;
const DEFAULT_MUTEX_RETRY_MS = 50;
const NATIVE_SAVE_TAB_SESSION_KEY = "openbiliclaw_native_save_task_tab_id";

export interface NativeSaveRunnerOptions {
  timeoutMs?: number;
  readinessRetryMs?: number;
  mutexRetryMs?: number;
}

interface ActiveTask {
  task: NativeSaveTask;
  tabId: number;
  platform: NativeSavePlatform;
  complete: (outcome: unknown) => void;
  settled: boolean;
}

class NativeSaveDeadlineError extends Error {}

let activeTask: ActiveTask | null = null;
let nativeSaveRecoveryPromise: Promise<void> | null = null;

function timeoutOutcome(): SanitizedNativeSaveOutcome {
  return sanitizeNativeSaveResult({ status: "failed", error_code: "native_save_timeout" });
}

function failedOutcome(): SanitizedNativeSaveOutcome {
  return sanitizeNativeSaveResult({ status: "failed", error_code: "native_save_failed" });
}

function remainingMs(deadline: number): number {
  return Math.max(0, deadline - Date.now());
}

async function beforeDeadline<T>(promise: Promise<T>, deadline: number): Promise<T> {
  const timeoutMs = remainingMs(deadline);
  if (timeoutMs <= 0) throw new NativeSaveDeadlineError("native-save task timed out");
  let timer: ReturnType<typeof setTimeout>;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(
          () => reject(new NativeSaveDeadlineError("native-save task timed out")),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer!);
  }
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(finish, ms);
    function finish(): void {
      clearTimeout(timer);
      signal?.removeEventListener("abort", finish);
      resolve();
    }
    signal?.addEventListener("abort", finish, { once: true });
  });
}

async function acquireMutexBefore(
  owner: string,
  deadline: number,
  retryMs: number,
): Promise<boolean> {
  while (Date.now() < deadline) {
    if (tryAcquireDispatcherMutex(owner)) return true;
    await delay(Math.min(retryMs, remainingMs(deadline)));
  }
  return false;
}

async function waitForTabLoad(tabId: number, deadline: number): Promise<chrome.tabs.Tab> {
  let revision = 0;
  let wake: (() => void) | null = null;
  const listener = (updatedId: number): void => {
    if (updatedId !== tabId) return;
    revision += 1;
    wake?.();
  };
  let registrationAttempted = false;
  try {
    registrationAttempted = true;
    chrome.tabs.onUpdated.addListener(listener);
    while (Date.now() < deadline) {
      const observedRevision = revision;
      const tab = await beforeDeadline(chrome.tabs.get(tabId), deadline);
      if (tab.status === "complete") return tab;
      if (revision !== observedRevision) continue;
      await new Promise<void>((resolve, reject) => {
        const timeoutMs = remainingMs(deadline);
        if (timeoutMs <= 0) {
          reject(new NativeSaveDeadlineError("native-save tab load timed out"));
          return;
        }
        const timer = setTimeout(() => {
          wake = null;
          reject(new NativeSaveDeadlineError("native-save tab load timed out"));
        }, timeoutMs);
        wake = () => {
          clearTimeout(timer);
          wake = null;
          resolve();
        };
        if (revision !== observedRevision) wake();
      });
    }
    throw new NativeSaveDeadlineError("native-save tab load timed out");
  } finally {
    if (registrationAttempted) {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          chrome.tabs.onUpdated.removeListener(listener);
          break;
        } catch {
          // Retry one transient failure, then let the runner continue teardown.
        }
      }
    }
  }
}

async function removeTabBestEffort(tabId: number): Promise<void> {
  try {
    await chrome.tabs.remove(tabId);
  } catch {
    // The user may have closed the tab, or Chrome cleanup may fail.
  }
}

function sessionStorageArea(): chrome.storage.StorageArea | null {
  try {
    return typeof chrome === "undefined" ? null : chrome.storage?.session ?? null;
  } catch {
    return null;
  }
}

async function recordNativeSaveTaskTab(tabId: number): Promise<void> {
  try {
    await sessionStorageArea()?.set({ [NATIVE_SAVE_TAB_SESSION_KEY]: tabId });
  } catch {
    // Session persistence is an optional recovery enhancement, not an execution prerequisite.
  }
}

async function clearRecordedNativeSaveTaskTab(tabId: number): Promise<void> {
  const storage = sessionStorageArea();
  if (!storage) return;
  try {
    const stored = await storage.get(NATIVE_SAVE_TAB_SESSION_KEY);
    if (stored[NATIVE_SAVE_TAB_SESSION_KEY] === tabId) {
      await storage.remove(NATIVE_SAVE_TAB_SESSION_KEY);
    }
  } catch {
    // A closed tab is safe even if best-effort session cleanup fails.
  }
}

/** Close only the runner-owned tab recorded before a previous MV3 worker stopped. */
export async function recoverRecordedNativeSaveTaskTab(): Promise<void> {
  const storage = sessionStorageArea();
  if (!storage) return;
  let tabId: unknown;
  try {
    const stored = await storage.get(NATIVE_SAVE_TAB_SESSION_KEY);
    tabId = stored[NATIVE_SAVE_TAB_SESSION_KEY];
  } catch {
    return;
  }
  if (typeof tabId !== "number" || !Number.isInteger(tabId) || tabId < 0) {
    try {
      await storage.remove(NATIVE_SAVE_TAB_SESSION_KEY);
    } catch {
      // Invalid runner metadata is safe to discard best-effort.
    }
    return;
  }
  await removeTabBestEffort(tabId);
  await clearRecordedNativeSaveTaskTab(tabId);
}

/** One MV3-lifetime barrier shared by startup, alarms, wakes, and direct execution. */
export function ensureNativeSaveTaskRecovery(): Promise<void> {
  nativeSaveRecoveryPromise ??= recoverRecordedNativeSaveTaskTab();
  return nativeSaveRecoveryPromise;
}

export function resetNativeSaveTaskRecoveryForTest(): void {
  nativeSaveRecoveryPromise = null;
}

async function createTabBeforeDeadline(
  url: string,
  deadline: number,
): Promise<chrome.tabs.Tab> {
  const creation = chrome.tabs.create({ active: true, url });
  try {
    return await beforeDeadline(creation, deadline);
  } catch (error) {
    if (error instanceof NativeSaveDeadlineError) {
      void creation.then(
        async (lateTab) => {
          if (lateTab.id !== undefined) await removeTabBestEffort(lateTab.id);
        },
        () => {},
      );
    }
    throw error;
  }
}

function contentResultForActiveTask(
  message: unknown,
  sender?: chrome.runtime.MessageSender,
): boolean {
  const active = activeTask;
  if (!active || active.settled || typeof message !== "object" || message === null) return false;
  const result = message as Record<string, unknown>;
  const senderUrl = typeof sender?.url === "string" ? sender.url : sender?.tab?.url;
  if (
    result.type !== "NATIVE_SAVE_RESULT" ||
    result.platform !== active.platform ||
    result.task_id !== active.task.id ||
    result.item_key !== active.task.item_key ||
    sender?.tab?.id !== active.tabId ||
    !isAllowedNativeSavePageUrl(active.platform, senderUrl)
  ) {
    return false;
  }
  active.settled = true;
  active.complete(result);
  return true;
}

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
      if (response !== undefined) return;
    } catch {
      // MV3 can expose the loaded tab before its content listener is registered.
    }
    await delay(Math.min(retryMs, remainingMs(deadline)), signal);
  }
}

async function executeBeforeDeadline(
  task: NativeSaveTask,
  tabId: number,
  deadline: number,
  retryMs: number,
  readinessController: AbortController,
): Promise<SanitizedNativeSaveOutcome> {
  const timeoutMs = remainingMs(deadline);
  if (timeoutMs <= 0) return timeoutOutcome();
  let timer: ReturnType<typeof setTimeout>;
  const terminal = new Promise<unknown>((resolve) => {
    const complete = (outcome: unknown): void => {
      if (activeTask) activeTask.settled = true;
      resolve(outcome);
    };
    activeTask = { task, tabId, platform: task.platform, complete, settled: false };
    timer = setTimeout(
      () => complete({ status: "failed", error_code: "native_save_timeout" }),
      timeoutMs,
    );
  });
  void sendExecuteWhenReady(tabId, task, deadline, retryMs, readinessController.signal);
  try {
    return sanitizeNativeSaveResult(await terminal);
  } finally {
    clearTimeout(timer!);
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
  await ensureNativeSaveTaskRecovery();
  const timeoutMs = Math.max(1, options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const deadline = Date.now() + timeoutMs;

  const owner = `native-save:${platformSlug}`;
  const readinessController = new AbortController();
  let mutexAcquired = false;
  let runtimeListenerRegistrationAttempted = false;
  let tabId: number | null = null;
  let outcome = failedOutcome();
  const listener = (message: unknown, sender: chrome.runtime.MessageSender): void => {
    handleNativeSaveContentResult(message, sender);
  };

  try {
    mutexAcquired = await acquireMutexBefore(
      owner,
      deadline,
      Math.max(1, options.mutexRetryMs ?? DEFAULT_MUTEX_RETRY_MS),
    );
    if (!mutexAcquired) {
      outcome = timeoutOutcome();
    } else {
      try {
        const tab = await createTabBeforeDeadline(task.content_url, deadline);
        if (tab.id === undefined) throw new Error("native-save task tab has no ID");
        tabId = tab.id;
        await recordNativeSaveTaskTab(tabId);
        const loadedTab = await waitForTabLoad(tabId, deadline);
        if (!isAllowedNativeSavePageUrl(task.platform, loadedTab.url)) {
          throw new Error("native-save task tab left its allow-listed platform");
        }
        runtimeListenerRegistrationAttempted = true;
        chrome.runtime.onMessage.addListener(listener);
        outcome = await executeBeforeDeadline(
          task,
          tabId,
          deadline,
          Math.max(1, options.readinessRetryMs ?? DEFAULT_READINESS_RETRY_MS),
          readinessController,
        );
      } catch (error) {
        outcome = error instanceof NativeSaveDeadlineError || Date.now() >= deadline
          ? timeoutOutcome()
          : failedOutcome();
      }
    }
    await postResult({ task_id: task.id, item_key: task.item_key, ...outcome });
  } finally {
    try {
      readinessController.abort();
    } catch {
      // Continue independent cleanup.
    }
    if (activeTask?.task.id === task.id) {
      activeTask.settled = true;
      activeTask = null;
    }
    if (runtimeListenerRegistrationAttempted) {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          chrome.runtime.onMessage.removeListener(listener);
          break;
        } catch {
          // Retry one transient Chrome failure, then continue independent cleanup.
        }
      }
    }
    if (tabId !== null) {
      await removeTabBestEffort(tabId);
      await clearRecordedNativeSaveTaskTab(tabId);
    }
    if (mutexAcquired) {
      try {
        releaseDispatcherMutex(owner);
      } catch {
        // Do not let mutex cleanup mask the authenticated callback result.
      }
    }
  }
}
