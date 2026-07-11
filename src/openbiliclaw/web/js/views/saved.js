/**
 * Saved-list view — a generic browsable list for the two "kept content"
 * surfaces: 稍后再看 (watch-later) and 收藏 (favorites). Both share the
 * same card layout and remove flow; only the data source, copy, and icon
 * differ. Instantiated twice via initWatchLaterView / initFavoritesView.
 */

import {
  fetchWatchLater,
  removeFromWatchLater,
  fetchFavorites,
  removeFromFavorite,
} from "../api.js";
import { getCoverImageAttrs, buildContentUrl } from "../view-models.js";
import { openContentUrl } from "../app-launch.js";

const PAGE_SIZE = 50;

function esc(s) {
  const el = document.createElement("span");
  el.textContent = s == null ? "" : String(s);
  return el.innerHTML;
}

/**
 * Build a saved-list view controller bound to a backend source.
 * @param {object} cfg
 * @param {string} cfg.icon      header glyph
 * @param {string} cfg.title     header title
 * @param {string} cfg.emptyText empty-state copy
 * @param {function} cfg.fetch   (limit, offset) => Promise<{items,total}>
 * @param {function} cfg.remove  (bvid) => Promise
 */
function createSavedView(cfg) {
  let $root = null;
  let items = [];
  let total = 0;
  let loading = false;
  let loaded = false;

  function renderShell(bodyHtml) {
    $root.innerHTML = `
      <div class="saved-view">
        <div class="saved-head">
          <span class="saved-head-icon" aria-hidden="true">${cfg.icon}</span>
          <span class="saved-head-title">${esc(cfg.title)}</span>
          <span class="saved-head-count" id="${cfg.countId}">${total > 0 ? total : ""}</span>
        </div>
        <div class="saved-body">${bodyHtml}</div>
      </div>`;
  }

  function renderList() {
    if (loading && !loaded) {
      renderShell(`<div style="padding:40px"><div class="spinner"></div></div>`);
      return;
    }
    if (!items.length) {
      renderShell(
        `<div class="saved-empty"><div class="saved-empty-icon">${cfg.icon}</div><div class="saved-empty-text">${esc(cfg.emptyText)}</div></div>`,
      );
      return;
    }
    const cards = items
      .map((it) => {
        const cover = getCoverImageAttrs(it.cover_url);
        const url = buildContentUrl(it);
        const coverHtml = cover
          ? `<img class="saved-card-cover" src="${esc(cover.src)}" alt="" loading="lazy">`
          : `<div class="saved-card-cover saved-card-cover-empty" aria-hidden="true">${cfg.icon}</div>`;
        return `
          <div class="saved-card" data-bvid="${esc(it.bvid)}" data-url="${esc(url)}">
            ${coverHtml}
            <div class="saved-card-body">
              <div class="saved-card-title">${esc(it.title || it.bvid)}</div>
              <div class="saved-card-up">${esc(it.up_name || "")}</div>
            </div>
            <button class="saved-card-remove" type="button" aria-label="移除" title="移除">×</button>
          </div>`;
      })
      .join("");
    renderShell(`<div class="saved-list">${cards}</div>`);

    for (const card of $root.querySelectorAll(".saved-card")) {
      const bvid = card.dataset.bvid;
      const url = card.dataset.url;
      card.addEventListener("click", (e) => {
        if (e.target.closest(".saved-card-remove")) return;
        if (url) openContentUrl(url);
      });
      const removeBtn = card.querySelector(".saved-card-remove");
      removeBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        removeBtn.disabled = true;
        try {
          await cfg.remove(bvid);
          items = items.filter((x) => x.bvid !== bvid);
          total = Math.max(0, total - 1);
          renderList();
        } catch {
          removeBtn.disabled = false;
        }
      });
    }
  }

  async function load() {
    loading = true;
    renderList();
    try {
      const data = await cfg.fetch(PAGE_SIZE, 0);
      items = Array.isArray(data?.items) ? data.items : [];
      total = Number(data?.total) || items.length;
      loaded = true;
    } catch {
      items = [];
      total = 0;
    } finally {
      loading = false;
      renderList();
    }
  }

  return function init(rootEl) {
    $root = rootEl;
    load();
  };
}

const CLOCK_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3.2 1.9"/></svg>';
const STAR_SVG =
  '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true"><path d="M12 3.6l2.65 5.37 5.93.86-4.29 4.18 1.01 5.9L12 17.1l-5.31 2.8 1.01-5.9L3.41 9.83l5.93-.86z"/></svg>';

export const initWatchLaterView = createSavedView({
  icon: CLOCK_SVG, // 时钟 = 稍后再看
  title: "稍后再看",
  emptyText: "还没有稍后再看的内容，去推荐里点时钟图标加入吧。",
  countId: "watchLaterViewCount",
  fetch: fetchWatchLater,
  remove: removeFromWatchLater,
});

export const initFavoritesView = createSavedView({
  icon: STAR_SVG, // 星星 = 收藏
  title: "我的收藏",
  emptyText: "还没有收藏的内容，去推荐里点星标收藏吧。",
  countId: "favoritesViewCount",
  fetch: fetchFavorites,
  remove: removeFromFavorite,
});
