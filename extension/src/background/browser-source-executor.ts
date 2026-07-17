import type {
  ClaimedSourceTask,
  BrowserTaskResult,
} from "./generic-source-task-dispatcher.ts";
import type { SourceId, SourceOperation } from "../shared/api-client.ts";
import type { SourceManifest } from "../shared/api-client.ts";

const TAB_READY_TIMEOUT_MS = 12_000;
const CONTENT_READY_TIMEOUT_MS = 8_000;
const RESULT_TIMEOUT_MS = 180_000;
const SEND_RETRY_MS = 250;

type RuntimeMessage = Record<string, unknown>;
type TaskPayload = ClaimedSourceTask["payload"];

interface LegacyExecution {
  readonly url: string;
  readonly action: string;
  readonly resultAction: string;
  readonly data: Record<string, unknown>;
  readonly active: boolean;
  readonly items: (result: RuntimeMessage) => ReadonlyArray<Record<string, unknown>>;
}

export const LOCAL_BROWSER_SOURCE_OPERATIONS: Readonly<
  Partial<Record<SourceId, ReadonlyArray<SourceOperation>>>
> = Object.freeze({
  bilibili: ["search"],
  xiaohongshu: ["bootstrap_import", "search", "creator"],
  douyin: ["bootstrap_import", "search", "trending", "feed"],
  youtube: ["bootstrap_import"],
  zhihu: ["bootstrap_import", "search", "trending", "feed", "creator", "related"],
  reddit: ["bootstrap_import", "search", "trending", "community", "related"],
});

export function browserOperationsFromManifests(
  manifests: ReadonlyArray<SourceManifest>,
): Partial<Record<SourceId, ReadonlyArray<SourceOperation>>> {
  const selected: Partial<Record<SourceId, ReadonlyArray<SourceOperation>>> = {};
  for (const manifest of manifests) {
    const local = LOCAL_BROWSER_SOURCE_OPERATIONS[manifest.source_id];
    if (!local) continue;
    const supported = new Set(local);
    const operations = manifest.operations
      .filter((spec) => (
        spec.transport_kind === "browser" || spec.fallback_transport_kind === "browser"
      ))
      .map((spec) => spec.operation)
      .filter((operation) => supported.has(operation));
    if (operations.length > 0) selected[manifest.source_id] = Object.freeze(operations);
  }
  return selected;
}

export async function executeBrowserSourceTask(
  task: ClaimedSourceTask,
  signal?: AbortSignal,
): Promise<BrowserTaskResult> {
  throwIfAborted(signal);
  if (task.source_id === "twitter") {
    throw new Error("twitter does not declare browser-assisted execution");
  }

  const executions = buildExecutions(task);
  const items: Record<string, unknown>[] = [];
  const results = await executeInTemporaryTab(
    task.id,
    task.request_deadline_at,
    executions,
    signal,
  );
  for (let index = 0; index < executions.length; index += 1) {
    const execution = executions[index]!;
    const result = results[index]!;
    items.push(...execution.items(result).map(stripCredentialFields));
  }
  return { operation: task.payload.operation, items };
}

function buildExecutions(task: ClaimedSourceTask): LegacyExecution[] {
  switch (task.source_id) {
    case "bilibili":
      return [bilibiliExecution(task.id, task.payload)];
    case "xiaohongshu":
      return [xiaohongshuExecution(task.id, task.payload)];
    case "douyin":
      return douyinExecutions(task.id, task.payload);
    case "youtube":
      return youtubeExecutions(task.id, task.payload);
    case "zhihu":
      return [zhihuExecution(task.id, task.payload)];
    case "reddit":
      return [redditExecution(task.id, task.payload)];
    default:
      throw new Error(`no browser executor for ${task.source_id}`);
  }
}

function bilibiliExecution(taskId: string, payload: TaskPayload): LegacyExecution {
  assertOperation(payload, "search");
  return {
    url: `https://search.bilibili.com/all?keyword=${encodeURIComponent(payload.query)}`,
    action: "BILI_TASK_EXECUTE",
    resultAction: "BILI_TASK_RESULT",
    data: { task_id: taskId, type: "search", query: payload.query, limit: payload.limit },
    active: false,
    items: (result) => recordArray(result.videos),
  };
}

function xiaohongshuExecution(taskId: string, payload: TaskPayload): LegacyExecution {
  let url = "https://www.xiaohongshu.com/explore";
  let type = "bootstrap_profile";
  if (payload.operation === "search") {
    url = `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(payload.query)}`;
    type = "search";
  } else if (payload.operation === "creator") {
    url = xiaohongshuCreatorUrl(payload.creator);
    type = "creator";
  } else {
    assertOperation(payload, "bootstrap_import");
  }
  return {
    url,
    action: "XHS_TASK_EXECUTE",
    resultAction: "XHS_TASK_RESULT",
    data: {
      task_id: taskId,
      type,
      max_items_per_scope: payload.limit,
      scopes: type === "bootstrap_profile" ? ["saved", "liked", "xhs_history"] : undefined,
      max_scroll_rounds: type === "bootstrap_profile" ? 4 : undefined,
    },
    active: type === "bootstrap_profile",
    items: (result) => recordArray(result.notes),
  };
}

function douyinExecutions(taskId: string, payload: TaskPayload): LegacyExecution[] {
  if (payload.operation === "bootstrap_import") {
    return ["dy_post", "dy_collect", "dy_like", "dy_follow"].map((scope) => ({
      url: "https://www.douyin.com/",
      action: "DY_SCOPE_EXECUTE",
      resultAction: "DY_SCOPE_RESULT",
      data: {
        task_id: taskId,
        scope,
        max_items_per_scope: payload.limit,
        max_scroll_rounds: 4,
        max_stagnant_scroll_rounds: 3,
      },
      active: true,
      items: (result: RuntimeMessage) => recordArray(result.items),
    }));
  }
  if (payload.operation === "search") {
    return [{
      url: `https://www.douyin.com/search/${encodeURIComponent(payload.query)}`,
      action: "DY_SEARCH_EXECUTE",
      resultAction: "DY_SEARCH_RESULT",
      data: { task_id: taskId, keyword: payload.query, max_items: payload.limit },
      active: true,
      items: (result) => recordArray(result.items),
    }];
  }
  if (payload.operation === "trending") {
    return [{
      url: "https://www.douyin.com/hot",
      action: "DY_HOT_EXECUTE",
      resultAction: "DY_HOT_RESULT",
      data: {
        task_id: taskId,
        sentence_id: "openbiliclaw-trending",
        word: "热门",
        max_items: payload.limit,
      },
      active: true,
      items: (result) => recordArray(result.items),
    }];
  }
  if (payload.operation === "feed") {
    return [{
      url: "https://www.douyin.com/",
      action: "DY_FEED_EXECUTE",
      resultAction: "DY_FEED_RESULT",
      data: { task_id: taskId, max_items: payload.limit },
      active: true,
      items: (result) => recordArray(result.items),
    }];
  }
  throw new Error(`douyin does not execute ${payload.operation} in the browser`);
}

function youtubeExecutions(taskId: string, payload: TaskPayload): LegacyExecution[] {
  assertOperation(payload, "bootstrap_import");
  const scopes = [
    ["yt_history", "https://www.youtube.com/feed/history"],
    ["yt_subscriptions", "https://www.youtube.com/feed/channels"],
    ["yt_likes", "https://www.youtube.com/playlist?list=LL"],
  ] as const;
  return scopes.map(([scope, url]) => ({
    url,
    action: "YT_SCOPE_EXECUTE",
    resultAction: "YT_SCOPE_RESULT",
    data: { task_id: taskId, scope, max_items_per_scope: payload.limit, max_scroll_rounds: 10 },
    active: true,
    items: (result) => recordArray(result.items),
  }));
}

function zhihuExecution(taskId: string, payload: TaskPayload): LegacyExecution {
  const data: Record<string, unknown> = { task_id: taskId, max_items: payload.limit };
  if (payload.operation === "bootstrap_import") {
    Object.assign(data, { type: "bootstrap_events", scopes: ["zhihu_read_history", "zhihu_collection"] });
  } else if (payload.operation === "search") {
    Object.assign(data, { type: "search", keywords: [payload.query], max_items_per_keyword: payload.limit });
  } else if (payload.operation === "trending" || payload.operation === "feed") {
    data.type = payload.operation === "trending" ? "hot" : "feed";
  } else if (payload.operation === "creator") {
    Object.assign(data, { type: "creator", creator_urls: [payload.creator], max_items_per_creator: payload.limit });
  } else if (payload.operation === "related") {
    Object.assign(data, { type: "related", related_urls: [payload.seed], max_items_per_seed: payload.limit });
  } else {
    throw new Error(`zhihu does not execute ${payload.operation} in the browser`);
  }
  return {
    url: "https://www.zhihu.com/#openbiliclaw_zhihu_task=1",
    action: "ZHIHU_TASK_EXECUTE",
    resultAction: "ZHIHU_TASK_RESULT",
    data,
    active: payload.operation === "bootstrap_import",
    items: (result) => recordArray(result.items),
  };
}

function redditExecution(taskId: string, payload: TaskPayload): LegacyExecution {
  const data: Record<string, unknown> = { task_id: taskId, max_items: payload.limit };
  if (payload.operation === "bootstrap_import") {
    Object.assign(data, { type: "bootstrap_events", max_items_per_scope: payload.limit });
  } else if (payload.operation === "search") {
    Object.assign(data, { type: "search", keywords: [payload.query], max_items_per_keyword: payload.limit });
  } else if (payload.operation === "trending") {
    data.type = "hot";
  } else if (payload.operation === "community") {
    Object.assign(data, { type: "subreddit", subreddit: payload.community, max_items_per_subreddit: payload.limit });
  } else if (payload.operation === "related") {
    Object.assign(data, { type: "related", related_urls: [payload.seed], max_items_per_seed: payload.limit });
  } else {
    throw new Error(`reddit does not execute ${payload.operation} in the browser`);
  }
  return {
    url: "https://www.reddit.com/#openbiliclaw_reddit_task=1",
    action: "REDDIT_TASK_EXECUTE",
    resultAction: "REDDIT_TASK_RESULT",
    data,
    active: payload.operation === "bootstrap_import",
    items: (result) => recordArray(result.items),
  };
}

async function executeInTemporaryTab(
  taskId: string,
  requestDeadlineAt: string,
  executions: ReadonlyArray<LegacyExecution>,
  signal?: AbortSignal,
): Promise<RuntimeMessage[]> {
  const first = executions[0];
  if (!first) return [];
  throwIfAborted(signal);
  const tab = await chrome.tabs.create({ url: first.url, active: first.active });
  if (typeof tab.id !== "number") throw new Error("browser task tab has no id");
  const tabId = tab.id;
  const results: RuntimeMessage[] = [];
  try {
    throwIfAborted(signal);
    for (let index = 0; index < executions.length; index += 1) {
      const execution = executions[index]!;
      if (index > 0) {
        await chrome.tabs.update(tabId, { url: execution.url, active: execution.active });
      }
      await waitForTabReady(tabId, signal);
      const executionController = new AbortController();
      const abortExecution = (): void => executionController.abort();
      signal?.addEventListener("abort", abortExecution, { once: true });
      if (signal?.aborted) executionController.abort();
      const resultPromise = waitForResult(
        tabId,
        taskId,
        execution,
        requestDeadlineAt,
        executionController.signal,
      );
      try {
        await sendWhenContentReady(
          tabId,
          { action: execution.action, data: execution.data },
          executionController.signal,
        );
        results.push(await resultPromise);
      } catch (error) {
        executionController.abort();
        await resultPromise.catch(() => undefined);
        throw error;
      } finally {
        executionController.abort();
        signal?.removeEventListener("abort", abortExecution);
      }
    }
    return results;
  } finally {
    await chrome.tabs.remove(tabId).catch(() => undefined);
  }
}

function waitForTabReady(tabId: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      signal?.removeEventListener("abort", onAbort);
      if (error) reject(error);
      else resolve();
    };
    const onAbort = (): void => finish(abortError());
    const onUpdated = (updatedId: number, info: { status?: string }): void => {
      if (updatedId === tabId && info.status === "complete") finish();
    };
    const timeout = setTimeout(finish, TAB_READY_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(onUpdated);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) {
      finish(abortError());
      return;
    }
    void chrome.tabs.get(tabId).then((current) => {
      if (current.status === "complete") finish();
    }).catch(finish);
  });
}

async function sendWhenContentReady(
  tabId: number,
  message: RuntimeMessage,
  signal?: AbortSignal,
): Promise<void> {
  const deadline = Date.now() + CONTENT_READY_TIMEOUT_MS;
  while (true) {
    throwIfAborted(signal);
    try {
      await chrome.tabs.sendMessage(tabId, message);
      return;
    } catch (error) {
      if (Date.now() >= deadline) throw error;
      await abortableDelay(SEND_RETRY_MS, signal);
    }
  }
}

function waitForResult(
  tabId: number,
  taskId: string,
  execution: LegacyExecution,
  requestDeadlineAt: string,
  signal?: AbortSignal,
): Promise<RuntimeMessage> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let aggregate: RuntimeMessage = {};
    let continuing = false;
    const listener = (message: RuntimeMessage): boolean => {
      if (message.action !== execution.resultAction) return false;
      const data = asRecord(message.data);
      if (String(data.task_id ?? "") !== taskId) return false;
      aggregate = mergeRuntimeResults(aggregate, data);
      if (data.status === "failed" || data.status === "error") {
        finish(new Error(String(data.error ?? `${execution.resultAction} failed`)));
        return false;
      }
      if (data.status === "partial") return false;
      if (execution.resultAction === "XHS_TASK_RESULT" && typeof data.next_url === "string") {
        if (continuing) return false;
        continuing = true;
        void continueXiaohongshuTask(tabId, execution, data.next_url, signal).then(
          () => {
            continuing = false;
          },
          (error: unknown) => {
            finish(error instanceof Error ? error : new Error("Xiaohongshu continuation failed"));
          },
        );
        return false;
      }
      if (data.status !== "ok" && data.status !== "empty") return false;
      finish(undefined, aggregate);
      return false;
    };
    const deadlineBudget = Date.parse(requestDeadlineAt) - Date.now() - 1_000;
    const timeout = setTimeout(() => {
      finish(new Error(`${execution.resultAction} timed out`));
    }, Math.max(1, Math.min(RESULT_TIMEOUT_MS, deadlineBudget)));
    const onAbort = (): void => finish(abortError());
    const cleanup = (): void => {
      clearTimeout(timeout);
      chrome.runtime.onMessage.removeListener(listener);
      signal?.removeEventListener("abort", onAbort);
    };
    const finish = (error?: Error, value?: RuntimeMessage): void => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else resolve(value ?? aggregate);
    };
    chrome.runtime.onMessage.addListener(listener);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) finish(abortError());
  });
}

async function continueXiaohongshuTask(
  tabId: number,
  execution: LegacyExecution,
  nextUrl: string,
  signal?: AbortSignal,
): Promise<void> {
  const url = requireSourceUrl(nextUrl, "next_url", "xiaohongshu.com");
  await chrome.tabs.update(tabId, { url, active: execution.active });
  await waitForTabReady(tabId, signal);
  await sendWhenContentReady(tabId, { action: execution.action, data: execution.data }, signal);
}

function abortError(): Error {
  const error = new Error("browser source task aborted");
  error.name = "AbortError";
  return error;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw abortError();
}

function abortableDelay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (!signal) return new Promise((resolve) => setTimeout(resolve, milliseconds));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = (): void => {
      clearTimeout(timeout);
      signal.removeEventListener("abort", onAbort);
      reject(abortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
}

function mergeRuntimeResults(
  previous: RuntimeMessage,
  current: RuntimeMessage,
): RuntimeMessage {
  const merged: RuntimeMessage = { ...previous, ...current };
  for (const field of ["items", "notes", "videos", "urls"] as const) {
    const combined = [...arrayValue(previous[field]), ...arrayValue(current[field])];
    if (combined.length > 0) merged[field] = uniqueValues(combined);
  }
  return merged;
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function uniqueValues(values: unknown[]): unknown[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = typeof value === "string" ? `string:${value}` : `json:${safeStableJson(value)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function safeStableJson(value: unknown): string {
  try {
    return JSON.stringify(value, Object.keys(asRecord(value)).sort());
  } catch {
    return String(value);
  }
}

function xiaohongshuCreatorUrl(value: string): string {
  const creator = value.trim();
  if (!creator) throw new Error("creator identifier cannot be empty");
  if (/^https?:\/\//i.test(creator)) {
    return requireSourceUrl(creator, "creator", "xiaohongshu.com");
  }
  if (!/^[A-Za-z0-9_-]{1,200}$/.test(creator)) {
    throw new Error("creator identifier contains unsupported characters");
  }
  return `https://www.xiaohongshu.com/user/profile/${encodeURIComponent(creator)}`;
}

function assertOperation<T extends SourceOperation>(
  payload: TaskPayload,
  operation: T,
): asserts payload is Extract<TaskPayload, { operation: T }> {
  if (payload.operation !== operation) throw new Error(`expected ${operation}, got ${payload.operation}`);
}

function requireSourceUrl(value: string, field: string, hostname: string): string {
  const url = new URL(value);
  if (url.protocol !== "https:") throw new Error(`${field} must be an https URL`);
  if (url.hostname !== hostname && !url.hostname.endsWith(`.${hostname}`)) {
    throw new Error(`${field} must belong to ${hostname}`);
  }
  return url.href;
}

function asRecord(value: unknown): RuntimeMessage {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as RuntimeMessage
    : {};
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length > 0) : [];
}

function stripCredentialFields(value: Record<string, unknown>): Record<string, unknown> {
  const output: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
    if (/(cookie|credential|password|secret|session|token|authorization|apikey)$/.test(normalized)) continue;
    if (Array.isArray(child)) {
      output[key] = child.map((item) => isRecord(item) ? stripCredentialFields(item) : item);
    } else if (isRecord(child)) {
      output[key] = stripCredentialFields(child);
    } else {
      output[key] = child;
    }
  }
  return output;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
