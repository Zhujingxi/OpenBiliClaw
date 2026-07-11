function normalizeBvid(bvid) {
  return String(bvid || "").trim();
}

function inferSavedPlatform(value, contentUrl) {
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
export function normalizeCanonicalSavedItem(item = {}) {
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

export function partitionSavedQueueResults(queue, results) {
  const rows = Array.isArray(queue) ? queue : [];
  const outcomes = Array.isArray(results) ? results : [];
  const saved = [];
  const savedIndexes = new Set();
  outcomes.forEach((result, index) => {
    if (result?.status !== "fulfilled" || result.value?.saved === false || !rows[index]) return;
    savedIndexes.add(index);
    saved.push({
      index,
      item: rows[index],
      itemKey: String(result.value?.item_key || rows[index]?.item_key || "").trim(),
      value: result.value,
    });
  });
  return {
    saved,
    remaining: rows.filter((_, index) => !savedIndexes.has(index)),
    savedCount: saved.length,
    failedCount: rows.length - saved.length,
  };
}

const SAVED_SYNC_STATUSES = new Set([
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

const SYNC_PRESENTATIONS = {
  pending: { label: "待同步", tone: "neutral", retryable: false },
  syncing: { label: "同步中", tone: "info", retryable: false },
  synced: { label: "已同步", tone: "success", retryable: false },
  already_synced: { label: "已同步", tone: "success", retryable: false },
  login_required: { label: "需要登录", tone: "warning", retryable: true },
  unsupported: { label: "同步失败", tone: "error", retryable: true },
  rate_limited: { label: "同步失败", tone: "error", retryable: true },
  extension_required: { label: "需要连接插件", tone: "warning", retryable: true },
  failed: { label: "同步失败", tone: "error", retryable: true },
};

const PLATFORM_LABELS = {
  bilibili: "B站",
  youtube: "YouTube",
  twitter: "X",
  xiaohongshu: "小红书",
  douyin: "抖音",
  zhihu: "知乎",
  reddit: "Reddit",
};

function safeSyncText(value, maxLength = 240) {
  return String(value || "").replace(/[\p{C}\p{Zl}\p{Zp}]/gu, "").trim().slice(0, maxLength);
}

export function getSavedSyncPresentation(status) {
  return { ...(SYNC_PRESENTATIONS[status] || SYNC_PRESENTATIONS.failed) };
}

export function sanitizeSavedSyncTask(payload) {
  const rows = Array.isArray(payload?.items) ? payload.items : [];
  return {
    task_id: safeSyncText(payload?.task_id, 64),
    items: rows.slice(0, 500).map((item) => ({
      item_key: safeSyncText(item?.item_key, 2048),
      status: SAVED_SYNC_STATUSES.has(item?.status) ? item.status : "failed",
      resolved_action: item?.resolved_action === "watch_later" ? "watch_later" : "favorite",
      resolved_target: safeSyncText(item?.resolved_target),
      error_code: safeSyncText(item?.error_code, 96),
      error_message: safeSyncText(item?.error_message),
    })),
  };
}

export function summarizeSavedSyncResults(items) {
  const groups = new Map();
  for (const item of Array.isArray(items) ? items : []) {
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
  let value = { items: [], total: 0, loaded: false, error: "" };
  return {
    commit(payload = {}) {
      const items = Array.isArray(payload.items) ? payload.items : [];
      value = { items, total: Number(payload.total) || 0, loaded: true, error: "" };
    },
    fail(reason) {
      value = { ...value, error: String(reason?.message || reason || "保存列表加载失败。").trim() };
    },
    snapshot() { return { ...value, items: [...value.items] }; },
  };
}

export function captureSavedFocus(root, activeElement = globalThis.document?.activeElement) {
  const card = activeElement?.closest?.("[data-item-key]");
  const itemKey = String(card?.dataset?.itemKey || "").trim();
  const action = String(activeElement?.dataset?.savedAction || "").trim();
  const cards = Array.from(root?.querySelectorAll?.("[data-item-key]") || []);
  const index = cards.indexOf(card);
  return root && itemKey && action ? { itemKey, action, index: Math.max(0, index) } : null;
}

export function restoreSavedFocus(root, token) {
  if (!root || !token?.itemKey || !token?.action) return false;
  const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []);
  const focusAction = (card) => {
    const actions = Array.from(card?.querySelectorAll?.("[data-saved-action]") || []);
    const action = actions.find((candidate) => candidate.dataset?.savedAction === token.action)
      || actions[0];
    action?.focus?.();
    return Boolean(action);
  };
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

export function createSavedSyncTaskTracker(options = {}) {
  const poll = options.poll;
  const now = options.now || Date.now;
  const isVisible = options.isVisible || (() => typeof document === "undefined" || !document.hidden);
  const schedule = options.schedule || ((run, delay) => setTimeout(run, delay));
  const cancel = options.cancel || clearTimeout;
  const foregroundHorizonMs = Number(options.foregroundHorizonMs ?? 20_000);
  const visibleDelayMs = Number(options.visibleDelayMs ?? 750);
  const hiddenDelayMs = Number(options.hiddenDelayMs ?? 5_000);
  const active = new Map();
  const terminal = (task) => {
    const rows = Array.isArray(task?.items) ? task.items : [];
    return rows.every((item) => SAVED_SYNC_STATUSES.has(item?.status)
      && !["pending", "syncing"].includes(item.status));
  };
  const queue = (entry, delay = null) => {
    if (!active.has(entry.taskId)) return;
    entry.timer = schedule(
      () => tick(entry),
      delay ?? (isVisible() ? visibleDelayMs : hiddenDelayMs),
    );
  };
  const tick = async (entry) => {
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
  const api = {
    has(taskId) { return active.has(safeSyncText(taskId, 64)); },
    track(initial, callbacks = {}) {
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
      const entry = {
        taskId, task, callbacks, startedAt: now(), backgroundAnnounced: false,
        polling: false, timer: null,
      };
      active.set(taskId, entry);
      callbacks.onProgress?.(task);
      queue(entry);
      return taskId;
    },
    resume(taskId) {
      const entry = active.get(safeSyncText(taskId, 64));
      if (!entry || entry.polling) return false;
      if (entry.timer != null) cancel(entry.timer);
      queue(entry, 0);
      return true;
    },
    stop(taskId) {
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

export function createSavedTaskCoordinator(options = {}) {
  const tracker = options.tracker;
  const fetchTask = options.fetchTask;
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
  const claim = (taskId, itemKeys) => {
    const keys = new Set(ownersByTask.get(taskId) || []);
    for (const key of (itemKeys || []).map((value) => safeSyncText(value, 2048)).filter(Boolean)) {
      keys.add(key);
    }
    ownersByTask.set(taskId, keys);
    for (const key of keys) taskByItem.set(key, taskId);
  };
  const track = (task, itemKeys, callbacks = {}) => {
    const taskId = safeSyncText(task?.task_id, 64);
    if (!taskId || disposed) return null;
    claim(taskId, itemKeys);
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
    owns(itemKey) { return taskByItem.has(safeSyncText(itemKey, 2048)); },
    taskFor(itemKey) { return taskByItem.get(safeSyncText(itemKey, 2048)) || ""; },
    track,
    async recover(rows, callbacks = {}) {
      if (disposed) return;
      const grouped = new Map();
      for (const row of Array.isArray(rows) ? rows : []) {
        if (!["pending", "syncing"].includes(row?.sync_status)) continue;
        const taskId = safeSyncText(row?.sync_task_id, 64);
        const itemKey = safeSyncText(row?.item_key, 2048);
        if (!taskId || !itemKey) continue;
        if (!grouped.has(taskId)) grouped.set(taskId, []);
        grouped.get(taskId).push(itemKey);
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

function mergeLabels(baseLabels, overrideLabels) {
  return {
    checkedTitle: "取消保存",
    uncheckedTitle: "保存",
    ...baseLabels,
    ...overrideLabels,
  };
}

function applyButtonState(button, saved, labels) {
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

export function createSavedToggleRegistry({ labels = {}, onChange = null } = {}) {
  const defaultLabels = mergeLabels(labels);
  const savedBvids = new Set();
  const buttonsByBvid = new Map();
  const mutationVersions = new Map();
  const busyBvids = new Set();

  function nextVersion(bvid) {
    const version = (mutationVersions.get(bvid) || 0) + 1;
    mutationVersions.set(bvid, version);
    return version;
  }

  function isDetached(button) {
    // Buttons removed from the DOM (e.g. via replaceChildren on re-render)
    // report isConnected === false. Test doubles that omit the property
    // (isConnected === undefined) are treated as live and kept.
    return button != null && button.isConnected === false;
  }

  function syncButtons(bvid) {
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

  function applySaved(key, saved) {
    if (saved) {
      savedBvids.add(key);
    } else {
      savedBvids.delete(key);
    }
    syncButtons(key);
  }

  function setSaved(bvid, saved) {
    const key = normalizeBvid(bvid);
    if (!key) return;
    nextVersion(key);
    applySaved(key, saved);
  }

  function registerButton(bvid, button, buttonLabels = {}) {
    const key = normalizeBvid(bvid);
    if (!key || !button) return () => {};
    const entry = {
      button,
      labels: mergeLabels(defaultLabels, buttonLabels),
    };
    if (!buttonsByBvid.has(key)) {
      buttonsByBvid.set(key, new Set());
    }
    buttonsByBvid.get(key).add(entry);
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

  async function hydrateStatus(bvid, loadStatus) {
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
      if (result && typeof result.saved === "boolean") {
        applySaved(key, result.saved);
      }
      return result;
    } catch {
      return null;
    }
  }

  async function toggle(bvid, { add, remove }) {
    const key = normalizeBvid(bvid);
    if (!key || busyBvids.has(key)) return false;
    const wasSaved = savedBvids.has(key);
    const optimisticSaved = !wasSaved;
    busyBvids.add(key);
    nextVersion(key);
    applySaved(key, optimisticSaved);
    try {
      const result = await (wasSaved ? remove(key) : add(key));
      const finalSaved = result && typeof result.saved === "boolean"
        ? result.saved
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
    isSaved(bvid) {
      return savedBvids.has(normalizeBvid(bvid));
    },
    pruneDetached,
    registerButton,
    setSaved,
    toggle,
  };
}
