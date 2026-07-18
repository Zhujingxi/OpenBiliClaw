import {
  request,
  readSse,
  escapeHtml,
  errorMessage,
  newConversationId,
  recordInteraction,
  saveContentToLibrary,
  safeWebUrl,
} from "./vnext-api.js";

const $app = document.getElementById("app"),
  $status = document.getElementById("status-bar"),
  $tabs = document.getElementById("tab-bar");
const tabs = [
  { id: "recommend", icon: "✨", label: "推荐" },
  { id: "watchLater", icon: "🕐", label: "稍后" },
  { id: "favorites", icon: "⭐", label: "收藏" },
  { id: "profile", icon: "🧠", label: "画像" },
  { id: "chat", icon: "💬", label: "对话" },
];
const state = { page: "recommend", feed: [], profile: null, settings: null };

function ensureViews() {
  for (const tab of tabs) {
    if (document.getElementById(`view-${tab.id}`)) continue;
    const view = document.createElement("section");
    view.id = `view-${tab.id}`;
    view.className = "view";
    $app.appendChild(view);
  }
}

function showView(id, markup) {
  ensureViews();
  const active = document.getElementById(`view-${id}`);
  if (markup !== undefined) active.innerHTML = markup;
  $app.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view === active);
  });
  return active;
}

function shell() {
  $status.innerHTML =
    '<span class="status-title">OpenBiliClaw</span><div class="status-right"><span class="status-dot online"></span><span id="mobileStatus" class="muted">/api/v1</span><button id="mobileSettings" class="badge-btn" aria-label="设置">⚙</button></div>';
  $tabs.innerHTML = tabs
    .map(
      (tab) =>
        `<button class="tab-item${state.page === tab.id ? " active" : ""}" data-tab="${tab.id}" role="tab" aria-selected="${state.page === tab.id}"><span class="tab-icon">${tab.icon}</span><span class="tab-label">${tab.label}</span></button>`,
    )
    .join("");
  $tabs.setAttribute("role", "tablist");
  $tabs.querySelectorAll("[data-tab]").forEach((button, index) => {
    button.tabIndex = button.dataset.tab === state.page ? 0 : -1;
    button.addEventListener("click", () => navigate(button.dataset.tab));
    button.addEventListener("keydown", (e) => {
      let next = null;
      if (e.key === "ArrowRight") next = tabs[(index + 1) % tabs.length];
      if (e.key === "ArrowLeft")
        next = tabs[(index - 1 + tabs.length) % tabs.length];
      if (!next) return;
      e.preventDefault();
      navigate(next.id);
      $tabs.querySelector('[aria-selected="true"]')?.focus();
    });
  });
  document
    .getElementById("mobileSettings")
    .addEventListener("click", () => void renderSettings());
}

function navigate(page) {
  state.page = tabs.some((tab) => tab.id === page) ? page : "recommend";
  location.hash = `#/${state.page}`;
  shell();
  showView(state.page);
  if (state.page === "recommend") renderFeed();
  else if (state.page === "watchLater" || state.page === "favorites")
    void renderLibrary(
      state.page === "watchLater" ? "watch_later" : "favorites",
    );
  else if (state.page === "profile") void renderProfile();
  else void renderChat();
}
function empty(text) {
  showView(
    state.page,
    `<div class="empty-state"><p>${escapeHtml(text)}</p></div>`,
  );
}
function imageOf(content) {
  return (
    content?.metadata?.thumbnail ||
    content?.metadata?.cover ||
    content?.metadata?.image ||
    ""
  );
}
function interaction(content_id, kind) {
  return recordInteraction(content_id, kind, "mobile_web");
}
async function save(collection, content_id, button) {
  button.disabled = true;
  try {
    const result = await saveContentToLibrary(
      collection,
      content_id,
      "mobile_web",
      { libraryPersisted: button.dataset.libraryPersisted === "true" },
    );
    button.dataset.libraryPersisted = "true";
    button.setAttribute("aria-pressed", "true");
    button.classList.add("active");
    if (result.interactionPending) {
      button.dataset.interactionPending = "true";
      button.textContent = "已保存 · 重试";
    } else {
      delete button.dataset.interactionPending;
      button.textContent = "已保存";
    }
  } catch (error) {
    button.textContent = "重试";
  } finally {
    button.disabled =
      button.dataset.libraryPersisted === "true" &&
      button.dataset.interactionPending !== "true";
  }
}
function card(content, entry, collection = "") {
  const el = document.createElement("article");
  el.className = "card rec-card";
  const image = imageOf(content);
  el.innerHTML = `<a class="card-open" href="${escapeHtml(content.url)}" target="_blank" rel="noreferrer" data-open><div class="card-cover-frame rec-thumb">${image ? `<img class="card-cover" src="${escapeHtml(image)}" alt="" loading="lazy">` : ""}</div><div class="card-body rec-body"><h3 class="card-title">${escapeHtml(content.title)}</h3><p class="card-meta rec-meta"><span class="card-source" data-source="${escapeHtml(content.source_id)}">${escapeHtml(content.source_id)}</span>${content.creator ? ` · ${escapeHtml(content.creator)}` : ""}</p>${entry?.explanation ? `<p class="card-expression rec-reason">${escapeHtml(entry.explanation)}</p>` : ""}</div></a><div class="card-actions rec-actions">${collection ? `<button data-remove class="card-action-btn btn btn-outline">移除</button>` : `<button data-kind="positive" class="card-action-btn btn btn-outline">喜欢</button><button data-kind="negative" class="card-action-btn btn btn-outline">不感兴趣</button><button data-save="watch_later" class="card-action-btn btn btn-outline">稍后</button><button data-save="favorites" class="card-action-btn btn btn-outline">收藏</button>`}</div>`;
  el.querySelector("[data-open]").addEventListener(
    "click",
    () => void interaction(content.id, "open").catch(() => undefined),
  );
  el.querySelectorAll("[data-kind]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await interaction(content.id, button.dataset.kind);
        button.classList.add("active");
      } catch {
        button.textContent = "重试";
      } finally {
        button.disabled = false;
      }
    }),
  );
  el.querySelectorAll("[data-save]").forEach((button) =>
    button.addEventListener(
      "click",
      () => void save(button.dataset.save, content.id, button),
    ),
  );
  el.querySelector("[data-remove]")?.addEventListener("click", async () => {
    await request("v1_library_remove", {
      path: { collection, content_id: content.id },
    });
    el.remove();
  });
  return el;
}
function renderFeed() {
  showView(
    "recommend",
    '<section class="recommend-header-card"><div class="recommend-header-top"><div class="recommend-header-copy"><p class="recommend-kicker">Discovery feed</p><h1 class="recommend-title">为你推荐</h1><p>基于证据画像、来源多样性和新鲜度生成。</p></div><button id="mobileReplenish" class="btn btn-outline recommend-refresh-btn">补齐发现流</button></div></section><div id="mobileFeed" class="rec-list"></div>',
  );
  const host = document.getElementById("mobileFeed");
  if (!state.feed.length)
    host.innerHTML =
      '<div class="empty-state"><p>发现流还是空的，点“补齐”从已连接来源收集内容。</p></div>';
  state.feed.forEach(({ content, entry }) =>
    host.appendChild(card(content, entry)),
  );
  document
    .getElementById("mobileReplenish")
    .addEventListener("click", async () => {
      const button = document.getElementById("mobileReplenish");
      button.disabled = true;
      try {
        const run = await request("v1_jobs_schedule", {
          body: {
            job_name: "feed_replenishment",
            idempotency_key: `mobile-feed-${Date.now()}`,
            priority: "user-triggered",
          },
        });
        button.textContent = "已提交";
        await readSse(
          "v1_jobs_events",
          { path: { run_id: run.id } },
          async ({ event, data }) => {
            if (event === "done") {
              if (data.status === "failed" || data.status === "cancelled") {
                button.textContent =
                  data.status === "cancelled" ? "已取消" : "任务失败";
                button.disabled = false;
                return;
              }
              await loadFeed();
              button.textContent = "已完成";
            }
          },
        );
      } catch {
        button.textContent = "重试";
        button.disabled = false;
      }
    });
}
async function loadFeed() {
  try {
    state.feed = await request("v1_feed_list", {
      query: { limit: 50, offset: 0 },
    });
    renderFeed();
  } catch (error) {
    empty(errorMessage(error));
  }
}
async function renderLibrary(collection) {
  empty("正在读取本地列表…");
  try {
    const items = await request("v1_library_list", { path: { collection } });
    showView(
      collection === "favorites" ? "favorites" : "watchLater",
      `<div class="saved-view"><div class="saved-head"><span class="saved-head-icon" aria-hidden="true">${collection === "favorites" ? "☆" : "◷"}</span><h1 class="saved-head-title">${collection === "favorites" ? "我的收藏" : "稍后再看"}</h1><span class="saved-head-count">${items.length || ""}</span></div><div class="saved-body"><div id="mobileLibrary" class="rec-list"></div></div></div>`,
    );
    const host = document.getElementById("mobileLibrary");
    if (!items.length)
      host.innerHTML = '<div class="empty-state"><p>这里还没有内容。</p></div>';
    items.forEach(({ content }) =>
      host.appendChild(card(content, null, collection)),
    );
  } catch (error) {
    empty(errorMessage(error));
  }
}
async function renderProfile() {
  empty("正在读取证据画像…");
  try {
    try {
      state.profile = await request("v1_profile_get");
    } catch (error) {
      if (error?.status !== 404) throw error;
      state.profile = {
        revision: null,
        narrative: "",
        facets: [],
        confidence: 0,
      };
    }
    const p = state.profile;
    const revisionLabel = p.revision === null ? "尚未创建" : `版本 ${p.revision}`;
    showView(
      "profile",
      `<div class="profile-section"><div class="profile-section-title">证据画像</div><div class="profile-portrait">${revisionLabel} · 置信度 ${Math.round((p.confidence || 0) * 100)}%</div></div><form id="mobileProfile"><div class="profile-section"><label class="profile-section-title" for="mobileNarrative">画像叙述</label><textarea id="mobileNarrative" class="edit-text-input" rows="6" placeholder="画像叙述">${escapeHtml(p.narrative || "")}</textarea></div><div class="profile-section"><div class="profile-section-title">证据维度</div><div class="chip-list">${(p.facets || []).map((f) => `<span class="chip">${escapeHtml(f.name)} · ${escapeHtml(f.value)}</span>`).join("")}</div></div><button class="btn btn-brand">保存叙述</button></form>`,
    );
    document
      .getElementById("mobileProfile")
      .addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          state.profile = await request("v1_profile_edit", {
            body: {
              expected_revision: p.revision,
              narrative: document.getElementById("mobileNarrative").value,
              upserts: [],
              removals: [],
            },
          });
          void renderProfile();
        } catch (error) {
          alert(errorMessage(error));
        }
      });
  } catch (error) {
    empty(errorMessage(error));
  }
}
async function renderChat() {
  showView(
    "chat",
    '<section class="chat-shell"><div class="view-header"><div><p class="eyebrow">Taste dialogue</p><h1>聊聊你的口味</h1></div></div><div id="mobileChatLog" class="chat-messages"></div><form id="mobileChatForm" class="chat-input-row"><textarea id="mobileChatInput" class="chat-input" maxlength="20000" required placeholder="说说你最近喜欢或不喜欢的内容"></textarea><label><input id="mobileChatLearn" type="checkbox"> 学习本轮</label><button class="chat-send-btn" aria-label="发送">发送</button></form></section>',
  );
  const id = newConversationId();
  const log = document.getElementById("mobileChatLog");
  try {
    const history = await request("v1_chat_history", {
      path: { conversation_id: id },
      query: { limit: 100, offset: 0 },
    });
    (history.items || []).forEach((turn) =>
      addTurn(log, turn.role, turn.content),
    );
  } catch {
    /* a new conversation has no history */
  }
  document
    .getElementById("mobileChatForm")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = document.getElementById("mobileChatInput"),
        message = input.value.trim();
      if (!message) return;
      input.value = "";
      addTurn(log, "user", message);
      const answer = addTurn(log, "assistant", "");
      try {
        await readSse(
          "v1_chat_stream",
          {
            body: {
              conversation_id: id,
              message,
              learn: document.getElementById("mobileChatLearn").checked,
            },
          },
          ({ event, data }) => {
            if (event === "delta") answer.textContent += data.content || "";
            if (event === "error")
              answer.textContent = data.message || "对话失败";
            answer.scrollIntoView({ block: "end" });
          },
        );
      } catch (error) {
        answer.textContent = errorMessage(error);
      }
    });
}
function addTurn(log, role, content) {
  const el = document.createElement("div");
  el.className = `chat-bubble ${role}`;
  el.textContent = content;
  log.appendChild(el);
  return el;
}
async function renderSettings() {
  try {
    const [health, settings] = await Promise.all([
      request("v1_system_ai_health"),
      request("v1_settings_get"),
    ]);
    state.settings = settings;
    const modal = document.createElement("section");
    modal.className = "settings-sheet open";
    const adminUrl = safeWebUrl(health.admin_url);
    modal.innerHTML = `<div class="settings-sheet-panel"><div class="settings-head"><h2>设置</h2><button id="closeMobileSettings" class="badge-btn">×</button></div><h3>AI 别名</h3>${(health.aliases || []).map((item) => `<p><strong>${escapeHtml(item.alias)}</strong> · ${escapeHtml(item.state)}</p>`).join("")}${adminUrl ? `<a class="btn btn-brand" href="${escapeHtml(adminUrl)}" target="_blank" rel="noreferrer">LiteLLM Admin ↗</a>` : ""}<form id="mobileSettingsForm"><h3>常用产品设置</h3><label>发现流低水位<input id="mLow" type="number" min="0" max="1000" value="${settings.feed.low_watermark}"></label><label>发现流高水位<input id="mHigh" type="number" min="1" max="2000" value="${settings.feed.high_watermark}"></label><label>最低推荐分<input id="mScore" type="number" min="0" max="1" step="0.01" value="${settings.feed.min_score}"></label><label>来源同步间隔（分钟）<input id="mSync" type="number" min="1" max="10080" value="${settings.schedules.source_sync_interval_minutes}"></label><label>画像投影间隔（分钟）<input id="mProfileSchedule" type="number" min="1" max="10080" value="${settings.schedules.profile_projection_interval_minutes}"></label><label>发现流补充间隔（分钟）<input id="mFeedSchedule" type="number" min="1" max="10080" value="${settings.schedules.feed_replenishment_interval_minutes}"></label><label>清理间隔（分钟）<input id="mCleanupSchedule" type="number" min="1" max="10080" value="${settings.schedules.cleanup_interval_minutes}"></label><label>网络模式<select id="mNetwork"><option value="direct">直接连接</option><option value="system">系统代理</option><option value="custom">自定义代理</option></select></label><label>代理 URL<input id="mProxy" value="${escapeHtml(settings.network.proxy_url)}"></label><label><input id="mExtension" type="checkbox" ${settings.access_control.extension_access_enabled ? "checked" : ""}> 允许浏览器扩展</label><p class="hint">完整的来源权重、任务限制、日志和访问控制设置可在桌面 Web 中调整。</p><button class="btn btn-brand">保存</button></form></div>`;
    document.body.appendChild(modal);
    document.getElementById("mNetwork").value = settings.network.mode;
    document
      .getElementById("closeMobileSettings")
      .addEventListener("click", () => modal.remove());
    document
      .getElementById("mobileSettingsForm")
      .addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await request("v1_settings_patch", {
            body: {
              feed: {
                low_watermark: Number(document.getElementById("mLow").value),
                high_watermark: Number(document.getElementById("mHigh").value),
                min_score: Number(document.getElementById("mScore").value),
              },
              schedules: {
                source_sync_interval_minutes: Number(
                  document.getElementById("mSync").value,
                ),
                profile_projection_interval_minutes: Number(
                  document.getElementById("mProfileSchedule").value,
                ),
                feed_replenishment_interval_minutes: Number(
                  document.getElementById("mFeedSchedule").value,
                ),
                cleanup_interval_minutes: Number(
                  document.getElementById("mCleanupSchedule").value,
                ),
              },
              network: {
                mode: document.getElementById("mNetwork").value,
                proxy_url:
                  document.getElementById("mNetwork").value === "custom"
                    ? document.getElementById("mProxy").value
                    : "",
              },
              access_control: {
                extension_access_enabled:
                  document.getElementById("mExtension").checked,
              },
            },
          });
          modal.remove();
        } catch (error) {
          alert(errorMessage(error));
        }
      });
  } catch (error) {
    alert(errorMessage(error));
  }
}
function showLogin() {
  $status.innerHTML = "";
  $tabs.innerHTML = "";
  $app.innerHTML =
    '<section class="view active login-view"><form id="mobileLogin" class="login-card"><h1>登录 OpenBiliClaw</h1><input id="mobilePassword" type="password" autocomplete="current-password" required placeholder="访问密码"><button class="btn btn-brand">登录</button><p id="mobileLoginError"></p></form></section>';
  document
    .getElementById("mobileLogin")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await request("v1_auth_login", {
          body: { password: document.getElementById("mobilePassword").value },
        });
        await boot();
      } catch (error) {
        document.getElementById("mobileLoginError").textContent =
          errorMessage(error);
      }
    });
}
async function boot() {
  try {
    const auth = await request("v1_auth_status");
    if (auth.enabled && !auth.authenticated) {
      showLogin();
      return;
    }
    shell();
    const ready = await request("v1_system_readiness");
    document.getElementById("mobileStatus").textContent = ready.ready
      ? "在线"
      : "启动中";
    state.page = location.hash.replace("#/", "") || "recommend";
    await loadFeed();
    navigate(state.page);
  } catch (error) {
    shell();
    empty(errorMessage(error));
    document.getElementById("mobileStatus").textContent = "离线";
  }
}
window.addEventListener("hashchange", () =>
  navigate(location.hash.replace("#/", "")),
);
window.addEventListener("obc:auth-required", showLogin);
boot();
