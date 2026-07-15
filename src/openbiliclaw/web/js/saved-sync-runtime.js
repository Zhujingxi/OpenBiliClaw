const TERMINAL = new Set([
  "synced", "already_synced", "login_required", "unsupported",
  "rate_limited", "extension_required", "failed",
]);

function keyFor(listKind, itemKey) {
  return `${String(listKind || "").trim()}:${String(itemKey || "").trim()}`;
}

export function createSavedSubmissionFence() {
  const keys = new Set();
  const normalize = (value) => String(value || "").trim();
  return {
    has(itemKey) { return keys.has(normalize(itemKey)); },
    claim(itemKeys) {
      const candidates = [...new Set((Array.isArray(itemKeys) ? itemKeys : []).map(normalize))]
        .filter(Boolean);
      if (!candidates.length || candidates.some((key) => keys.has(key))) return false;
      for (const key of candidates) keys.add(key);
      return true;
    },
    release(itemKeys) {
      for (const itemKey of Array.isArray(itemKeys) ? itemKeys : []) keys.delete(normalize(itemKey));
    },
  };
}

export function isSavedTaskTerminal(task) {
  const rows = Array.isArray(task?.items) ? task.items : [];
  return rows.every((item) => TERMINAL.has(item?.status));
}

export function createRetainedSavedListState() {
  let items = [];
  let total = 0;
  let loaded = false;
  let error = "";
  return {
    commit(payload = {}) {
      items = Array.isArray(payload.items) ? payload.items : [];
      total = Number(payload.total) || 0;
      loaded = true;
      error = "";
    },
    fail(reason) {
      error = String(reason?.message || reason || "保存列表加载失败。").trim();
    },
    snapshot() {
      return { items: [...items], total, loaded, error };
    },
  };
}

export function createSavedMutationRegistry() {
  const saved = new Set();
  const busy = new Set();
  const versions = new Map();
  const nextVersion = (key) => {
    const value = (versions.get(key) || 0) + 1;
    versions.set(key, value);
    return value;
  };
  return {
    isBusy(listKind, itemKey) { return busy.has(keyFor(listKind, itemKey)); },
    isSaved(listKind, itemKey) { return saved.has(keyFor(listKind, itemKey)); },
    setSaved(listKind, itemKey, value) {
      const key = keyFor(listKind, itemKey);
      nextVersion(key);
      if (value) saved.add(key); else saved.delete(key);
    },
    async hydrate(listKind, itemKey, load) {
      const key = keyFor(listKind, itemKey);
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
      const key = keyFor(listKind, itemKey);
      if (busy.has(key)) return false;
      const wasSaved = saved.has(key);
      busy.add(key);
      nextVersion(key);
      if (wasSaved) saved.delete(key); else saved.add(key);
      try {
        const result = await (wasSaved ? operations.remove(itemKey) : operations.add(itemKey));
        const finalSaved = typeof result?.saved === "boolean" ? result.saved : !wasSaved;
        nextVersion(key);
        if (finalSaved) saved.add(key); else saved.delete(key);
        return true;
      } catch (error) {
        nextVersion(key);
        if (wasSaved) saved.add(key); else saved.delete(key);
        throw error;
      } finally {
        busy.delete(key);
      }
    },
  };
}

export function captureSavedFocus(root, activeElement = globalThis.document?.activeElement) {
  if (!root || !activeElement) return null;
  const listAction = String(activeElement.dataset?.savedListAction || "").trim();
  if (listAction) return { kind: "list", action: listAction };
  const card = activeElement.closest?.("[data-item-key]");
  const itemKey = String(card?.dataset?.itemKey || "").trim();
  const action = String(activeElement.dataset?.savedAction || "").trim();
  const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []);
  const index = cards.indexOf(card);
  return itemKey && action ? { itemKey, action, index: Math.max(0, index) } : null;
}

export function restoreSavedFocus(root, token) {
  if (!root || !token?.action) return false;
  const cards = Array.from(root.querySelectorAll?.("[data-item-key]") || []);
  const focusAction = (card, preferred = token.action) => {
    const actions = Array.from(card?.querySelectorAll?.("[data-saved-action]") || []);
    const action = actions.find((candidate) => candidate.dataset?.savedAction === preferred)
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

export function createDialogFocusController(options = {}) {
  const dialog = options.dialog;
  const opener = options.opener;
  const doc = options.document || globalThis.document;
  let active = false;
  const focusables = () => Array.from(dialog?.querySelectorAll?.(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
  ) || []).filter(
    (node) => node.hidden !== true && !node.closest?.("[hidden], [inert]"),
  );
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
    if (event.shiftKey && doc.activeElement === first) {
      event.preventDefault?.();
      last.focus?.();
    } else if (!event.shiftKey && doc.activeElement === last) {
      event.preventDefault?.();
      first.focus?.();
    }
  };
  return {
    activate() {
      if (active) return;
      active = true;
      doc?.addEventListener?.("keydown", onKeydown);
    },
    deactivate() {
      if (!active) return;
      active = false;
      doc?.removeEventListener?.("keydown", onKeydown);
      const focusTarget = options.resolveOpener ? options.resolveOpener() : opener;
      focusTarget?.focus?.();
    },
  };
}

export function createDurableTaskTracker(options = {}) {
  const poll = options.poll;
  const now = options.now || Date.now;
  const isVisible = options.isVisible || (() => typeof document === "undefined" || !document.hidden);
  const schedule = options.schedule || ((run, delay) => setTimeout(run, delay));
  const cancel = options.cancel || clearTimeout;
  const foregroundHorizonMs = Number(options.foregroundHorizonMs ?? 20_000);
  const visibleDelayMs = Number(options.visibleDelayMs ?? 750);
  const hiddenDelayMs = Number(options.hiddenDelayMs ?? 5_000);
  const active = new Map();

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
      if (next && typeof next === "object") entry.task = next;
      if (isSavedTaskTerminal(entry.task)) {
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
    has(taskId) { return active.has(String(taskId || "").trim()); },
    track(initial, callbacks = {}) {
      const taskId = String(initial?.task_id || "").trim();
      if (!taskId) return null;
      if (isSavedTaskTerminal(initial)) {
        callbacks.onTerminal?.(initial);
        return taskId;
      }
      const existing = active.get(taskId);
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
    resume(taskId) {
      const entry = active.get(String(taskId || "").trim());
      if (!entry || entry.polling) return false;
      if (entry.timer != null) cancel(entry.timer);
      queue(entry, 0);
      return true;
    },
    stop(taskId) {
      const entry = active.get(String(taskId || "").trim());
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
    for (const key of (itemKeys || []).map((value) => String(value || "").trim()).filter(Boolean)) {
      keys.add(key);
    }
    ownersByTask.set(taskId, keys);
    for (const key of keys) taskByItem.set(key, taskId);
  };
  const track = (task, itemKeys, callbacks = {}) => {
    const taskId = String(task?.task_id || "").trim();
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
    owns(itemKey) { return taskByItem.has(String(itemKey || "").trim()); },
    taskFor(itemKey) { return taskByItem.get(String(itemKey || "").trim()) || ""; },
    track,
    async recover(rows, callbacks = {}) {
      if (disposed) return;
      const grouped = new Map();
      for (const row of Array.isArray(rows) ? rows : []) {
        if (!["pending", "syncing"].includes(row?.sync_status)) continue;
        const taskId = String(row?.sync_task_id || "").trim();
        const itemKey = String(row?.item_key || "").trim();
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
