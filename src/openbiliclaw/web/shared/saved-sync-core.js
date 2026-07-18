/**
 * Canonical, surface-neutral state and coordination for web saved sync.
 *
 * Migration map for the existing consumers:
 * - desktop `OpenBiliClawSavedSync.*` maps one-to-one to the exports with the
 *   same names; desktop `taskIsTerminal` remains `taskIsTerminal`.
 * - mobile `saved-sync-runtime.js` maps one-to-one to the exports with the
 *   same names; mobile `isSavedTaskTerminal` remains an alias export.
 * - mobile `getSavedSyncViewModel` maps its state decisions to
 *   `getSavedSyncPresentation`; copy stays in the mobile surface.
 * - mobile API saved helpers map to `createStrictSavedApi` when that surface
 *   is rewired around its request adapter.
 *
 * DOM, visibility, clock, and timer dependencies are accepted as arguments.
 * The module never reads document or window. Named exports serve module
 * consumers, while `OBCSavedSyncCore` serves global-namespace consumers.
 */

const TERMINAL_STATUSES = new Set([
  "synced",
  "already_synced",
  "login_required",
  "unsupported",
  "rate_limited",
  "extension_required",
  "failed",
]);
const KNOWN_STATUSES = new Set([
  "not_started",
  "pending",
  "syncing",
  ...TERMINAL_STATUSES,
]);
const PLATFORM_ALIASES = Object.freeze({
  bili: "bilibili",
  xhs: "xiaohongshu",
  dy: "douyin",
  yt: "youtube",
  x: "twitter",
  zh: "zhihu",
  rd: "reddit",
});
const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/**
 * @typedef {Object} SavedItemInput
 * @property {unknown} [source_platform]
 * @property {unknown} [platform]
 * @property {unknown} [item_key]
 * @property {unknown} [content_id]
 * @property {unknown} [bvid]
 * @property {unknown} [content_url]
 * @property {unknown} [url]
 * @property {unknown} [content_type]
 * @property {unknown} [title]
 * @property {unknown} [author_name]
 * @property {unknown} [up_name]
 * @property {unknown} [author]
 * @property {unknown} [cover_url]
 * @property {unknown} [note]
 */

function text(value) {
  return String(value || "").trim();
}

function itemKeys(values) {
  return [...new Set((Array.isArray(values) ? values : []).map(text))].filter(Boolean);
}

export function createSavedSubmissionFence() {
  const claimed = new Set();
  return {
    has(itemKey) {
      return claimed.has(text(itemKey));
    },
    claim(values) {
      const candidates = itemKeys(values);
      if (!candidates.length || candidates.some((key) => claimed.has(key))) return false;
      for (const key of candidates) claimed.add(key);
      return true;
    },
    release(values) {
      for (const key of Array.isArray(values) ? values : []) claimed.delete(text(key));
    },
  };
}

function inferPlatform(item) {
  const explicit = text(item?.source_platform || item?.platform).toLowerCase();
  if (explicit) return PLATFORM_ALIASES[explicit] || explicit;
  try {
    const host = new URL(text(item?.content_url || item?.url)).hostname.toLowerCase();
    if (host === "youtu.be" || host.endsWith(".youtube.com")) return "youtube";
    if (host === "x.com" || host.endsWith(".x.com") || host.endsWith(".twitter.com")) {
      return "twitter";
    }
    if (host.endsWith(".zhihu.com")) return "zhihu";
    if (host.endsWith(".bilibili.com") || host === "b23.tv") return "bilibili";
    return "web";
  } catch {
    return "bilibili";
  }
}

/**
 * @param {SavedItemInput} [item]
 * @returns {SavedItemInput & {
 *   item_key: string,
 *   source_platform: string,
 *   content_id: string,
 *   content_url: string,
 *   content_type: string,
 * }}
 */
export function normalizeSavedItem(item = {}) {
  const sourcePlatform = inferPlatform(item);
  const legacyId = text(item.bvid);
  const contentId = text(item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : ""));
  const contentUrl = text(item.content_url || item.url);
  return {
    ...item,
    item_key: text(item.item_key) || (contentId ? `${sourcePlatform}:${contentId}` : ""),
    source_platform: sourcePlatform,
    content_id: contentId,
    content_url: contentUrl,
    content_type: text(item.content_type)
      || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
  };
}

function savedListPath(listKind) {
  if (listKind !== "favorite" && listKind !== "watch_later") {
    throw new TypeError(`Unknown saved list: ${listKind}`);
  }
  return `/saved/${listKind}`;
}

export function createStrictSavedApi(requestJsonStrict) {
  if (typeof requestJsonStrict !== "function") {
    throw new TypeError("requestJsonStrict is required");
  }
  const readTimeout = 10_000;
  const writeTimeout = 12_000;
  const json = (body, timeoutMs = writeTimeout) => ({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs,
  });
  return {
    save(listKind, item) {
      const normalized = normalizeSavedItem(item);
      return requestJsonStrict(savedListPath(listKind), json({
        source_platform: normalized.source_platform,
        content_id: normalized.content_id,
        content_url: normalized.content_url,
        content_type: normalized.content_type,
        title: text(normalized.title),
        author_name: text(normalized.author_name || normalized.up_name || normalized.author),
        cover_url: text(normalized.cover_url),
        note: text(normalized.note),
      }));
    },
    remove(listKind, itemKey) {
      return requestJsonStrict(`${savedListPath(listKind)}/remove`, json({ item_key: text(itemKey) }));
    },
    status(listKind, itemKey) {
      return requestJsonStrict(
        `${savedListPath(listKind)}/status?item_key=${encodeURIComponent(text(itemKey))}`,
        { timeoutMs: readTimeout },
      );
    },
    list(listKind, limit = 100, offset = 0) {
      return requestJsonStrict(
        `${savedListPath(listKind)}?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
        { timeoutMs: readTimeout },
      );
    },
    sync(listKind, values) {
      return requestJsonStrict(`${savedListPath(listKind)}/sync`, json({ item_keys: values }));
    },
    pollTask(taskId) {
      return requestJsonStrict(`/saved-sync/tasks/${encodeURIComponent(text(taskId))}`, {
        timeoutMs: readTimeout,
      });
    },
  };
}

export function taskIsTerminal(task) {
  const rows = Array.isArray(task?.items) ? task.items : [];
  return rows.every((item) => TERMINAL_STATUSES.has(item?.status));
}

export const isSavedTaskTerminal = taskIsTerminal;

export function getSavedSyncPresentation(item = {}) {
  const rawStatus = text(item.sync_status) || "not_started";
  const status = KNOWN_STATUSES.has(rawStatus) ? rawStatus : "failed";
  const errorCode = text(item.error_code);
  const busy = status === "syncing" || (status === "pending" && Boolean(text(item.sync_task_id)));
  const localOnly = status === "unsupported" && errorCode === "unsupported_content_type";
  let labelKey = {
    not_started: "pending",
    pending: "pending",
    syncing: "syncing",
    synced: "synced",
    already_synced: "synced",
    login_required: "login_required",
    unsupported: "local_only",
    rate_limited: "sync_failed",
    extension_required: "extension_required",
    failed: "sync_failed",
  }[status];
  let tone = {
    not_started: "neutral",
    pending: "info",
    syncing: "info",
    synced: "success",
    already_synced: "success",
    login_required: "warning",
    unsupported: "neutral",
    rate_limited: "error",
    extension_required: "warning",
    failed: "error",
  }[status];
  let retryable = ["login_required", "rate_limited", "extension_required", "failed"].includes(status);
  let detailKey = busy ? "busy" : status;
  if (localOnly) {
    detailKey = "unsupported_content_type";
  } else if (status === "unsupported" && errorCode === "unsupported_adapter_missing") {
    labelKey = "upgrade_required";
    detailKey = "unsupported_adapter_missing";
    tone = "warning";
    retryable = true;
  } else if (status === "unsupported") {
    labelKey = "sync_unavailable";
    detailKey = "unsupported";
    tone = "warning";
    retryable = true;
  }
  const actionable = !busy
    && status !== "synced"
    && status !== "already_synced"
    && !localOnly;
  return {
    status,
    labelKey,
    detailKey,
    actionKey: busy ? "syncing" : (retryable ? "retry" : "sync"),
    tone,
    retryable,
    actionable,
    busy,
    localOnly,
  };
}

export function isSavedSyncEligibleStatus(status, errorCode = "", syncTaskId = "") {
  return getSavedSyncPresentation({
    sync_status: status,
    error_code: errorCode,
    sync_task_id: syncTaskId,
  }).actionable;
}

export function updateSavedBatchButtonState(button, pendingCount) {
  const disabled = pendingCount <= 0;
  button.disabled = disabled;
  button.setAttribute("aria-disabled", String(disabled));
  button.removeAttribute("aria-busy");
}

export function createRetainedSavedListState() {
  let value = { items: [], total: 0, loaded: false, error: "" };
  return {
    commit(payload = {}) {
      value = {
        items: Array.isArray(payload.items) ? payload.items : [],
        total: Number(payload.total) || 0,
        loaded: true,
        error: "",
      };
    },
    fail(reason) {
      value = { ...value, error: text(reason?.message || reason || "saved_list_load_failed") };
    },
    snapshot() {
      return { ...value, items: [...value.items] };
    },
  };
}

export function createSavedMutationRegistry() {
  const saved = new Set();
  const busy = new Set();
  const versions = new Map();
  const composite = (listKind, itemKey) => `${text(listKind)}:${text(itemKey)}`;
  const bump = (key) => {
    const next = (versions.get(key) || 0) + 1;
    versions.set(key, next);
  };
  return {
    isBusy(listKind, itemKey) {
      return busy.has(composite(listKind, itemKey));
    },
    isSaved(listKind, itemKey) {
      return saved.has(composite(listKind, itemKey));
    },
    setSaved(listKind, itemKey, value) {
      const key = composite(listKind, itemKey);
      bump(key);
      if (value) saved.add(key);
      else saved.delete(key);
    },
    async hydrate(listKind, itemKey, load) {
      const key = composite(listKind, itemKey);
      const version = versions.get(key) || 0;
      try {
        const result = await load(itemKey);
        if (busy.has(key) || (versions.get(key) || 0) !== version) return result;
        if (result?.saved === true) saved.add(key);
        if (result?.saved === false) saved.delete(key);
        return result;
      } catch {
        return null;
      }
    },
    async toggle(listKind, itemKey, operations) {
      const key = composite(listKind, itemKey);
      if (busy.has(key)) return false;
      const wasSaved = saved.has(key);
      busy.add(key);
      bump(key);
      if (wasSaved) saved.delete(key);
      else saved.add(key);
      try {
        const result = await (wasSaved ? operations.remove(itemKey) : operations.add(itemKey));
        const finalSaved = typeof result?.saved === "boolean" ? result.saved : !wasSaved;
        bump(key);
        if (finalSaved) saved.add(key);
        else saved.delete(key);
        return true;
      } catch (error) {
        bump(key);
        if (wasSaved) saved.add(key);
        else saved.delete(key);
        throw error;
      } finally {
        busy.delete(key);
      }
    },
  };
}

export function captureSavedFocus(root, activeElement) {
  if (!root || !activeElement) return null;
  const listAction = text(activeElement.dataset?.savedListAction);
  if (listAction) return { kind: "list", action: listAction };
  const card = activeElement.closest?.("[data-item-key]");
  const itemKey = text(card?.dataset?.itemKey);
  const action = text(activeElement.dataset?.savedAction);
  const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []);
  const index = cards.indexOf(card);
  return itemKey && action ? { itemKey, action, index: Math.max(0, index) } : null;
}

export function restoreSavedFocus(root, token) {
  if (!root || !token?.action) return false;
  const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []);
  const focusAction = (card) => {
    const actions = Array.from(card?.querySelectorAll?.("[data-saved-action]") || []);
    const action = actions.find((candidate) => candidate.dataset?.savedAction === token.action)
      || actions[0];
    action?.focus?.();
    return Boolean(action);
  };
  if (token.kind === "list" || token.itemKey === "__list__") {
    const sameListAction = root.querySelector?.(`[data-saved-list-action="${token.action}"]`);
    if (sameListAction) {
      sameListAction.focus?.();
      return true;
    }
    if (focusAction(cards[0])) return true;
    const heading = root.querySelector?.("[data-saved-heading]");
    if (heading) {
      heading.focus?.();
      return true;
    }
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
    if (exact) {
      exact.focus?.();
      return true;
    }
  }
  const index = sameIndex >= 0
    ? sameIndex + 1
    : Math.max(0, Math.min(Number(token.index) || 0, cards.length));
  const previousIndex = sameIndex >= 0 ? sameIndex - 1 : index - 1;
  if (focusAction(cards[index]) || focusAction(cards[previousIndex])) return true;
  const listAction = root.querySelector?.(
    '[data-saved-list-action="sync-all"], [data-saved-list-action="retry"]',
  );
  if (listAction) {
    listAction.focus?.();
    return true;
  }
  const heading = root.querySelector?.("[data-saved-heading]");
  if (heading) {
    heading.focus?.();
    return true;
  }
  return false;
}

export function createDialogFocusController(options = {}) {
  const dialog = options.dialog;
  const opener = options.opener;
  const document = options.document;
  let active = false;
  const focusables = () => Array.from(dialog?.querySelectorAll?.(FOCUSABLE_SELECTOR) || [])
    .filter((node) => node.hidden !== true && !node.closest?.("[hidden], [inert]"));
  const onKeydown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault?.();
      options.onClose?.();
      return;
    }
    if (event.key !== "Tab") return;
    const nodes = focusables();
    if (!nodes.length) {
      event.preventDefault?.();
      dialog?.focus?.();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document?.activeElement === first) {
      event.preventDefault?.();
      last.focus?.();
    } else if (!event.shiftKey && document?.activeElement === last) {
      event.preventDefault?.();
      first.focus?.();
    }
  };
  return {
    activate() {
      if (active) return;
      active = true;
      document?.addEventListener?.("keydown", onKeydown);
    },
    deactivate() {
      if (!active) return;
      active = false;
      document?.removeEventListener?.("keydown", onKeydown);
      const focusTarget = options.resolveOpener ? options.resolveOpener() : opener;
      focusTarget?.focus?.();
    },
  };
}

export function createDurableTaskTracker(options = {}) {
  const { poll, now, schedule, cancel } = options;
  if (typeof poll !== "function") throw new TypeError("poll is required");
  if (typeof now !== "function") throw new TypeError("now is required");
  if (typeof schedule !== "function") throw new TypeError("schedule is required");
  if (typeof cancel !== "function") throw new TypeError("cancel is required");
  const isVisible = options.isVisible || (() => true);
  const foregroundHorizonMs = Number(options.foregroundHorizonMs ?? 20_000);
  const visibleDelayMs = Number(options.visibleDelayMs ?? 750);
  const hiddenDelayMs = Number(options.hiddenDelayMs ?? 5_000);
  const active = new Map();

  const queue = (entry, explicitDelay = null) => {
    if (!active.has(entry.taskId)) return;
    const delay = explicitDelay ?? (isVisible() ? visibleDelayMs : hiddenDelayMs);
    entry.timer = schedule(() => tick(entry), delay);
  };
  const tick = async (entry) => {
    if (!active.has(entry.taskId) || entry.polling) return;
    entry.polling = true;
    try {
      const next = await poll(entry.taskId);
      if (!active.has(entry.taskId)) return;
      if (next && typeof next === "object") entry.task = next;
      if (taskIsTerminal(entry.task)) {
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
  const resume = (taskId) => {
    const entry = active.get(text(taskId));
    if (!entry || entry.polling) return false;
    if (entry.timer != null) cancel(entry.timer);
    queue(entry, 0);
    return true;
  };

  return {
    has(taskId) {
      return active.has(text(taskId));
    },
    track(initial, callbacks = {}) {
      const taskId = text(initial?.task_id);
      if (!taskId) return null;
      const existing = active.get(taskId);
      if (taskIsTerminal(initial)) {
        if (existing?.timer != null) cancel(existing.timer);
        active.delete(taskId);
        callbacks.onTerminal?.(initial);
        return taskId;
      }
      if (existing) {
        existing.task = initial;
        existing.callbacks = { ...existing.callbacks, ...callbacks };
        return taskId;
      }
      const entry = {
        taskId,
        task: initial,
        callbacks,
        startedAt: now(),
        backgroundAnnounced: false,
        polling: false,
        timer: null,
      };
      active.set(taskId, entry);
      callbacks.onProgress?.(initial);
      queue(entry);
      return taskId;
    },
    resume,
    stop(taskId) {
      const entry = active.get(text(taskId));
      if (!entry) return false;
      if (entry.timer != null) cancel(entry.timer);
      active.delete(entry.taskId);
      return true;
    },
    resumeAll() {
      let resumed = 0;
      for (const taskId of active.keys()) if (resume(taskId)) resumed += 1;
      return resumed;
    },
    dispose() {
      for (const entry of active.values()) if (entry.timer != null) cancel(entry.timer);
      active.clear();
    },
  };
}

export function createSavedTaskCoordinator(options = {}) {
  const tracker = options.tracker;
  const fetchTask = options.fetchTask;
  if (!tracker || typeof tracker.track !== "function") {
    throw new TypeError("tracker is required");
  }
  if (typeof fetchTask !== "function") throw new TypeError("fetchTask is required");
  const ownersByTask = new Map();
  const taskByItem = new Map();
  const recovering = new Map();
  let disposed = false;

  const release = (taskId) => {
    for (const itemKey of ownersByTask.get(taskId) || []) {
      if (taskByItem.get(itemKey) === taskId) taskByItem.delete(itemKey);
    }
    ownersByTask.delete(taskId);
  };
  const claim = (taskId, values) => {
    const keys = new Set(ownersByTask.get(taskId) || []);
    for (const key of itemKeys(values)) keys.add(key);
    ownersByTask.set(taskId, keys);
    for (const key of keys) taskByItem.set(key, taskId);
  };
  const track = (task, values, callbacks = {}) => {
    const taskId = text(task?.task_id);
    if (!taskId || disposed) return null;
    claim(taskId, values);
    return tracker.track(task, {
      ...callbacks,
      onTerminal(terminalTask) {
        release(taskId);
        callbacks.onTerminal?.(terminalTask);
        options.onTerminal?.(terminalTask);
      },
      onProgress(progressTask) {
        callbacks.onProgress?.(progressTask);
        options.onProgress?.(progressTask);
      },
      onBackground(progressTask) {
        callbacks.onBackground?.(progressTask);
        options.onBackground?.(progressTask);
      },
      onPollError(error, progressTask) {
        callbacks.onPollError?.(error, progressTask);
        options.onPollError?.(error, progressTask);
      },
    });
  };

  return {
    owns(itemKey) {
      return taskByItem.has(text(itemKey));
    },
    taskFor(itemKey) {
      return taskByItem.get(text(itemKey)) || "";
    },
    track,
    async recover(rows, callbacks = {}) {
      if (disposed) return;
      const grouped = new Map();
      for (const row of Array.isArray(rows) ? rows : []) {
        if (row?.sync_status !== "pending" && row?.sync_status !== "syncing") continue;
        const taskId = text(row?.sync_task_id);
        const itemKey = text(row?.item_key);
        if (!taskId || !itemKey) continue;
        if (!grouped.has(taskId)) grouped.set(taskId, []);
        grouped.get(taskId).push(itemKey);
      }
      await Promise.all([...grouped].map(async ([taskId, values]) => {
        claim(taskId, values);
        if (tracker.has(taskId)) return;
        if (recovering.has(taskId)) return recovering.get(taskId);
        const recovery = Promise.resolve()
          .then(() => fetchTask(taskId))
          .then((task) => track(task, values, callbacks))
          .catch((error) => {
            if (disposed) return;
            track({
              task_id: taskId,
              items: values.map((item_key) => ({ item_key, status: "syncing" })),
            }, values, callbacks);
            callbacks.onPollError?.(error);
          })
          .finally(() => recovering.delete(taskId));
        recovering.set(taskId, recovery);
        return recovery;
      }));
    },
    resumeAll() {
      return tracker.resumeAll?.() || 0;
    },
    dispose() {
      disposed = true;
      recovering.clear();
      ownersByTask.clear();
      taskByItem.clear();
      tracker.dispose?.();
    },
  };
}

const savedSyncCore = Object.freeze({
  captureSavedFocus,
  createDialogFocusController,
  createDurableTaskTracker,
  createRetainedSavedListState,
  createSavedMutationRegistry,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  createStrictSavedApi,
  getSavedSyncPresentation,
  isSavedSyncEligibleStatus,
  isSavedTaskTerminal,
  normalizeSavedItem,
  restoreSavedFocus,
  taskIsTerminal,
  updateSavedBatchButtonState,
});

if (typeof globalThis === "object") globalThis.OBCSavedSyncCore = savedSyncCore;

export { savedSyncCore };
