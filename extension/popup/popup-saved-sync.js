function normalizeBvid(bvid) {
  return String(bvid || "").trim();
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
  unsupported: { label: "同步失败", tone: "error", retryable: false },
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
