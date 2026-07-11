(function installSavedSyncCore(global) {
  "use strict";

  const TERMINAL_STATUSES = new Set([
    "synced", "already_synced", "login_required", "unsupported",
    "rate_limited", "extension_required", "failed",
  ]);

  function text(value) {
    return String(value || "").trim();
  }

  function inferPlatform(item) {
    const explicit = text(item?.source_platform || item?.platform).toLowerCase();
    if (explicit) return explicit;
    try {
      const host = new URL(text(item?.content_url || item?.url)).hostname.toLowerCase();
      if (host === "youtu.be" || host.endsWith(".youtube.com")) return "youtube";
      if (host === "x.com" || host.endsWith(".x.com") || host.endsWith(".twitter.com")) return "twitter";
      if (host.endsWith(".zhihu.com")) return "zhihu";
      if (host.endsWith(".bilibili.com") || host === "b23.tv") return "bilibili";
      return "web";
    } catch {
      return "bilibili";
    }
  }

  function normalizeSavedItem(item = {}) {
    const sourcePlatform = inferPlatform(item);
    const legacyId = text(item.bvid);
    const contentId = text(item.content_id || (legacyId && !legacyId.includes(":") ? legacyId : ""));
    const contentUrl = text(item.content_url || item.url);
    const explicitType = text(item.content_type);
    return {
      ...item,
      item_key: text(item.item_key) || (contentId ? `${sourcePlatform}:${contentId}` : ""),
      source_platform: sourcePlatform,
      content_id: contentId,
      content_url: contentUrl,
      content_type: explicitType || (sourcePlatform === "bilibili" && contentId ? "video" : ""),
    };
  }

  function assertListKind(listKind) {
    if (listKind !== "favorite" && listKind !== "watch_later") {
      throw new TypeError(`Unknown saved list: ${listKind}`);
    }
    return `/saved/${listKind}`;
  }

  function createStrictSavedApi(requestJsonStrict) {
    if (typeof requestJsonStrict !== "function") throw new TypeError("requestJsonStrict is required");
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
        return requestJsonStrict(assertListKind(listKind), json({
          source_platform: normalized.source_platform,
          content_id: normalized.content_id,
          content_url: normalized.content_url,
          content_type: normalized.content_type,
          title: text(normalized.title),
          author_name: text(normalized.author_name || normalized.up_name),
          cover_url: text(normalized.cover_url),
          note: text(normalized.note),
        }));
      },
      remove(listKind, itemKey) {
        return requestJsonStrict(`${assertListKind(listKind)}/remove`, json({ item_key: text(itemKey) }));
      },
      status(listKind, itemKey) {
        return requestJsonStrict(
          `${assertListKind(listKind)}/status?item_key=${encodeURIComponent(text(itemKey))}`,
          { timeoutMs: readTimeout },
        );
      },
      list(listKind, limit = 100, offset = 0) {
        return requestJsonStrict(
          `${assertListKind(listKind)}?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
          { timeoutMs: readTimeout },
        );
      },
      sync(listKind, itemKeys) {
        return requestJsonStrict(`${assertListKind(listKind)}/sync`, json({ item_keys: itemKeys }));
      },
      pollTask(taskId) {
        return requestJsonStrict(`/saved-sync/tasks/${encodeURIComponent(text(taskId))}`, {
          timeoutMs: readTimeout,
        });
      },
    };
  }

  function taskIsTerminal(task) {
    const rows = Array.isArray(task?.items) ? task.items : [];
    return rows.every((item) => TERMINAL_STATUSES.has(item?.status));
  }

  function createRetainedSavedListState() {
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
        value = { ...value, error: text(reason?.message || reason || "保存列表加载失败。") };
      },
      snapshot() { return { ...value, items: [...value.items] }; },
    };
  }

  function createSavedMutationRegistry() {
    const saved = new Set();
    const busy = new Set();
    const versions = new Map();
    const composite = (listKind, itemKey) => `${text(listKind)}:${text(itemKey)}`;
    const bump = (key) => {
      const next = (versions.get(key) || 0) + 1;
      versions.set(key, next);
      return next;
    };
    return {
      isBusy(listKind, itemKey) { return busy.has(composite(listKind, itemKey)); },
      isSaved(listKind, itemKey) { return saved.has(composite(listKind, itemKey)); },
      setSaved(listKind, itemKey, value) {
        const key = composite(listKind, itemKey);
        bump(key);
        if (value) saved.add(key); else saved.delete(key);
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
        if (wasSaved) saved.delete(key); else saved.add(key);
        try {
          const result = await (wasSaved ? operations.remove(itemKey) : operations.add(itemKey));
          const finalSaved = typeof result?.saved === "boolean" ? result.saved : !wasSaved;
          bump(key);
          if (finalSaved) saved.add(key); else saved.delete(key);
          return true;
        } catch (error) {
          bump(key);
          if (wasSaved) saved.add(key); else saved.delete(key);
          throw error;
        } finally {
          busy.delete(key);
        }
      },
    };
  }

  function captureSavedFocus(root, activeElement = global.document?.activeElement) {
    const listAction = text(activeElement?.dataset?.savedListAction);
    if (root && listAction) return { kind: "list", action: listAction };
    const card = activeElement?.closest?.("[data-item-key]");
    const itemKey = text(card?.dataset?.itemKey);
    const action = text(activeElement?.dataset?.savedAction);
    const cards = Array.from(root?.querySelectorAll?.("[data-item-key]") || []);
    const index = cards.indexOf(card);
    return root && itemKey && action ? { itemKey, action, index: Math.max(0, index) } : null;
  }

  function restoreSavedFocus(root, token) {
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

  function createDurableTaskTracker(options = {}) {
    const poll = options.poll;
    const now = options.now || Date.now;
    const isVisible = options.isVisible || (() => true);
    const schedule = options.schedule || ((run, delay) => global.setTimeout(run, delay));
    const cancel = options.cancel || ((handle) => global.clearTimeout(handle));
    const foregroundHorizonMs = Number(options.foregroundHorizonMs ?? 20_000);
    const visibleDelayMs = Number(options.visibleDelayMs ?? 750);
    const hiddenDelayMs = Number(options.hiddenDelayMs ?? 5_000);
    const active = new Map();

    function queue(entry) {
      if (!active.has(entry.taskId)) return;
      const delay = isVisible() ? visibleDelayMs : hiddenDelayMs;
      entry.timer = schedule(() => tick(entry), delay);
    }

    async function tick(entry) {
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
    }

    function track(initial, callbacks = {}) {
      const taskId = text(initial?.task_id);
      if (!taskId) return null;
      const existing = active.get(taskId);
      if (existing) {
        existing.callbacks = { ...existing.callbacks, ...callbacks };
        if (initial && typeof initial === "object") existing.task = initial;
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
      if (taskIsTerminal(initial)) {
        callbacks.onTerminal?.(initial);
        return taskId;
      }
      active.set(taskId, entry);
      callbacks.onProgress?.(initial);
      queue(entry);
      return taskId;
    }

    function resume(taskId) {
      const entry = active.get(text(taskId));
      if (!entry || entry.polling) return false;
      if (entry.timer != null) cancel(entry.timer);
      entry.timer = schedule(() => tick(entry), 0);
      return true;
    }

    const api = {
      has(taskId) { return active.has(text(taskId)); },
      resume,
      stop(taskId) {
        const entry = active.get(text(taskId));
        if (!entry) return false;
        if (entry.timer != null) cancel(entry.timer);
        active.delete(entry.taskId);
        return true;
      },
      track,
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
    return api;
  }

  function createSavedTaskCoordinator(options = {}) {
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
      for (const key of (itemKeys || []).map(text).filter(Boolean)) keys.add(key);
      ownersByTask.set(taskId, keys);
      for (const key of keys) taskByItem.set(key, taskId);
    };
    const track = (task, itemKeys, callbacks = {}) => {
      const taskId = text(task?.task_id);
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
      owns(itemKey) { return taskByItem.has(text(itemKey)); },
      taskFor(itemKey) { return taskByItem.get(text(itemKey)) || ""; },
      track,
      async recover(rows, callbacks = {}) {
        if (disposed) return;
        const grouped = new Map();
        for (const row of Array.isArray(rows) ? rows : []) {
          if (!["pending", "syncing"].includes(row?.sync_status)) continue;
          const taskId = text(row?.sync_task_id);
          const itemKey = text(row?.item_key);
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

  const api = {
    captureSavedFocus,
    createDurableTaskTracker,
    createSavedTaskCoordinator,
    createRetainedSavedListState,
    createSavedMutationRegistry,
    createStrictSavedApi,
    normalizeSavedItem,
    restoreSavedFocus,
    taskIsTerminal,
  };
  global.OpenBiliClawSavedSync = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
