/** Platform-neutral local saved lists with explicit native-sync controls. */

import {
  fetchSavedItems,
  pollSavedSyncTask,
  removeSavedItem,
  syncSavedItems,
} from "../api.js";
import { getCoverImageAttrs, buildContentUrl } from "../view-models.js";
import { openContentUrl } from "../app-launch.js";
import {
  captureSavedFocus,
  createDurableTaskTracker,
  createRetainedSavedListState,
  restoreSavedFocus,
} from "../saved-sync-runtime.js";

const PAGE_SIZE = 50;
const PRESENTATION = {
  pending: ["待同步", "neutral", false],
  syncing: ["同步中", "info", false],
  synced: ["已同步", "success", false],
  already_synced: ["已同步", "success", false],
  login_required: ["需要登录", "warning", true],
  unsupported: ["同步失败", "error", true],
  rate_limited: ["同步失败", "error", true],
  extension_required: ["需要连接插件", "warning", true],
  failed: ["同步失败", "error", true],
};
const PLATFORM_NAMES = {
  bilibili: "B站", youtube: "YouTube", twitter: "X", xiaohongshu: "小红书",
  douyin: "抖音", zhihu: "知乎", reddit: "Reddit",
};

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s == null ? "" : String(s);
  return el.innerHTML;
}

function safeText(value, maxLength = 240) {
  return String(value || "").replace(/[\p{C}\p{Zl}\p{Zp}]/gu, "").trim().slice(0, maxLength);
}

export function normalizeSavedListItem(item = {}) {
  const platform = safeText(item.source_platform || "bilibili", 64);
  const contentId = safeText(item.content_id || item.bvid || item.id, 2048);
  return {
    ...item,
    item_key: safeText(item.item_key || `${platform}:${contentId}`, 2048),
    source_platform: platform,
    content_id: contentId,
    content_url: safeText(item.content_url, 2048),
    content_type: safeText(item.content_type || "video", 128),
    title: safeText(item.title || contentId),
    author_name: safeText(item.author_name || item.up_name),
    cover_url: safeText(item.cover_url, 2048),
    sync_status: PRESENTATION[item.sync_status] ? item.sync_status : "failed",
    resolved_target: safeText(item.resolved_target),
    error_message: safeText(item.error_message),
  };
}

export function getSavedSyncViewModel(item) {
  const normalized = normalizeSavedListItem(item);
  const [label, tone, retryable] = PRESENTATION[normalized.sync_status];
  let detail = normalized.error_message || normalized.resolved_target || "平台目标将在同步时确认";
  if (normalized.sync_status === "extension_required") {
    detail = "请连接已安装 OpenBiliClaw 插件的登录态浏览器后重试。";
  }
  return { ...normalized, label, tone, retryable, detail };
}

function summarize(items) {
  const groups = new Map();
  for (const item of items) {
    const platform = safeText(item.item_key, 2048).split(":", 1)[0] || "unknown";
    const group = groups.get(platform) || [0, 0];
    group[1] += 1;
    if (["synced", "already_synced"].includes(item.status)) group[0] += 1;
    groups.set(platform, group);
  }
  return Array.from(groups, ([platform, [success, total]]) => (
    `${PLATFORM_NAMES[platform] || platform} ${success}/${total}`
  )).join(" · ");
}

function eligible(item) {
  return !["synced", "already_synced", "syncing"].includes(item.sync_status);
}

function createSavedView(cfg) {
  let $root = null;
  let items = [];
  let total = 0;
  let loading = false;
  let loaded = false;
  let syncingKeys = new Set();
  let message = "";
  let messageIsError = false;
  let pendingFocus = null;
  const retained = createRetainedSavedListState();
  const activeTaskIds = new Set();
  const taskTracker = createDurableTaskTracker({
    poll: (taskId) => pollSavedSyncTask(safeText(taskId, 64)),
  });

  function renderShell(bodyHtml) {
    const pending = items.filter((item) => eligible(item) && !syncingKeys.has(item.item_key)).length;
    $root.innerHTML = `
      <div class="saved-view">
        <div class="saved-head">
          <span class="saved-head-icon" aria-hidden="true">${cfg.icon}</span>
          <span class="saved-head-title">${esc(cfg.title)}</span>
          <span class="saved-head-count" id="${cfg.countId}">${total > 0 ? total : ""}</span>
        </div>
        <div class="saved-sync-toolbar">
          <button class="btn btn-outline saved-sync-all" type="button" ${pending === 0 ? "disabled" : ""}>同步未同步内容（${pending}）</button>
          <span class="saved-sync-message" aria-live="polite" ${messageIsError ? 'role="alert"' : ""}>${esc(message)}</span>
          ${retained.snapshot().error ? '<button class="btn btn-outline saved-load-retry" type="button">重试加载</button>' : ""}
        </div>
        <div class="saved-body">${bodyHtml}</div>
      </div>`;
    $root.querySelector(".saved-sync-all")?.addEventListener("click", (event) => {
      pendingFocus = { itemKey: "__list__", action: "sync-all" };
      void runSync(items.filter((item) => eligible(item) && !syncingKeys.has(item.item_key)), event.currentTarget, true);
    });
    $root.querySelector(".saved-load-retry")?.addEventListener("click", () => { void load(); });
  }

  async function runSync(selected, activeButton, confirmBatch = false) {
    if (!selected.length || activeButton.disabled) return;
    const platforms = Array.from(new Set(selected.map((item) => (
      PLATFORM_NAMES[item.source_platform] || item.source_platform
    ))));
    if (confirmBatch && !window.confirm(
      `将同步 ${selected.length} 项到 ${platforms.join("、")}，继续吗？`,
    )) return;
    for (const item of selected) syncingKeys.add(item.item_key);
    activeButton.disabled = true;
    activeButton.textContent = "同步中…";
    message = `正在同步 ${selected.length} 项…`;
    messageIsError = false;
    try {
      const task = await syncSavedItems(cfg.listKind, selected.map((item) => item.item_key));
      const taskId = safeText(task?.task_id, 64);
      if (!taskId) throw new Error("同步任务缺少 task_id，请重试。");
      activeTaskIds.add(taskId);
      taskTracker.track(task, {
        onProgress: () => {
          message = `正在同步 ${selected.length} 项…`;
          messageIsError = false;
          renderList();
        },
        onBackground: () => {
          message = "仍在后台同步；可离开此页，返回后会继续更新。";
          messageIsError = false;
          renderList();
        },
        onPollError: () => {
          message = "仍在后台同步；连接恢复后会继续查询。";
          messageIsError = false;
          renderList();
        },
        onTerminal: (terminalTask) => {
          activeTaskIds.delete(taskId);
          for (const item of selected) syncingKeys.delete(item.item_key);
          message = summarize(terminalTask.items) || "同步已完成";
          messageIsError = false;
          void load();
        },
      });
      message = `同步任务已提交 · ${selected.length} 项`;
      await load();
    } catch (error) {
      for (const item of selected) syncingKeys.delete(item.item_key);
      message = error?.message || "同步失败，请稍后重试。";
      messageIsError = true;
    } finally {
      activeButton.disabled = false;
    }
  }

  function renderList() {
    const focusToken = captureSavedFocus($root) || pendingFocus;
    if (loading && !loaded) {
      renderShell(`<div style="padding:40px"><div class="spinner"></div></div>`);
      return;
    }
    if (!items.length) {
      renderShell(`<div class="saved-empty"><div class="saved-empty-icon">${cfg.icon}</div><div class="saved-empty-text">${esc(cfg.emptyText)}</div></div>`);
      return;
    }
    const cards = items.map((raw) => {
      const it = getSavedSyncViewModel(
        syncingKeys.has(raw.item_key) ? { ...raw, sync_status: "syncing" } : raw,
      );
      const cover = getCoverImageAttrs(it.cover_url);
      const url = buildContentUrl(it);
      const coverHtml = cover
        ? `<img class="saved-card-cover" src="${esc(cover.src)}" alt="" loading="lazy">`
        : `<div class="saved-card-cover saved-card-cover-empty" aria-hidden="true">${cfg.icon}</div>`;
      const syncLabel = it.retryable ? "重试同步" : "同步";
      return `<article class="saved-card" data-item-key="${esc(it.item_key)}">
        <button class="saved-card-open" data-saved-action="open" type="button" ${url ? `data-url="${esc(url)}"` : "disabled"} aria-label="打开 ${esc(it.title || it.content_id)}">${coverHtml}</button>
        <div class="saved-card-body">
          <div class="saved-card-title">${esc(it.title || it.content_id)}</div>
          <div class="saved-card-up">${esc(it.author_name)}</div>
          <div class="saved-sync-line"><span class="saved-sync-chip" data-tone="${esc(it.tone)}">${esc(it.label)}</span><span>${esc(it.detail)}</span></div>
        </div>
        <div class="saved-card-actions">
          ${eligible(it) && !syncingKeys.has(it.item_key) ? `<button class="saved-card-sync" data-saved-action="sync" type="button">${syncLabel}</button>` : ""}
          <button class="saved-card-remove" data-saved-action="remove" type="button" aria-label="从本地移除" title="只从 OpenBiliClaw 本地移除">×</button>
        </div>
      </article>`;
    }).join("");
    renderShell(`<div class="saved-list">${cards}</div>`);

    for (const card of $root.querySelectorAll(".saved-card")) {
      const item = items.find((row) => row.item_key === card.dataset.itemKey);
      const open = card.querySelector(".saved-card-open");
      open?.addEventListener("click", () => {
        if (open.dataset.url) openContentUrl(open.dataset.url);
      });
      card.querySelector(".saved-card-sync")?.addEventListener("click", (event) => {
        pendingFocus = { itemKey: item.item_key, action: "sync" };
        void runSync([item], event.currentTarget);
      });
      const remove = card.querySelector(".saved-card-remove");
      remove.addEventListener("click", async () => {
        pendingFocus = { itemKey: item.item_key, action: "remove" };
        remove.disabled = true;
        try {
          await removeSavedItem(cfg.listKind, item.item_key);
          await load();
        } catch (error) {
          remove.disabled = false;
          message = error?.message || "本地移除失败，请重试。";
          messageIsError = true;
          renderList();
        }
      });
    }
    if (restoreSavedFocus($root, focusToken)) pendingFocus = null;
  }

  async function load() {
    loading = true;
    renderList();
    const hadLoadError = Boolean(retained.snapshot().error);
    try {
      const data = await fetchSavedItems(cfg.listKind, PAGE_SIZE, 0);
      retained.commit({
        items: (Array.isArray(data?.items) ? data.items : []).map(normalizeSavedListItem),
        total: Number(data?.total) || (Array.isArray(data?.items) ? data.items.length : 0),
      });
      ({ items, total, loaded } = retained.snapshot());
      if (hadLoadError) message = "";
      messageIsError = false;
    } catch (error) {
      retained.fail(error);
      ({ items, total, loaded } = retained.snapshot());
      message = retained.snapshot().error;
      messageIsError = true;
    } finally {
      loading = false;
      renderList();
    }
  }

  return function init(rootEl) {
    $root = rootEl;
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) for (const taskId of activeTaskIds) taskTracker.resume(taskId);
    });
    void load();
  };
}

const CLOCK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
const STAR_SVG = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>';

export const initWatchLaterView = createSavedView({
  listKind: "watch_later", icon: CLOCK_SVG, title: "稍后再看",
  emptyText: "还没有稍后再看的内容，去推荐里点时钟图标加入吧。",
  countId: "watchLaterViewCount",
});

export const initFavoritesView = createSavedView({
  listKind: "favorite", icon: STAR_SVG, title: "我的收藏",
  emptyText: "还没有收藏的内容，去推荐里点星标收藏吧。",
  countId: "favoritesViewCount",
});
