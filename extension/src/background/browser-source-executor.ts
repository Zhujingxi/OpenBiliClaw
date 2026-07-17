import type {
  ClaimedSourceTask,
  BrowserTaskResult,
} from "./generic-source-task-dispatcher.ts";
import type { SourceId, SourceOperation } from "../shared/api-client.ts";

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

export const BROWSER_SOURCE_OPERATIONS: Readonly<
  Partial<Record<SourceId, ReadonlyArray<SourceOperation>>>
> = Object.freeze({
  bilibili: ["search"],
  xiaohongshu: ["bootstrap_import", "search", "creator"],
  douyin: ["bootstrap_import", "search", "trending", "feed"],
  youtube: ["bootstrap_import"],
  zhihu: ["bootstrap_import", "search", "trending", "feed", "creator", "related"],
  reddit: ["bootstrap_import", "search", "trending", "community", "related"],
});

export async function executeBrowserSourceTask(
  task: ClaimedSourceTask,
): Promise<BrowserTaskResult> {
  if (task.source_id === "twitter") {
    throw new Error("twitter does not declare browser-assisted execution");
  }

  const executions = buildExecutions(task);
  const items: Record<string, unknown>[] = [];
  const results = await executeInTemporaryTab(task.id, task.request_deadline_at, executions);
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
    url = requireSourceUrl(payload.creator, "creator", "xiaohongshu.com");
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
      url: "https://www.douyin.com/",
      action: "DY_SEARCH_EXECUTE",
      resultAction: "DY_SEARCH_RESULT",
      data: { task_id: taskId, keyword: payload.query, max_items: payload.limit },
      active: false,
      items: (result) => recordArray(result.items),
    }];
  }
  if (payload.operation !== "trending" && payload.operation !== "feed") {
    throw new Error(`douyin does not execute ${payload.operation} in the browser`);
  }
  return [{
    url: "https://www.douyin.com/",
    action: "DY_FEED_EXECUTE",
    resultAction: "DY_FEED_RESULT",
    data: { task_id: taskId, max_items: payload.limit },
    active: false,
    items: (result) => recordArray(result.items),
  }];
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
): Promise<RuntimeMessage[]> {
  const first = executions[0];
  if (!first) return [];
  const tab = await chrome.tabs.create({ url: first.url, active: first.active });
  if (typeof tab.id !== "number") throw new Error("browser task tab has no id");
  const tabId = tab.id;
  const results: RuntimeMessage[] = [];
  try {
    for (let index = 0; index < executions.length; index += 1) {
      const execution = executions[index]!;
      if (index > 0) {
        await chrome.tabs.update(tabId, { url: execution.url, active: execution.active });
      }
      await waitForTabReady(tabId);
      const resultPromise = waitForResult(taskId, execution.resultAction, requestDeadlineAt);
      await sendWhenContentReady(tabId, { action: execution.action, data: execution.data });
      results.push(await resultPromise);
    }
    return results;
  } finally {
    await chrome.tabs.remove(tabId).catch(() => undefined);
  }
}

function waitForTabReady(tabId: number): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve();
    };
    const onUpdated = (updatedId: number, info: { status?: string }): void => {
      if (updatedId === tabId && info.status === "complete") finish();
    };
    const timeout = setTimeout(finish, TAB_READY_TIMEOUT_MS);
    chrome.tabs.onUpdated.addListener(onUpdated);
    void chrome.tabs.get(tabId).then((current) => {
      if (current.status === "complete") finish();
    }).catch(finish);
  });
}

async function sendWhenContentReady(tabId: number, message: RuntimeMessage): Promise<void> {
  const deadline = Date.now() + CONTENT_READY_TIMEOUT_MS;
  while (true) {
    try {
      await chrome.tabs.sendMessage(tabId, message);
      return;
    } catch (error) {
      if (Date.now() >= deadline) throw error;
      await new Promise((resolve) => setTimeout(resolve, SEND_RETRY_MS));
    }
  }
}

function waitForResult(taskId: string, action: string, requestDeadlineAt: string): Promise<RuntimeMessage> {
  return new Promise((resolve, reject) => {
    const listener = (message: RuntimeMessage): boolean => {
      if (message.action !== action) return false;
      const data = asRecord(message.data);
      if (String(data.task_id ?? "") !== taskId) return false;
      cleanup();
      if (data.status === "failed" || data.status === "error") {
        reject(new Error(String(data.error ?? `${action} failed`)));
      } else {
        resolve(data);
      }
      return false;
    };
    const deadlineBudget = Date.parse(requestDeadlineAt) - Date.now() - 1_000;
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error(`${action} timed out`));
    }, Math.max(1, Math.min(RESULT_TIMEOUT_MS, deadlineBudget)));
    const cleanup = (): void => {
      clearTimeout(timeout);
      chrome.runtime.onMessage.removeListener(listener);
    };
    chrome.runtime.onMessage.addListener(listener);
  });
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
