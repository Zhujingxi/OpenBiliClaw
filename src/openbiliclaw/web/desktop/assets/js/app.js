import {
  request,
  readSse,
  escapeHtml,
  errorMessage,
  newConversationId,
  recordInteraction,
  saveContentToLibrary,
  safeWebUrl,
} from "/m/js/vnext-api.js";

const SOURCES = [
  "bilibili",
  "xiaohongshu",
  "douyin",
  "youtube",
  "twitter",
  "zhihu",
  "reddit",
];
const TASKS = [
  "profile_delta",
  "keyword_generation",
  "candidate_assessment",
  "candidate_batch_assessment",
  "chat_response",
  "recommendation_explanation",
];
const SOURCE_LABELS = {
  bilibili: "Bilibili",
  xiaohongshu: "小红书",
  douyin: "抖音",
  youtube: "YouTube",
  twitter: "X",
  zhihu: "知乎",
  reddit: "Reddit",
};
const state = {
  feed: [],
  offset: 0,
  query: "",
  sourceFilter: "",
  profile: null,
  settings: null,
  manifests: [],
  accounts: [],
  moduleSettings: {},
  pendingFacets: [],
  removals: [],
};
const $ = (selector) => document.querySelector(selector);
const THEME_ORDER = ["auto", "light", "dark"];

function applyTheme(theme) {
  const normalized = THEME_ORDER.includes(theme) ? theme : "auto";
  if (normalized === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = normalized;
  const labels = { auto: "跟随系统", light: "浅色", dark: "深色" };
  const glyphs = { auto: "◐", light: "☀", dark: "☾" };
  $("#themeToggleBtn").title = `主题：${labels[normalized]}`;
  $("#themeToggleBtn").setAttribute("aria-label", `主题：${labels[normalized]}`);
  $("#themeToggleGlyph").textContent = glyphs[normalized];
  return normalized;
}

function setMobileMenu(open) {
  $("#mobileMenu").classList.toggle("is-open", open);
  $("#mobileMenu").setAttribute("aria-hidden", String(!open));
  document.body.classList.toggle("mobile-menu-open", open);
}

function selectSettingsTab(name) {
  document.querySelectorAll("[data-settings-tab]").forEach((button) => {
    const active = button.dataset.settingsTab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document
    .querySelectorAll("#settingsForm [data-settings-panel]")
    .forEach((panel) => {
      panel.hidden = panel.dataset.settingsPanel !== name;
    });
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    element.hidden = true;
  }, 3200);
}

function navigate(page) {
  const allowed = new Set([
    "feed",
    "watch_later",
    "favorites",
    "profile",
    "chat",
    "jobs",
    "settings",
  ]);
  if (!allowed.has(page)) page = "feed";
  state.page = page;
  document.querySelectorAll("[data-page-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.pagePanel !== page;
  });
  document
    .querySelectorAll("[data-page]")
    .forEach((button) =>
      button.setAttribute(
        "aria-current",
        button.dataset.page === page ? "page" : "false",
      ),
    );
  history.replaceState(null, "", `#${page}`);
  setMobileMenu(false);
  if (page === "watch_later" || page === "favorites") void loadLibrary(page);
  if (page === "profile") void loadProfile();
  if (page === "chat") void loadChat();
  if (page === "jobs") void loadJobs();
  if (page === "settings") void loadSettings();
}

function contentImage(content) {
  return (
    content?.metadata?.thumbnail ||
    content?.metadata?.cover ||
    content?.metadata?.image ||
    ""
  );
}

function contentCard(content, explanation = "", collection = "") {
  const article = document.createElement("article");
  article.className = "video-card";
  article.dataset.contentId = content.id;
  const image = contentImage(content);
  article.innerHTML = `<a href="${escapeHtml(content.url)}" target="_blank" rel="noreferrer" data-open><div class="vnext-cover">${image ? `<img src="${escapeHtml(image)}" alt="" loading="lazy">` : ""}</div><h3 class="video-title">${escapeHtml(content.title)}</h3></a><p class="video-meta">${escapeHtml(SOURCE_LABELS[content.source_id] || content.source_id)}${content.creator ? ` · ${escapeHtml(content.creator)}` : ""}</p>${content.summary ? `<p>${escapeHtml(content.summary)}</p>` : ""}${explanation ? `<p class="reason">${escapeHtml(explanation)}</p>` : ""}<div class="vnext-card-actions">${collection ? `<button class="small-btn" data-remove="${escapeHtml(collection)}">移除</button>` : `<button class="small-btn" data-feedback="positive">喜欢</button><button class="small-btn" data-feedback="negative">不感兴趣</button><button class="small-btn" data-save="watch_later">稍后再看</button><button class="small-btn" data-save="favorites">收藏</button>`}</div>`;
  article
    .querySelector("[data-open]")
    ?.addEventListener("click", () => {
      void interact(content.id, "open").catch(() => undefined);
    });
  article.querySelectorAll("[data-feedback]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await interact(content.id, button.dataset.feedback);
        button.setAttribute("aria-pressed", "true");
        toast("反馈已记录，会影响之后的排序");
      } catch (error) {
        toast(errorMessage(error));
      } finally {
        button.disabled = false;
      }
    }),
  );
  article.querySelectorAll("[data-save]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        const result = await saveItem(
          button.dataset.save,
          content.id,
          button.dataset.libraryPersisted === "true",
        );
        if (result.libraryPersisted) {
          button.setAttribute("aria-pressed", "true");
          button.dataset.libraryPersisted = "true";
        }
        if (result.interactionPending) {
          button.dataset.interactionPending = "true";
        } else {
          delete button.dataset.interactionPending;
        }
      } finally {
        button.disabled =
          button.dataset.libraryPersisted === "true" &&
          button.dataset.interactionPending !== "true";
      }
    }),
  );
  article
    .querySelector("[data-remove]")
    ?.addEventListener("click", async (event) => {
      await request("v1_library_remove", {
        path: {
          collection: event.currentTarget.dataset.remove,
          content_id: content.id,
        },
      });
      article.remove();
      toast("已从本地列表移除");
    });
  return article;
}

function interact(contentId, kind) {
  return recordInteraction(contentId, kind, "web");
}

async function saveItem(collection, contentId, libraryPersisted) {
  try {
    const result = await saveContentToLibrary(
      collection,
      contentId,
      "web",
      { libraryPersisted },
    );
    toast(
      result.interactionPending
        ? "已保存到本地列表；行为记录失败，点击可重试"
        : "已保存到本地列表",
    );
    return result;
  } catch (error) {
    toast(errorMessage(error));
    return { libraryPersisted: false, interactionPending: false };
  }
}

function renderFeed() {
  const grid = $("#videoGrid");
  grid.innerHTML = "";
  const query = state.query.toLowerCase();
  const sourceIds = [
    ...new Set(state.feed.map(({ content }) => content.source_id)),
  ];
  $("#filterRow").innerHTML = ["", ...sourceIds]
    .map(
      (source) =>
        `<button class="pill-btn" data-source-filter="${escapeHtml(source)}" aria-pressed="${source === state.sourceFilter}">${escapeHtml(source ? SOURCE_LABELS[source] || source : "全部来源")}</button>`,
    )
    .join("");
  document.querySelectorAll("[data-source-filter]").forEach((button) =>
    button.addEventListener("click", () => {
      state.sourceFilter = button.dataset.sourceFilter;
      renderFeed();
    }),
  );
  const items = state.feed.filter(({ content }) => {
    const matchesSource =
      !state.sourceFilter || content.source_id === state.sourceFilter;
    const matchesQuery =
      !query ||
      `${content.title} ${content.summary} ${content.creator}`
        .toLowerCase()
        .includes(query);
    return matchesSource && matchesQuery;
  });
  if (!items.length) {
    grid.innerHTML =
      '<div class="vnext-empty">发现流还是空的。运行“补齐发现流”，后台会按来源能力收集并排序内容。</div>';
    return;
  }
  items.forEach(({ content, entry }) =>
    grid.appendChild(contentCard(content, entry.explanation)),
  );
}

async function loadFeed({ append = false } = {}) {
  try {
    const offset = append ? state.offset : 0;
    const items = await request("v1_feed_list", {
      query: { limit: 24, offset },
    });
    state.feed = append ? [...state.feed, ...items] : items;
    state.offset = offset + items.length;
    renderFeed();
    $("#loadMoreBtn").hidden = items.length < 24;
  } catch (error) {
    $("#videoGrid").innerHTML =
      `<div class="vnext-empty">${escapeHtml(errorMessage(error))}</div>`;
  }
}

async function loadLibrary(collection) {
  const grid =
    collection === "favorites" ? $("#favoritesList") : $("#watchLaterList");
  grid.innerHTML = '<div class="vnext-empty">正在读取…</div>';
  try {
    const items = await request("v1_library_list", { path: { collection } });
    grid.innerHTML = "";
    if (!items.length)
      grid.innerHTML =
        '<div class="vnext-empty">这里还没有内容，去发现流保存一些吧。</div>';
    items.forEach(({ content }) =>
      grid.appendChild(contentCard(content, "", collection)),
    );
  } catch (error) {
    grid.innerHTML = `<div class="vnext-empty">${escapeHtml(errorMessage(error))}</div>`;
  }
}

function renderProfile() {
  const profile = state.profile || { revision: 0, narrative: "", facets: [] };
  $("#profileNarrative").value = profile.narrative || "";
  $("#profileMeta").textContent =
    `版本 ${profile.revision} · 置信度 ${Math.round((profile.confidence || 0) * 100)}%`;
  $("#profileSummary").textContent = `v${profile.revision}`;
  const host = $("#profileFacets");
  host.innerHTML = "";
  [
    ...profile.facets.map((facet) => ({ ...facet, persisted: true })),
    ...state.pendingFacets,
  ].forEach((facet) => {
    const row = document.createElement("div");
    row.className = "vnext-source-row";
    row.innerHTML = `<strong>${escapeHtml(facet.name)}</strong><span>${escapeHtml(facet.value)} · ${Number(facet.weight).toFixed(2)} · 置信度 ${Math.round((facet.confidence ?? 1) * 100)}%</span><button type="button" class="small-btn">移除</button>`;
    row.querySelector("button").addEventListener("click", () => {
      if (facet.persisted)
        state.removals.push({ name: facet.name, value: facet.value });
      else
        state.pendingFacets = state.pendingFacets.filter(
          (item) => item !== facet,
        );
      row.remove();
    });
    host.appendChild(row);
  });
}

async function loadProfile() {
  try {
    state.profile = await request("v1_profile_get");
    renderProfile();
  } catch (error) {
    $("#profileMeta").textContent = errorMessage(error);
  }
}

async function loadChat() {
  const conversationId = newConversationId();
  try {
    const page = await request("v1_chat_history", {
      path: { conversation_id: conversationId },
      query: { limit: 100, offset: 0 },
    });
    const log = $("#chatLog");
    log.innerHTML = "";
    (page.items || []).forEach(addChatTurn);
  } catch (error) {
    $("#chatLog").innerHTML =
      `<p class="vnext-empty">${escapeHtml(errorMessage(error))}</p>`;
  }
}

function addChatTurn(turn) {
  const el = document.createElement("div");
  el.className = `vnext-chat-turn ${turn.role || "assistant"}`;
  el.textContent = turn.content || "";
  $("#chatLog").appendChild(el);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
  return el;
}

async function loadJobs() {
  try {
    const jobs = await request("v1_jobs_list", { query: { limit: 50 } });
    const host = $("#jobList");
    host.innerHTML = "";
    jobs.forEach((job) => {
      const row = document.createElement("div");
      row.className = "vnext-job-row";
      row.innerHTML = `<strong>${escapeHtml(job.job_name)}</strong><span>${escapeHtml(job.status)} · ${Math.round(job.progress * 100)}%${job.error ? ` · ${escapeHtml(job.error)}` : ""}</span>${["pending", "running"].includes(job.status) ? '<button class="small-btn">取消</button>' : "<span></span>"}`;
      row.querySelector("button")?.addEventListener("click", async () => {
        await request("v1_jobs_cancel", { path: { run_id: job.id } });
        await loadJobs();
      });
      host.appendChild(row);
    });
  } catch (error) {
    $("#jobList").innerHTML =
      `<div class="vnext-empty">${escapeHtml(errorMessage(error))}</div>`;
  }
}

async function watchJob(run) {
  try {
    await readSse(
      "v1_jobs_events",
      { path: { run_id: run.id } },
      async ({ event, data }) => {
        if (state.page === "jobs") await loadJobs();
        if (event === "done") {
          if (data.status === "failed" || data.status === "cancelled") {
            toast(
              data.status === "cancelled"
                ? `${run.job_name} 已取消`
                : `${run.job_name} 失败`,
            );
            return;
          }
          toast(`${run.job_name} 已完成`);
          if (run.job_name === "feed_replenishment") await loadFeed();
        }
      },
    );
  } catch (error) {
    toast(errorMessage(error, "任务进度连接已断开，可在任务页查看最终状态"));
  }
}

const numberField = (id, label, value, options = {}) =>
  `<label>${escapeHtml(label)}<input id="${id}" type="number" value="${value ?? ""}" ${options.min !== undefined ? `min="${options.min}"` : ""} ${options.max !== undefined ? `max="${options.max}"` : ""} step="${options.step || 1}"></label>`;
const checkboxField = (id, label, value) =>
  `<label><span>${escapeHtml(label)}</span><input id="${id}" type="checkbox" ${value ? "checked" : ""}></label>`;
const valueOf = (id, type = "number") =>
  type === "boolean"
    ? $(`#${id}`).checked
    : type === "string"
      ? $(`#${id}`).value
      : Number($(`#${id}`).value);

function renderSettings() {
  const settings = state.settings;
  $("#sourceSettings").innerHTML = SOURCES.map(
    (source) =>
      `<div class="vnext-source-row"><strong>${escapeHtml(SOURCE_LABELS[source])}</strong><label><input id="source-enabled-${source}" type="checkbox" ${settings.sources.enabled[source] ? "checked" : ""}> 启用</label><label>权重 <input id="source-weight-${source}" type="number" min="0" max="100" step="0.1" value="${settings.sources.weights[source]}"></label></div>`,
  ).join("");
  $("#sourceModuleSettings").innerHTML = state.manifests
    .map((manifest) => {
      const properties = manifest.settings_schema?.properties || {};
      const current = state.moduleSettings[manifest.source_id] || {};
      const fields = Object.entries(properties)
        .map(([name, schema]) => {
          const choices =
            schema.enum || schema.anyOf?.find((item) => item.enum)?.enum;
          const value = current[name] ?? schema.default ?? "";
          if (choices)
            return `<label>${escapeHtml(schema.title || name)}<select data-source-setting="${escapeHtml(manifest.source_id)}" data-setting-name="${escapeHtml(name)}">${choices.map((choice) => `<option value="${escapeHtml(choice)}" ${choice === value ? "selected" : ""}>${escapeHtml(choice)}</option>`).join("")}</select></label>`;
          if (schema.type === "boolean")
            return checkboxField(
              `source-module-${manifest.source_id}-${name}`,
              schema.title || name,
              Boolean(value),
            ).replace(
              "<input",
              `<input data-source-setting="${escapeHtml(manifest.source_id)}" data-setting-name="${escapeHtml(name)}"`,
            );
          return `<label>${escapeHtml(schema.title || name)}<input data-source-setting="${escapeHtml(manifest.source_id)}" data-setting-name="${escapeHtml(name)}" value="${escapeHtml(value)}"></label>`;
        })
        .join("");
      return fields
        ? `<fieldset><legend>${escapeHtml(manifest.display_name)} 专属设置</legend><div class="vnext-fields">${fields}</div></fieldset>`
        : "";
    })
    .join("");
  $("#feedSettings").innerHTML =
    numberField("feed-low", "低水位", settings.feed.low_watermark, {
      min: 0,
      max: 1000,
    }) +
    numberField("feed-high", "高水位", settings.feed.high_watermark, {
      min: 1,
      max: 2000,
    }) +
    numberField(
      "feed-multiplier",
      "候选倍数",
      settings.feed.candidate_multiplier,
      { min: 1, max: 20 },
    ) +
    numberField(
      "feed-batch",
      "单批候选上限",
      settings.feed.max_batch_candidates,
      { min: 1, max: 100 },
    ) +
    numberField("feed-source", "单来源上限", settings.feed.max_per_source, {
      min: 1,
      max: 100,
    }) +
    numberField("feed-topic", "单主题上限", settings.feed.max_per_topic, {
      min: 1,
      max: 100,
    }) +
    numberField("feed-score", "最低分", settings.feed.min_score, {
      min: 0,
      max: 1,
      step: 0.01,
    }) +
    numberField("feed-novelty", "最低新颖度", settings.feed.min_novelty, {
      min: 0,
      max: 1,
      step: 0.01,
    });
  $("#profileSettings").innerHTML = numberField(
    "profile-confidence",
    "最低证据置信度",
    settings.profile.minimum_evidence_confidence,
    { min: 0, max: 1, step: 0.01 },
  );
  $("#taskSettings").innerHTML = TASKS.map((task) => {
    const config = settings.tasks[task];
    return `<fieldset><legend>${escapeHtml(task)}</legend><div class="vnext-fields"><label>模型别名<select id="task-alias-${task}"><option ${config.model_alias === "obc-analysis" ? "selected" : ""}>obc-analysis</option><option ${config.model_alias === "obc-interactive" ? "selected" : ""}>obc-interactive</option></select></label>${numberField(`task-retry-${task}`, "语义重试", config.semantic_retry_limit, { min: 0, max: 10 })}${numberField(`task-timeout-${task}`, "超时秒数", config.timeout_seconds, { min: 1, max: 600, step: 0.1 })}${numberField(`task-request-${task}`, "请求次数上限", config.request_limit, { min: 1, max: 20 })}${numberField(`task-token-${task}`, "总 Token 上限", config.total_tokens_limit, { min: 1, max: 1000000 })}</div></fieldset>`;
  }).join("");
  $("#scheduleSettings").innerHTML =
    numberField(
      "schedule-sync",
      "来源同步间隔（分钟）",
      settings.schedules.source_sync_interval_minutes,
      { min: 1, max: 10080 },
    ) +
    numberField(
      "job-retention",
      "任务记录保留天数",
      settings.jobs.retention_days,
      { min: 1, max: 3650 },
    ) +
    `<label>Worker 并发（部署项）<input value="${settings.jobs.worker_concurrency}" disabled></label>`;
  $("#runtimeSettings").innerHTML =
    `<label>网络模式<select id="network-mode"><option value="direct">直接连接</option><option value="system">系统代理</option><option value="custom">自定义代理</option></select></label><label>代理 URL<input id="network-proxy" value="${escapeHtml(settings.network.proxy_url)}"></label><label>控制台日志<select id="log-console">${["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].map((v) => `<option ${v === settings.logging.console_level ? "selected" : ""}>${v}</option>`).join("")}</select></label><label>文件日志<select id="log-file">${["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].map((v) => `<option ${v === settings.logging.file_level ? "selected" : ""}>${v}</option>`).join("")}</select></label>${checkboxField("access-web", "启用 Web 密码", settings.access_control.web_password_enabled)}${checkboxField("access-loopback", "信任本机回环", settings.access_control.trust_loopback)}${checkboxField("access-extension", "允许浏览器扩展", settings.access_control.extension_access_enabled)}${numberField("access-session", "Web 会话小时", settings.access_control.session_ttl_hours, { min: 0, max: 8760 })}${numberField("access-extension-ttl", "扩展会话小时", settings.access_control.extension_session_ttl_hours, { min: 1, max: 168 })}`;
  $("#network-mode").value = settings.network.mode;
  renderSourceAccounts();
}

function renderSourceAccounts() {
  const bySource = new Map(
    state.accounts.map((account) => [account.source_id, account]),
  );
  $("#sourceAccounts").innerHTML = state.manifests
    .map((manifest) => {
      const account = bySource.get(manifest.source_id);
      return `<form class="vnext-source-row" data-account-source="${escapeHtml(manifest.source_id)}"><strong>${escapeHtml(manifest.display_name)}</strong><span>${account?.configured ? `已连接 ${escapeHtml(account.account_key)}` : "需要浏览器 Cookie 才能直接配置"}</span><span class="vnext-actions">${account?.configured ? '<button type="button" class="small-btn" data-disconnect>断开</button>' : `<input name="cookie" type="password" autocomplete="off" placeholder="Cookie"><button class="small-btn">连接</button>`}</span></form>`;
    })
    .join("");
  document.querySelectorAll("[data-account-source]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const source_id = form.dataset.accountSource;
      const cookie = new FormData(form).get("cookie");
      try {
        await request("v1_sources_configure_account", {
          path: { source_id },
          body: { account_key: "default", credentials: { cookie } },
        });
        toast("来源账号已连接");
        await loadSettings();
      } catch (error) {
        toast(errorMessage(error));
      }
    });
    form
      .querySelector("[data-disconnect]")
      ?.addEventListener("click", async () => {
        const account = bySource.get(form.dataset.accountSource);
        await request("v1_sources_disconnect_account", {
          path: {
            source_id: account.source_id,
            account_key: account.account_key,
          },
        });
        await loadSettings();
      });
  });
}

function settingsPayload() {
  return {
    sources: {
      enabled: Object.fromEntries(
        SOURCES.map((s) => [s, valueOf(`source-enabled-${s}`, "boolean")]),
      ),
      weights: Object.fromEntries(
        SOURCES.map((s) => [s, valueOf(`source-weight-${s}`)]),
      ),
    },
    feed: {
      low_watermark: valueOf("feed-low"),
      high_watermark: valueOf("feed-high"),
      candidate_multiplier: valueOf("feed-multiplier"),
      max_batch_candidates: valueOf("feed-batch"),
      max_per_source: valueOf("feed-source"),
      max_per_topic: valueOf("feed-topic"),
      min_score: valueOf("feed-score"),
      min_novelty: valueOf("feed-novelty"),
    },
    profile: { minimum_evidence_confidence: valueOf("profile-confidence") },
    tasks: Object.fromEntries(
      TASKS.map((task) => [
        task,
        {
          model_alias: valueOf(`task-alias-${task}`, "string"),
          semantic_retry_limit: valueOf(`task-retry-${task}`),
          timeout_seconds: valueOf(`task-timeout-${task}`),
          request_limit: valueOf(`task-request-${task}`),
          total_tokens_limit: valueOf(`task-token-${task}`),
        },
      ]),
    ),
    schedules: { source_sync_interval_minutes: valueOf("schedule-sync") },
    jobs: { retention_days: valueOf("job-retention") },
    network: {
      mode: valueOf("network-mode", "string"),
      proxy_url: valueOf("network-proxy", "string"),
    },
    logging: {
      console_level: valueOf("log-console", "string"),
      file_level: valueOf("log-file", "string"),
    },
    access_control: {
      web_password_enabled: valueOf("access-web", "boolean"),
      trust_loopback: valueOf("access-loopback", "boolean"),
      extension_access_enabled: valueOf("access-extension", "boolean"),
      session_ttl_hours: valueOf("access-session"),
      extension_session_ttl_hours: valueOf("access-extension-ttl"),
    },
  };
}

function sourceModulePayload(manifest) {
  const properties = manifest.settings_schema?.properties || {};
  const values = {};
  document
    .querySelectorAll(`[data-source-setting="${manifest.source_id}"]`)
    .forEach((input) => {
      const schema = properties[input.dataset.settingName] || {};
      let value = input.type === "checkbox" ? input.checked : input.value;
      if (schema.type === "integer") value = Number.parseInt(value, 10);
      else if (schema.type === "number") value = Number(value);
      values[input.dataset.settingName] = value;
    });
  return values;
}

async function loadSettings() {
  try {
    const [settings, health, manifests, accounts] = await Promise.all([
      request("v1_settings_get"),
      request("v1_system_ai_health"),
      request("v1_sources_list"),
      request("v1_sources_status"),
    ]);
    const moduleStates = await Promise.all(
      manifests.map((manifest) =>
        request("v1_sources_get_settings", {
          path: { source_id: manifest.source_id },
        }),
      ),
    );
    state.settings = settings;
    state.manifests = manifests;
    state.accounts = accounts;
    state.moduleSettings = Object.fromEntries(
      moduleStates.map((item) => [item.source_id, item.settings]),
    );
    $("#aliasHealth").innerHTML = (health.aliases || [])
      .map(
        (item) =>
          `<div class="vnext-alias-row"><strong>${escapeHtml(item.alias)}</strong><span>${escapeHtml(item.state)}${item.reason ? ` · ${escapeHtml(item.reason)}` : ""}</span><span>${item.available ? "可用" : "不可用"}</span></div>`,
      )
      .join("");
    const adminUrl = safeWebUrl(health.admin_url);
    if (adminUrl) {
      $("#litellmAdmin").href = adminUrl;
      $("#litellmAdmin").hidden = false;
    }
    renderSettings();
  } catch (error) {
    toast(errorMessage(error));
  }
}

async function start() {
  try {
    const auth = await request("v1_auth_status");
    if (auth.enabled && !auth.authenticated) {
      $("#loginGate").hidden = false;
      return;
    }
    const ready = await request("v1_system_readiness");
    $("#statusLabel").textContent = ready.ready
      ? `v${ready.version} 已就绪`
      : `v${ready.version} 启动中`;
    $("#runtimeSummary").textContent = ready.ready ? "运行正常" : "启动中";
    $("#healthState").textContent = ready.ready ? "已就绪" : "启动中";
    $("#mobileRuntimeSummary").textContent = ready.ready
      ? `v${ready.version} 已就绪`
      : `v${ready.version} 启动中`;
    await loadFeed();
    navigate(location.hash.slice(1) || "feed");
  } catch (error) {
    $("#statusLabel").textContent = "后端不可用";
    $("#runtimeSummary").textContent = "不可用";
    $("#healthState").textContent = "不可用";
    $("#mobileRuntimeSummary").textContent = "后端不可用";
    toast(errorMessage(error));
  }
}

document
  .querySelectorAll("[data-page]")
  .forEach((button) =>
    button.addEventListener("click", () => navigate(button.dataset.page)),
  );
$("#sideDrawerBtn").addEventListener("click", () => {
  $("#sideDrawer").classList.toggle("is-open");
  $("#sideDrawer").setAttribute(
    "aria-hidden",
    String(!$("#sideDrawer").classList.contains("is-open")),
  );
});
$("#sideDrawerScrim").addEventListener("click", () => {
  $("#sideDrawer").classList.remove("is-open");
  $("#sideDrawer").setAttribute("aria-hidden", "true");
});
$("#mobileMenuBtn").addEventListener("click", () => setMobileMenu(true));
$("#mobileMenuClose").addEventListener("click", () => setMobileMenu(false));
document.querySelectorAll("[data-mobile-page]").forEach((button) =>
  button.addEventListener("click", () => navigate(button.dataset.mobilePage)),
);
document.querySelectorAll("[data-settings-tab]").forEach((button) =>
  button.addEventListener("click", () => selectSettingsTab(button.dataset.settingsTab)),
);
$("#mobileSearchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.query = $("#mobileSearchInput").value.trim();
  $("#searchInput").value = state.query;
  navigate("feed");
  renderFeed();
});
$("#openSettingsHero").addEventListener("click", () => navigate("settings"));
$("#themeToggleBtn").addEventListener("click", () => {
  const current = localStorage.getItem("obc.theme") || "auto";
  const next = THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  localStorage.setItem("obc.theme", next);
  applyTheme(next);
});
$("#searchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.query = $("#searchInput").value.trim();
  renderFeed();
});
$("#refreshFeed").addEventListener("click", () => void loadFeed());
$("#loadMoreBtn").addEventListener(
  "click",
  () => void loadFeed({ append: true }),
);
$("#replenishFeed").addEventListener("click", async () => {
  try {
    const run = await request("v1_jobs_schedule", {
      body: {
        job_name: "feed_replenishment",
        idempotency_key: `web-feed-${Date.now()}`,
        priority: "user-triggered",
      },
    });
    toast("补货任务已提交");
    void watchJob(run);
  } catch (error) {
    toast(errorMessage(error));
  }
});
$("#addFacet").addEventListener("click", () => {
  const value = $("#facetValue").value.trim();
  if (!value) return;
  state.pendingFacets.push({
    name: $("#facetName").value,
    value,
    weight: Number($("#facetWeight").value),
    confidence: 1,
    evidence_ids: [],
  });
  $("#facetValue").value = "";
  renderProfile();
});
$("#profileForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    state.profile = await request("v1_profile_edit", {
      body: {
        expected_revision: state.profile?.revision ?? 0,
        narrative: $("#profileNarrative").value,
        upserts: state.pendingFacets.map(({ name, value, weight }) => ({
          name,
          value,
          weight,
        })),
        removals: state.removals,
      },
    });
    state.pendingFacets = [];
    state.removals = [];
    renderProfile();
    toast("画像新版本已保存");
  } catch (error) {
    toast(errorMessage(error));
  }
});
$("#chatForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#chatInput");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  addChatTurn({ role: "user", content: message });
  const assistant = addChatTurn({ role: "assistant", content: "" });
  try {
    await readSse(
      "v1_chat_stream",
      {
        body: {
          conversation_id: newConversationId(),
          message,
          learn: $("#chatLearn").checked,
        },
      },
      ({ event, data }) => {
        if (event === "delta") assistant.textContent += data.content || "";
        else if (event === "error")
          assistant.textContent = data.message || "对话失败";
        $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
      },
    );
  } catch (error) {
    assistant.textContent = errorMessage(error);
  }
});
$("#jobForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const run = await request("v1_jobs_schedule", {
      body: {
        job_name: $("#jobName").value,
        idempotency_key: `web-${$("#jobName").value}-${Date.now()}`,
        priority: "user-triggered",
      },
    });
    await loadJobs();
    void watchJob(run);
  } catch (error) {
    toast(errorMessage(error));
  }
});
$("#refreshJobs").addEventListener("click", () => void loadJobs());
$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const [settings] = await Promise.all([
      request("v1_settings_patch", { body: settingsPayload() }),
      ...state.manifests.map((manifest) =>
        request("v1_sources_update_settings", {
          path: { source_id: manifest.source_id },
          body: { settings: sourceModulePayload(manifest) },
        }),
      ),
    ]);
    state.settings = settings;
    renderSettings();
    toast("设置已保存");
  } catch (error) {
    toast(errorMessage(error));
  }
});
$("#loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("v1_auth_login", {
      body: { password: $("#loginPassword").value },
    });
    $("#loginGate").hidden = true;
    await start();
  } catch (error) {
    $("#loginError").textContent = errorMessage(error, "密码不正确");
  }
});
window.addEventListener("obc:auth-required", () => {
  $("#loginGate").hidden = false;
});
window.addEventListener("hashchange", () => navigate(location.hash.slice(1)));
applyTheme(localStorage.getItem("obc.theme") || "auto");
selectSettingsTab("sources");
start();
