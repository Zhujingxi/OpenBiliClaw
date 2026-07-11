const TERMINAL = new Set([
  "synced", "already_synced", "login_required", "unsupported",
  "rate_limited", "extension_required", "failed",
]);

function keyFor(listKind, itemKey) {
  return `${String(listKind || "").trim()}:${String(itemKey || "").trim()}`;
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
  const card = activeElement.closest?.("[data-item-key]");
  const itemKey = String(card?.dataset?.itemKey || "").trim();
  const action = String(activeElement.dataset?.savedAction || "").trim();
  return itemKey && action ? { itemKey, action } : null;
}

export function restoreSavedFocus(root, token) {
  if (!root || !token?.itemKey || !token?.action) return false;
  const cards = root.querySelectorAll?.("[data-item-key]") || [];
  for (const card of cards) {
    if (card.dataset?.itemKey !== token.itemKey) continue;
    const actions = card.querySelectorAll?.("[data-saved-action]") || [];
    for (const action of actions) {
      if (action.dataset?.savedAction !== token.action) continue;
      action.focus?.();
      return true;
    }
  }
  return false;
}

export function createDialogFocusController(options = {}) {
  const dialog = options.dialog;
  const opener = options.opener;
  const doc = options.document || globalThis.document;
  let active = false;
  const focusables = () => Array.from(dialog?.querySelectorAll?.(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
  ) || []).filter((node) => node.hidden !== true);
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
      opener?.focus?.();
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
      entry.callbacks.onPollError?.(error, entry.task);
    } finally {
      entry.polling = false;
    }
    queue(entry);
  };

  return {
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
  };
}
