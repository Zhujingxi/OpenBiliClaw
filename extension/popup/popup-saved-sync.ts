type UnknownRecord = Record<string, unknown>;

interface CanonicalSavedItemInput {
  source_platform?: unknown;
  platform?: unknown;
  content_url?: unknown;
  url?: unknown;
  bvid?: unknown;
  content_id?: unknown;
  content_type?: unknown;
  item_key?: unknown;
  [key: string]: unknown;
}

interface QueueOutcome {
  status?: unknown;
  value?: {
    saved?: unknown;
    item_key?: unknown;
    [key: string]: unknown;
  };
}

type SavedSyncStatus =
  | "pending"
  | "syncing"
  | "synced"
  | "already_synced"
  | "login_required"
  | "unsupported"
  | "rate_limited"
  | "extension_required"
  | "failed";
type SavedPresentationStatus = SavedSyncStatus | "not_started";

interface SavedSyncPresentation {
  label: string;
  tone: string;
  retryable: boolean;
  busy?: boolean;
  localOnly?: boolean;
  actionable?: boolean;
  actionLabel?: string;
  detail?: string;
}

interface SavedSyncItem {
  item_key: string;
  status: SavedSyncStatus;
  resolved_action: "watch_later" | "favorite";
  resolved_target: string;
  error_code: string;
  error_message: string;
}

interface SavedSyncTask {
  task_id: string;
  items: SavedSyncItem[];
}

interface SavedSyncCallbacks {
  onTerminal?: (task: SavedSyncTask) => void;
  onProgress?: (task: SavedSyncTask) => void;
  onBackground?: (task: SavedSyncTask) => void;
  onPollError?: (error: unknown, task?: SavedSyncTask) => void;
}

type SavedTimer = ReturnType<typeof setTimeout>;

interface SavedSyncTrackerEntry {
  taskId: string;
  task: SavedSyncTask;
  callbacks: SavedSyncCallbacks;
  startedAt: number;
  backgroundAnnounced: boolean;
  polling: boolean;
  timer: SavedTimer | null;
}

interface SavedSyncTracker {
  has(taskId: unknown): boolean;
  track(initial: unknown, callbacks?: SavedSyncCallbacks): string | null;
  resume(taskId: unknown): boolean;
  stop(taskId: unknown): boolean;
  resumeAll(): number;
  dispose(): void;
}

interface SavedSyncTrackerOptions {
  poll?: (taskId: string) => Promise<unknown>;
  now?: () => number;
  isVisible?: () => boolean;
  schedule?: (run: () => void, delay: number) => SavedTimer;
  cancel?: (timer: SavedTimer) => void;
  foregroundHorizonMs?: unknown;
  visibleDelayMs?: unknown;
  hiddenDelayMs?: unknown;
}

interface SavedTaskCoordinatorOptions extends SavedSyncCallbacks {
  tracker?: SavedSyncTracker;
  fetchTask?: (taskId: string) => Promise<unknown>;
}

interface FocusElementLike {
  dataset?: Record<string, string | undefined>;
  closest?: (selector: string) => FocusElementLike | null;
  querySelectorAll?: (selector: string) => ArrayLike<FocusElementLike> | Iterable<FocusElementLike>;
  querySelector?: (selector: string) => FocusElementLike | null;
  focus?: () => void;
}

interface SavedFocusToken {
  kind?: string;
  itemKey?: string;
  action: string;
  index?: number;
}

interface SavedButtonLike {
  setAttribute: (name: string, value: string) => void;
  removeAttribute: (name: string) => void;
  textContent?: string | null;
  title?: string;
  disabled?: boolean;
  isConnected?: boolean;
}

interface SavedToggleLabels {
  checkedTitle: string;
  uncheckedTitle: string;
  checkedAriaLabel?: string;
  uncheckedAriaLabel?: string;
  checkedText?: string;
  uncheckedText?: string;
}

interface SavedToggleEntry {
  button: SavedButtonLike;
  labels: SavedToggleLabels;
}

interface SavedToggleRegistryOptions {
  labels?: Partial<SavedToggleLabels>;
  onChange?: ((change: { bvid: string; saved: boolean }) => void) | null;
}

function normalizeBvid(bvid: unknown) {
  return String(bvid || "").trim();
}

function inferSavedPlatform(value: unknown, contentUrl: unknown) {
  const explicit = String(value || "").trim().toLowerCase();
  if (explicit) return explicit;
  try {
    const host = new URL(String(contentUrl || "").trim()).hostname.toLowerCase();
    if (host === "youtu.be" || host.endsWith(".youtube.com")) return "youtube";
    if (host === "x.com" || host.endsWith(".x.com") || host.endsWith(".twitter.com")) return "twitter";
    if (host.endsWith(".zhihu.com")) return "zhihu";
    if (host.endsWith(".bilibili.com") || host === "b23.tv") return "bilibili";
    return "web";
  } catch {
    return "bilibili";
  }
}

/** Preserve server-issued canonical identity without parsing namespaced keys into content IDs. */
export function normalizeCanonicalSavedItem(item: CanonicalSavedItemInput = {}) {
  const sourcePlatform = inferSavedPlatform(item.source_platform || item.platform, item.content_url);
  const legacyId = String(item.bvid || "").trim();
  const contentId = String(item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : "")).trim();
  const contentUrl = String(item.content_url || item.url || "").trim();
  const explicitType = String(item.content_type || "").trim();
  const contentType = explicitType || (sourcePlatform === "bilibili" && contentId ? "video" : "");
  return {
    item_key: String(item.item_key || (contentId ? `${sourcePlatform}:${contentId}` : "")).trim(),
    source_platform: sourcePlatform,
    content_id: contentId,
    content_url: contentUrl,
    content_type: contentType,
  };
}

export function partitionSavedQueueResults(queue: unknown, results: unknown) {
  const rows = (Array.isArray(queue) ? queue : []) as CanonicalSavedItemInput[];
  const outcomes = (Array.isArray(results) ? results : []) as QueueOutcome[];
  const saved: Array<{
    index: number;
    item: CanonicalSavedItemInput;
    itemKey: string;
    value: NonNullable<QueueOutcome["value"]>;
  }> = [];
  const savedIndexes = new Set();
  outcomes.forEach((result, index) => {
    if (result?.status !== "fulfilled" || result.value?.saved === false || !rows[index]) return;
    savedIndexes.add(index);
    saved.push({
      index,
      item: rows[index],
      itemKey: String(result.value?.item_key || rows[index]?.item_key || "").trim(),
      value: result.value as NonNullable<QueueOutcome["value"]>,
    });
  });
  return {
    saved,
    remaining: rows.filter((_, index) => !savedIndexes.has(index)),
    savedCount: saved.length,
    failedCount: rows.length - saved.length,
  };
}

const SAVED_SYNC_STATUSES: ReadonlySet<unknown> = new Set([
  "pending",
  "syncing",
  "synced",
  "already_synced",
  "login_required",
  "unsupported",
  "rate_limited",
  "extension_required",
  "failed",
]);

const SYNC_PRESENTATIONS: Record<SavedPresentationStatus, SavedSyncPresentation> = {
  not_started: { label: "待同步", tone: "neutral", retryable: false },
  pending: { label: "待同步", tone: "info", retryable: false },
  syncing: { label: "同步中", tone: "info", retryable: false },
  synced: { label: "已同步", tone: "success", retryable: false },
  already_synced: { label: "已同步", tone: "success", retryable: false },
  login_required: { label: "需要登录", tone: "warning", retryable: true },
  unsupported: { label: "仅本地保存", tone: "neutral", retryable: false },
  rate_limited: { label: "同步失败", tone: "error", retryable: true },
  extension_required: { label: "需要连接插件", tone: "warning", retryable: true },
  failed: { label: "同步失败", tone: "error", retryable: true },
};

const RETRY_DETAILS: Partial<Record<SavedPresentationStatus, string>> = {
  login_required: "请登录对应平台后重试。",
  rate_limited: "平台请求过于频繁，请稍后重试。",
  extension_required: "请连接已安装 OpenBiliClaw 插件的登录态浏览器后重试。",
  failed: "平台同步失败，请重试；若持续失败请检查连接或登录状态。",
};

const PLATFORM_LABELS: Record<string, string> = {
  bilibili: "B站",
  youtube: "YouTube",
  twitter: "X",
  xiaohongshu: "小红书",
  douyin: "抖音",
  zhihu: "知乎",
  reddit: "Reddit",
};

function safeSyncText(value: unknown, maxLength = 240) {
  return String(value || "").replace(/[\p{C}\p{Zl}\p{Zp}]/gu, "").trim().slice(0, maxLength);
}

export function createSavedSubmissionFence() {
  const keys = new Set<string>();
  const normalize = (value: unknown) => safeSyncText(value, 2048);
  return {
    has(itemKey: unknown) { return keys.has(normalize(itemKey)); },
    claim(itemKeys: unknown) {
      const candidates = [...new Set((Array.isArray(itemKeys) ? itemKeys : []).map(normalize))]
        .filter(Boolean);
      if (!candidates.length || candidates.some((key) => keys.has(key))) return false;
      for (const key of candidates) keys.add(key);
      return true;
    },
    release(itemKeys: unknown) {
      for (const itemKey of Array.isArray(itemKeys) ? itemKeys : []) keys.delete(normalize(itemKey));
    },
  };
}

export function getSavedSyncPresentation(
  status: unknown,
  errorCode: unknown = "",
  resolvedTarget: unknown = "",
  errorMessage: unknown = "",
  syncTaskId: unknown = "",
) {
  const normalizedStatus = (
    SAVED_SYNC_STATUSES.has(status) ? status : (status ? "failed" : "not_started")
  ) as SavedPresentationStatus;
  const code = safeSyncText(errorCode, 96);
  const target = safeSyncText(resolvedTarget);
  const message = safeSyncText(errorMessage);
  const presentation = { ...(SYNC_PRESENTATIONS[normalizedStatus] || SYNC_PRESENTATIONS.failed) };
  presentation.busy = normalizedStatus === "syncing"
    || (normalizedStatus === "pending" && Boolean(safeSyncText(syncTaskId, 64)));
  presentation.localOnly = normalizedStatus === "unsupported" && code === "unsupported_content_type";
  if (normalizedStatus === "unsupported" && code === "unsupported_adapter_missing") {
    presentation.label = "待升级重试";
    presentation.tone = "warning";
    presentation.retryable = true;
  } else if (normalizedStatus === "unsupported" && !presentation.localOnly) {
    presentation.label = "同步暂不可用";
    presentation.tone = "warning";
    presentation.retryable = true;
  }
  presentation.actionable = !presentation.busy
    && !["synced", "already_synced"].includes(normalizedStatus)
    && !presentation.localOnly;
  presentation.actionLabel = presentation.busy
    ? "同步中…"
    : (presentation.retryable ? "重试同步" : "同步");
  if (presentation.localOnly) {
    presentation.detail = "此内容类型暂不支持平台同步，仅保存在本地。";
  } else if (normalizedStatus === "unsupported" && code === "unsupported_adapter_missing") {
    presentation.detail = "同步能力可能正在滚动升级，请更新后端与插件后重试。";
  } else if (normalizedStatus === "unsupported") {
    presentation.detail = message || "当前同步能力暂不可用，请更新后重试。";
  } else if (["synced", "already_synced"].includes(normalizedStatus)) {
    presentation.detail = target || "平台已确认同步完成。";
  } else if (presentation.busy) {
    presentation.detail = target || "平台同步任务已提交，请稍候。";
  } else if (normalizedStatus === "pending") {
    presentation.detail = target || "已保存在本地，可手动同步到平台。";
  } else {
    presentation.detail = message || target || RETRY_DETAILS[normalizedStatus]
      || "平台目标将在同步时确认";
  }
  return presentation;
}

export function isSavedSyncEligibleStatus(
  status: unknown,
  errorCode: unknown = "",
  syncTaskId: unknown = "",
) {
  return getSavedSyncPresentation(status, errorCode, "", "", syncTaskId).actionable;
}

export function updateSavedBatchButtonState(button: SavedButtonLike, pendingCount: number) {
  const disabled = pendingCount <= 0;
  button.disabled = disabled;
  button.setAttribute("aria-disabled", String(disabled));
  button.removeAttribute("aria-busy");
}

export function sanitizeSavedSyncTask(payload: unknown): SavedSyncTask {
  const rows = Array.isArray((payload as UnknownRecord | null)?.items)
    ? (payload as UnknownRecord).items as UnknownRecord[]
    : [];
  return {
    task_id: safeSyncText((payload as UnknownRecord | null)?.task_id, 64),
    items: rows.slice(0, 500).map((item) => ({
      item_key: safeSyncText(item?.item_key, 2048),
      status: (SAVED_SYNC_STATUSES.has(item?.status) ? item.status : "failed") as SavedSyncStatus,
      resolved_action: item?.resolved_action === "watch_later" ? "watch_later" : "favorite",
      resolved_target: safeSyncText(item?.resolved_target),
      error_code: safeSyncText(item?.error_code, 96),
      error_message: safeSyncText(item?.error_message),
    })),
  };
}

export function summarizeSavedSyncResults(items: unknown) {
  const groups = new Map<string, { success: number; total: number }>();
  for (const item of (Array.isArray(items) ? items : []) as UnknownRecord[]) {
    const platform = safeSyncText(item?.item_key, 2048).split(":", 1)[0] || "unknown";
    const group = groups.get(platform) || { success: 0, total: 0 };
    group.total += 1;
    if (item?.status === "synced" || item?.status === "already_synced") {
      group.success += 1;
    }
    groups.set(platform, group);
  }
  return Array.from(groups, ([platform, result]) => (
    `${PLATFORM_LABELS[platform] || platform} ${result.success}/${result.total}`
  )).join(" · ");
}

export function createRetainedSavedListState() {
  let value: { items: unknown[]; total: number; loaded: boolean; error: string } = {
    items: [], total: 0, loaded: false, error: "",
  };
  return {
    commit(payload: { items?: unknown; total?: unknown } = {}) {
      const items = Array.isArray(payload.items) ? payload.items : [];
      value = { items, total: Number(payload.total) || 0, loaded: true, error: "" };
    },
    fail(reason: unknown) {
      value = {
        ...value,
        error: String(
          (reason as { message?: unknown } | null)?.message || reason || "保存列表加载失败。",
        ).trim(),
      };
    },
    snapshot() { return { ...value, items: [...value.items] }; },
  };
}

export function captureSavedFocus(
  root: FocusElementLike | null | undefined,
  activeElement: FocusElementLike | null | undefined = globalThis.document?.activeElement as HTMLElement | null,
) {
  const listAction = String(activeElement?.dataset?.savedListAction || "").trim();
  if (root && listAction) return { kind: "list", action: listAction };
  const card = activeElement?.closest?.("[data-item-key]");
  const itemKey = String(card?.dataset?.itemKey || "").trim();
  const action = String(activeElement?.dataset?.savedAction || "").trim();
  const cards = Array.from(root?.querySelectorAll?.("[data-item-key]") || []) as FocusElementLike[];
  const index = cards.indexOf(card as FocusElementLike);
  return root && itemKey && action ? { itemKey, action, index: Math.max(0, index) } : null;
}

export function restoreSavedFocus(
  root: FocusElementLike | null | undefined,
  token: SavedFocusToken | null | undefined,
) {
  if (!root || !token?.action) return false;
  const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []) as FocusElementLike[];
  const focusAction = (card: FocusElementLike | undefined) => {
    const actions = Array.from(card?.querySelectorAll?.("[data-saved-action]") || []) as FocusElementLike[];
    const action = actions.find((candidate) => candidate.dataset?.savedAction === token.action)
      || actions[0];
    action?.focus?.();
    return Boolean(action);
  };
  if (token.kind === "list" || token.itemKey === "__list__") {
    const sameListAction = root.querySelector?.(
      `[data-saved-list-action="${token.action}"]`,
    );
    if (sameListAction) { sameListAction.focus?.(); return true; }
    if (focusAction(cards[0])) return true;
    const heading = root.querySelector?.("[data-saved-heading]");
    if (heading) { heading.focus?.(); return true; }
    return false;
  }
  if (!token.itemKey) return false;
  let sameIndex = -1;
  for (let cardIndex = 0; cardIndex < cards.length; cardIndex += 1) {
    const card = cards[cardIndex];
    if (card.dataset?.itemKey !== token.itemKey) continue;
    sameIndex = cardIndex;
    const exact = Array.from(card.querySelectorAll?.("[data-saved-action]") || [])
      .find((action) => action.dataset?.savedAction === token.action);
    if (exact) { exact.focus?.(); return true; }
  }
  const index = sameIndex >= 0
    ? sameIndex + 1
    : Math.max(0, Math.min(Number(token.index) || 0, cards.length));
  const previousIndex = sameIndex >= 0 ? sameIndex - 1 : index - 1;
  if (focusAction(cards[index]) || focusAction(cards[previousIndex])) return true;
  const listAction = root.querySelector?.(
    '[data-saved-list-action="sync-all"], [data-saved-list-action="retry"]',
  );
  if (listAction) { listAction.focus?.(); return true; }
  const heading = root.querySelector?.("[data-saved-heading]");
  if (heading) { heading.focus?.(); return true; }
  return false;
}

export function createSavedSyncTaskTracker(options: SavedSyncTrackerOptions = {}) {
  const poll = options.poll as NonNullable<SavedSyncTrackerOptions["poll"]>;
  const now = options.now || Date.now;
  const isVisible = options.isVisible || (() => typeof document === "undefined" || !document.hidden);
  const schedule = options.schedule || ((run: () => void, delay: number) => setTimeout(run, delay));
  const cancel = options.cancel || clearTimeout;
  const foregroundHorizonMs = Number(options.foregroundHorizonMs ?? 20_000);
  const visibleDelayMs = Number(options.visibleDelayMs ?? 750);
  const hiddenDelayMs = Number(options.hiddenDelayMs ?? 5_000);
  const active = new Map<string, SavedSyncTrackerEntry>();
  const terminal = (task: SavedSyncTask) => {
    const rows = Array.isArray(task?.items) ? task.items : [];
    return rows.every((item) => SAVED_SYNC_STATUSES.has(item?.status)
      && !["pending", "syncing"].includes(item.status));
  };
  const queue = (entry: SavedSyncTrackerEntry, delay: number | null = null) => {
    if (!active.has(entry.taskId)) return;
    entry.timer = schedule(
      () => tick(entry),
      delay ?? (isVisible() ? visibleDelayMs : hiddenDelayMs),
    );
  };
  const tick = async (entry: SavedSyncTrackerEntry) => {
    if (!active.has(entry.taskId) || entry.polling) return;
    entry.polling = true;
    try {
      const next = await poll(entry.taskId);
      if (!active.has(entry.taskId)) return;
      if (next && typeof next === "object") entry.task = sanitizeSavedSyncTask(next);
      if (terminal(entry.task)) {
        active.delete(entry.taskId);
        entry.callbacks.onTerminal?.(entry.task);
        return;
      }
      entry.callbacks.onProgress?.(entry.task);
      if (!entry.backgroundAnnounced && now() - entry.startedAt >= foregroundHorizonMs) {
        entry.backgroundAnnounced = true;
        entry.callbacks.onBackground?.(entry.task);
      }
    } catch (error) {
      if (!active.has(entry.taskId)) return;
      entry.callbacks.onPollError?.(error, entry.task);
    } finally {
      entry.polling = false;
    }
    queue(entry);
  };
  const api: SavedSyncTracker = {
    has(taskId: unknown) { return active.has(safeSyncText(taskId, 64)); },
    track(initial: unknown, callbacks: SavedSyncCallbacks = {}) {
      const task = sanitizeSavedSyncTask(initial);
      const taskId = task.task_id;
      if (!taskId) return null;
      if (terminal(task)) { callbacks.onTerminal?.(task); return taskId; }
      const existing = active.get(taskId);
      if (existing) {
        existing.task = task;
        existing.callbacks = { ...existing.callbacks, ...callbacks };
        return taskId;
      }
      const entry: SavedSyncTrackerEntry = {
        taskId, task, callbacks, startedAt: now(), backgroundAnnounced: false,
        polling: false, timer: null,
      };
      active.set(taskId, entry);
      callbacks.onProgress?.(task);
      queue(entry);
      return taskId;
    },
    resume(taskId: unknown) {
      const entry = active.get(safeSyncText(taskId, 64));
      if (!entry || entry.polling) return false;
      if (entry.timer != null) cancel(entry.timer);
      queue(entry, 0);
      return true;
    },
    stop(taskId: unknown) {
      const entry = active.get(safeSyncText(taskId, 64));
      if (!entry) return false;
      if (entry.timer != null) cancel(entry.timer);
      active.delete(entry.taskId);
      return true;
    },
    resumeAll() {
      let resumed = 0;
      for (const taskId of active.keys()) if (api.resume(taskId)) resumed += 1;
      return resumed;
    },
    dispose() {
      for (const entry of active.values()) if (entry.timer != null) cancel(entry.timer);
      active.clear();
    },
  };
  return api;
}

export function createSavedTaskCoordinator(options: SavedTaskCoordinatorOptions = {}) {
  const tracker = options.tracker as SavedSyncTracker;
  const fetchTask = options.fetchTask as NonNullable<SavedTaskCoordinatorOptions["fetchTask"]>;
  const ownersByTask = new Map<string, Set<string>>();
  const taskByItem = new Map<string, string>();
  const recovering = new Map<string, Promise<unknown>>();
  let disposed = false;
  const release = (taskId: string) => {
    for (const itemKey of ownersByTask.get(taskId) || []) {
      if (taskByItem.get(itemKey) === taskId) taskByItem.delete(itemKey);
    }
    ownersByTask.delete(taskId);
  };
  const claim = (taskId: string, itemKeys: string[]) => {
    const keys = new Set(ownersByTask.get(taskId) || []);
    for (const key of (itemKeys || []).map((value) => safeSyncText(value, 2048)).filter(Boolean)) {
      keys.add(key);
    }
    ownersByTask.set(taskId, keys);
    for (const key of keys) taskByItem.set(key, taskId);
  };
  const track = (task: unknown, itemKeys: string[], callbacks: SavedSyncCallbacks = {}) => {
    const taskId = safeSyncText((task as { task_id?: unknown } | null)?.task_id, 64);
    if (!taskId || disposed) return null;
    claim(taskId, itemKeys);
    return tracker.track(task, {
      ...callbacks,
      onTerminal(terminalTask: SavedSyncTask) {
        release(taskId);
        callbacks.onTerminal?.(terminalTask);
        options.onTerminal?.(terminalTask);
      },
      onProgress(progressTask: SavedSyncTask) {
        callbacks.onProgress?.(progressTask);
        options.onProgress?.(progressTask);
      },
      onBackground(progressTask: SavedSyncTask) {
        callbacks.onBackground?.(progressTask);
        options.onBackground?.(progressTask);
      },
      onPollError(error: unknown, progressTask?: SavedSyncTask) {
        callbacks.onPollError?.(error, progressTask);
        options.onPollError?.(error, progressTask);
      },
    });
  };
  return {
    owns(itemKey: unknown) { return taskByItem.has(safeSyncText(itemKey, 2048)); },
    taskFor(itemKey: unknown) { return taskByItem.get(safeSyncText(itemKey, 2048)) || ""; },
    track,
    async recover(rows: unknown, callbacks: SavedSyncCallbacks = {}) {
      if (disposed) return;
      const grouped = new Map<string, string[]>();
      for (const row of (Array.isArray(rows) ? rows : []) as UnknownRecord[]) {
        if (!["pending", "syncing"].includes(row?.sync_status as string)) continue;
        const taskId = safeSyncText(row?.sync_task_id, 64);
        const itemKey = safeSyncText(row?.item_key, 2048);
        if (!taskId || !itemKey) continue;
        if (!grouped.has(taskId)) grouped.set(taskId, []);
        grouped.get(taskId)!.push(itemKey);
      }
      await Promise.all(Array.from(grouped, async ([taskId, itemKeys]) => {
        claim(taskId, itemKeys);
        if (tracker.has(taskId)) return;
        if (recovering.has(taskId)) return recovering.get(taskId);
        const recovery = Promise.resolve()
          .then(() => fetchTask(taskId))
          .then((task) => track(task, itemKeys, callbacks))
          .catch((error) => {
            if (disposed) return;
            track({
              task_id: taskId,
              items: itemKeys.map((item_key) => ({ item_key, status: "syncing" })),
            }, itemKeys, callbacks);
            callbacks.onPollError?.(error);
          })
          .finally(() => recovering.delete(taskId));
        recovering.set(taskId, recovery);
        return recovery;
      }));
    },
    resumeAll() { return tracker.resumeAll?.() || 0; },
    dispose() {
      disposed = true;
      recovering.clear();
      ownersByTask.clear();
      taskByItem.clear();
      tracker.dispose?.();
    },
  };
}

function mergeLabels(
  baseLabels: Partial<SavedToggleLabels>,
  overrideLabels?: Partial<SavedToggleLabels>,
): SavedToggleLabels {
  return {
    checkedTitle: "取消保存",
    uncheckedTitle: "保存",
    ...baseLabels,
    ...overrideLabels,
  };
}

function applyButtonState(
  button: SavedButtonLike | null | undefined,
  saved: boolean,
  labels: SavedToggleLabels,
) {
  if (!button) return;
  if (typeof button.setAttribute === "function") {
    button.setAttribute("aria-pressed", saved ? "true" : "false");
    const ariaLabel = saved ? labels.checkedAriaLabel : labels.uncheckedAriaLabel;
    if (ariaLabel) {
      button.setAttribute("aria-label", ariaLabel);
    }
  }
  if (
    labels.checkedText !== undefined &&
    labels.uncheckedText !== undefined &&
    "textContent" in button
  ) {
    button.textContent = saved ? labels.checkedText : labels.uncheckedText;
  }
  if ("title" in button) {
    button.title = saved ? labels.checkedTitle : labels.uncheckedTitle;
  }
}

export function createSavedToggleRegistry(
  { labels = {}, onChange = null }: SavedToggleRegistryOptions = {},
) {
  const defaultLabels = mergeLabels(labels);
  const savedBvids = new Set<string>();
  const buttonsByBvid = new Map<string, Set<SavedToggleEntry>>();
  const mutationVersions = new Map<string, number>();
  const busyBvids = new Set<string>();

  function nextVersion(bvid: string) {
    const version = (mutationVersions.get(bvid) || 0) + 1;
    mutationVersions.set(bvid, version);
    return version;
  }

  function isDetached(button: SavedButtonLike | null | undefined) {
    // Buttons removed from the DOM (e.g. via replaceChildren on re-render)
    // report isConnected === false. Test doubles that omit the property
    // (isConnected === undefined) are treated as live and kept.
    return button != null && button.isConnected === false;
  }

  function syncButtons(bvid: string) {
    const entries = buttonsByBvid.get(bvid);
    if (!entries) return;
    const saved = savedBvids.has(bvid);
    for (const entry of entries) {
      if (isDetached(entry.button)) {
        entries.delete(entry);
        continue;
      }
      if ("disabled" in entry.button) entry.button.disabled = busyBvids.has(bvid);
      applyButtonState(entry.button, saved, entry.labels);
    }
    if (entries.size === 0) {
      buttonsByBvid.delete(bvid);
    }
  }

  function pruneDetached() {
    for (const [bvid, entries] of buttonsByBvid) {
      for (const entry of entries) {
        if (isDetached(entry.button)) {
          entries.delete(entry);
        }
      }
      if (entries.size === 0) {
        buttonsByBvid.delete(bvid);
      }
    }
  }

  function applySaved(key: string, saved: boolean) {
    if (saved) {
      savedBvids.add(key);
    } else {
      savedBvids.delete(key);
    }
    syncButtons(key);
  }

  function setSaved(bvid: unknown, saved: boolean) {
    const key = normalizeBvid(bvid);
    if (!key) return;
    nextVersion(key);
    applySaved(key, saved);
  }

  function registerButton(
    bvid: unknown,
    button: SavedButtonLike | null | undefined,
    buttonLabels: Partial<SavedToggleLabels> = {},
  ) {
    const key = normalizeBvid(bvid);
    if (!key || !button) return () => {};
    const entry: SavedToggleEntry = {
      button,
      labels: mergeLabels(defaultLabels, buttonLabels),
    };
    if (!buttonsByBvid.has(key)) {
      buttonsByBvid.set(key, new Set());
    }
    buttonsByBvid.get(key)!.add(entry);
    applyButtonState(button, savedBvids.has(key), entry.labels);
    return () => {
      const entries = buttonsByBvid.get(key);
      if (!entries) return;
      entries.delete(entry);
      if (entries.size === 0) {
        buttonsByBvid.delete(key);
      }
    };
  }

  async function hydrateStatus(
    bvid: unknown,
    loadStatus: ((key: string) => Promise<unknown>) | unknown,
  ) {
    const key = normalizeBvid(bvid);
    if (!key || typeof loadStatus !== "function") return null;
    const version = mutationVersions.get(key) || 0;
    try {
      const result = await loadStatus(key);
      // Drop a stale hydration if a mutation ran (version bumped) OR is still
      // in flight (busy) since this GET started: its server snapshot may predate
      // the write and would otherwise roll a just-confirmed toggle back to stale.
      if (busyBvids.has(key) || (mutationVersions.get(key) || 0) !== version) {
        return result;
      }
      if (result && typeof (result as { saved?: unknown }).saved === "boolean") {
        applySaved(key, (result as { saved: boolean }).saved);
      }
      return result;
    } catch {
      return null;
    }
  }

  async function toggle(
    bvid: unknown,
    { add, remove }: {
      add: (key: string) => Promise<unknown>;
      remove: (key: string) => Promise<unknown>;
    },
  ) {
    const key = normalizeBvid(bvid);
    if (!key || busyBvids.has(key)) return false;
    const wasSaved = savedBvids.has(key);
    const optimisticSaved = !wasSaved;
    busyBvids.add(key);
    nextVersion(key);
    applySaved(key, optimisticSaved);
    try {
      const result = await (wasSaved ? remove(key) : add(key));
      const finalSaved = result && typeof (result as { saved?: unknown }).saved === "boolean"
        ? (result as { saved: boolean }).saved
        : optimisticSaved;
      // Bump again before applying the confirmed state: invalidates any
      // hydration whose status GET started during this write and resolves
      // after busy clears (busy check alone misses that window).
      nextVersion(key);
      applySaved(key, finalSaved);
      if (typeof onChange === "function") {
        onChange({ bvid: key, saved: finalSaved });
      }
      return true;
    } catch (error) {
      nextVersion(key);
      applySaved(key, wasSaved);
      throw error;
    } finally {
      busyBvids.delete(key);
      syncButtons(key);
    }
  }

  return {
    hydrateStatus,
    isSaved(bvid: unknown) {
      return savedBvids.has(normalizeBvid(bvid));
    },
    pruneDetached,
    registerButton,
    setSaved,
    toggle,
  };
}
