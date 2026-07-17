import {
  request,
  readSse,
  escapeHtml,
  errorMessage,
  newConversationId,
  safeWebUrl,
} from "./vnext-api.js";

const $app = document.getElementById("app"),
  $status = document.getElementById("status-bar"),
  $tabs = document.getElementById("tab-bar");
const tabs = [
  { id: "feed", icon: "✨", label: "推荐" },
  { id: "watch_later", icon: "🕐", label: "稍后" },
  { id: "favorites", icon: "⭐", label: "收藏" },
  { id: "profile", icon: "🧠", label: "画像" },
  { id: "chat", icon: "💬", label: "对话" },
];
const state = { page: "feed", feed: [], profile: null, settings: null };

function shell() {
  $status.innerHTML =
    '<span class="status-title">OpenBiliClaw</span><div class="status-right"><span class="status-dot online"></span><span id="mobileStatus" class="muted">/api/v1</span><button id="mobileSettings" class="badge-btn" aria-label="设置">⚙</button></div>';
  $tabs.innerHTML = tabs
    .map(
      (tab) =>
        `<button class="tab-item${state.page === tab.id ? " active" : ""}" data-tab="${tab.id}" role="tab" aria-selected="${state.page === tab.id}"><span class="tab-icon">${tab.icon}</span><span class="tab-label">${tab.label}</span></button>`,
    )
    .join("");
  $tabs
    .querySelectorAll("[data-tab]")
    .forEach((button) =>
      button.addEventListener("click", () => navigate(button.dataset.tab)),
    );
  document
    .getElementById("mobileSettings")
    .addEventListener("click", () => void renderSettings());
}

function navigate(page) {
  state.page = tabs.some((tab) => tab.id === page) ? page : "feed";
  location.hash = `#/${state.page}`;
  shell();
  if (state.page === "feed") renderFeed();
  else if (state.page === "watch_later" || state.page === "favorites")
    void renderLibrary(state.page);
  else if (state.page === "profile") void renderProfile();
  else void renderChat();
}
function empty(text) {
  $app.innerHTML = `<section class="view active"><div class="empty-state"><p>${escapeHtml(text)}</p></div></section>`;
}
function imageOf(content) {
  return (
    content?.metadata?.thumbnail ||
    content?.metadata?.cover ||
    content?.metadata?.image ||
    ""
  );
}
async function interaction(content_id, kind) {
  try {
    await request("v1_interactions_create", {
      body: { content_id, kind, metadata: { surface: "mobile_web" } },
    });
  } catch {
    /* feedback remains best effort */
  }
}
async function save(collection, content_id, button) {
  try {
    await request("v1_library_add", {
      path: { collection },
      body: { content_id, note: "" },
    });
    await interaction(
      content_id,
      collection === "favorites" ? "save_favorite" : "save_watch_later",
    );
    button.classList.add("active");
    button.textContent = "已保存";
  } catch (error) {
    button.textContent = error.status === 409 ? "已保存" : "重试";
  }
}
function card(content, entry, collection = "") {
  const el = document.createElement("article");
  el.className = "rec-card";
  const image = imageOf(content);
  el.innerHTML = `<a href="${escapeHtml(content.url)}" target="_blank" rel="noreferrer" data-open><div class="rec-thumb">${image ? `<img src="${escapeHtml(image)}" alt="" loading="lazy">` : ""}</div><div class="rec-body"><h3>${escapeHtml(content.title)}</h3><p class="rec-meta">${escapeHtml(content.source_id)}${content.creator ? ` · ${escapeHtml(content.creator)}` : ""}</p>${entry?.explanation ? `<p class="rec-reason">${escapeHtml(entry.explanation)}</p>` : ""}</div></a><div class="rec-actions">${collection ? `<button data-remove class="btn btn-ghost">移除</button>` : `<button data-kind="positive" class="btn btn-ghost">喜欢</button><button data-kind="negative" class="btn btn-ghost">不感兴趣</button><button data-save="watch_later" class="btn btn-ghost">稍后</button><button data-save="favorites" class="btn btn-ghost">收藏</button>`}</div>`;
  el.querySelector("[data-open]").addEventListener(
    "click",
    () => void interaction(content.id, "open"),
  );
  el.querySelectorAll("[data-kind]").forEach((button) =>
    button.addEventListener("click", () => {
      void interaction(content.id, button.dataset.kind);
      button.classList.add("active");
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
  $app.innerHTML =
    '<section class="view active"><div class="view-header"><div><p class="eyebrow">Discovery feed</p><h1>为你推荐</h1></div><button id="mobileReplenish" class="btn btn-brand">补齐</button></div><div id="mobileFeed" class="rec-list"></div></section>';
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
          async ({ event }) => {
            if (event === "done") {
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
    $app.innerHTML = `<section class="view active"><div class="view-header"><div><p class="eyebrow">${collection === "favorites" ? "Favorites" : "Watch later"}</p><h1>${collection === "favorites" ? "我的收藏" : "稍后再看"}</h1></div></div><div id="mobileLibrary" class="rec-list"></div></section>`;
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
    state.profile = await request("v1_profile_get");
    const p = state.profile;
    $app.innerHTML = `<section class="view active"><div class="view-header"><div><p class="eyebrow">Evidence profile</p><h1>我的画像</h1><p>版本 ${p.revision} · 置信度 ${Math.round((p.confidence || 0) * 100)}%</p></div></div><form id="mobileProfile"><textarea id="mobileNarrative" rows="6" placeholder="画像叙述">${escapeHtml(p.narrative || "")}</textarea><div class="chip-list">${(p.facets || []).map((f) => `<span class="chip">${escapeHtml(f.name)} · ${escapeHtml(f.value)}</span>`).join("")}</div><button class="btn btn-brand">保存叙述</button></form></section>`;
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
  $app.innerHTML =
    '<section class="view active"><div class="view-header"><div><p class="eyebrow">Taste dialogue</p><h1>聊聊你的口味</h1></div></div><div id="mobileChatLog" class="chat-messages"></div><form id="mobileChatForm" class="chat-composer"><textarea id="mobileChatInput" maxlength="20000" required placeholder="说说你最近喜欢或不喜欢的内容"></textarea><label><input id="mobileChatLearn" type="checkbox"> 学习本轮</label><button class="btn btn-brand">发送</button></form></section>';
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
    modal.innerHTML = `<div class="settings-sheet-panel"><div class="settings-head"><h2>设置</h2><button id="closeMobileSettings" class="badge-btn">×</button></div><h3>AI 别名</h3>${(health.aliases || []).map((item) => `<p><strong>${escapeHtml(item.alias)}</strong> · ${escapeHtml(item.state)}</p>`).join("")}${adminUrl ? `<a class="btn btn-brand" href="${escapeHtml(adminUrl)}" target="_blank" rel="noreferrer">LiteLLM Admin ↗</a>` : ""}<form id="mobileSettingsForm"><h3>常用产品设置</h3><label>发现流低水位<input id="mLow" type="number" min="0" max="1000" value="${settings.feed.low_watermark}"></label><label>发现流高水位<input id="mHigh" type="number" min="1" max="2000" value="${settings.feed.high_watermark}"></label><label>最低推荐分<input id="mScore" type="number" min="0" max="1" step="0.01" value="${settings.feed.min_score}"></label><label>来源同步间隔（分钟）<input id="mSync" type="number" min="1" max="10080" value="${settings.schedules.source_sync_interval_minutes}"></label><label>网络模式<select id="mNetwork"><option value="direct">直接连接</option><option value="system">系统代理</option><option value="custom">自定义代理</option></select></label><label>代理 URL<input id="mProxy" value="${escapeHtml(settings.network.proxy_url)}"></label><label><input id="mExtension" type="checkbox" ${settings.access_control.extension_access_enabled ? "checked" : ""}> 允许浏览器扩展</label><p class="hint">完整的来源权重、任务限制、日志和访问控制设置可在桌面 Web 中调整。</p><button class="btn btn-brand">保存</button></form></div>`;
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
              },
              network: {
                mode: document.getElementById("mNetwork").value,
                proxy_url: document.getElementById("mProxy").value,
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
    state.page = location.hash.replace("#/", "") || "feed";
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
