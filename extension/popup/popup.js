import { requestV1, readV1Sse, resetPopupApiClient } from "./popup-api.js";
import {
  getBackendEndpointConfig,
  updateBackendEndpoint,
} from "./popup-backend-config.js";
import {
  clearPopupSession,
  ensurePopupSession,
  pairDeviceKey,
} from "./popup-device-auth.js";

const FACET_NAMES = new Set([
  "interests",
  "avoidances",
  "style_preferences",
  "values",
  "source_affinities",
]);
const SETTING_GROUPS = [
  "sources",
  "schedules",
  "feed",
  "profile",
  "tasks",
  "network",
  "logging",
  "jobs",
  "access_control",
];
const READ_ONLY_SETTINGS = new Set([
  "logging.directory",
  "jobs.worker_concurrency",
  "access_control.installer_bearer_configured",
  "access_control.password_configured",
]);
const LABELS = {
  sources: "来源分配",
  enabled: "启用",
  weights: "权重",
  schedules: "计划任务",
  source_sync_interval_minutes: "来源同步间隔（分钟）",
  feed: "发现策略",
  low_watermark: "低水位",
  high_watermark: "高水位",
  candidate_multiplier: "候选倍数",
  max_batch_candidates: "单批候选上限",
  min_score: "最低得分",
  min_novelty: "最低新颖度",
  max_per_source: "单来源上限",
  max_per_topic: "单主题上限",
  profile: "证据画像",
  minimum_evidence_confidence: "最低证据置信度",
  tasks: "AI 任务",
  model_alias: "模型别名",
  semantic_retry_limit: "语义重试上限",
  timeout_seconds: "超时（秒）",
  request_limit: "请求上限",
  total_tokens_limit: "Token 总上限",
  network: "网络",
  mode: "代理模式",
  proxy_url: "代理地址",
  logging: "日志",
  console_level: "控制台级别",
  file_level: "文件级别",
  jobs: "后台任务",
  retention_days: "任务记录保留天数",
  access_control: "访问控制",
  extension_access_enabled: "允许扩展访问",
  extension_session_ttl_hours: "扩展会话时长（小时）",
  session_ttl_hours: "网页会话时长（小时）",
  trust_loopback: "信任本机访问",
  web_password_enabled: "启用网页密码",
};

const state = {
  activeView: "recommend",
  activeCollection: "favorites",
  manifests: [],
  sourceStatuses: [],
  settings: null,
  profile: null,
  conversationId: "",
  online: false,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function node(tag, options = {}, children = []) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = String(value ?? "");
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key in element) element[key] = value;
    else element.setAttribute(key, value);
  }
  element.append(...children.filter(Boolean));
  return element;
}

function errorMessage(error) {
  return error?.details?.error?.message
    || error?.details?.detail
    || error?.message
    || "请求失败";
}

let toastTimer = 0;
function toast(message, kind = "") {
  const target = $("#toast");
  target.textContent = message;
  target.dataset.tone = kind === "error" ? "error" : "success";
  target.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { target.hidden = true; }, 3200);
}

function setStatus(label, status = "loading") {
  $("#statusLabel").textContent = label;
  const tone = status === "ready"
    ? "online"
    : status === "error" ? "offline" : "reconnecting";
  $("#statusBadge").dataset.tone = tone;
  $("#statusDot").className = `status-dot ${tone}`;
}

function setFormBusy(form, busy) {
  for (const control of form.elements) control.disabled = busy;
}

function emptyState(title, description) {
  return node("div", { class: "empty-state" }, [
    node("h3", { text: title }),
    node("p", { text: description }),
  ]);
}

function storageGet(key) {
  const storage = globalThis.chrome?.storage?.local;
  if (!storage?.get) return Promise.resolve({});
  return new Promise((resolve) => {
    try {
      const pending = storage.get(key, (items) => resolve(items || {}));
      if (pending?.then) pending.then(resolve).catch(() => resolve({}));
    } catch { resolve({}); }
  });
}

function storageSet(items) {
  const storage = globalThis.chrome?.storage?.local;
  if (!storage?.set) return Promise.resolve();
  return new Promise((resolve) => {
    try {
      const pending = storage.set(items, resolve);
      if (pending?.then) pending.then(resolve).catch(resolve);
    } catch { resolve(); }
  });
}

function createUuid() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16);
    return (char === "x" ? random : (random & 3) | 8).toString(16);
  });
}

async function loadConversationId() {
  const key = "obc_vnext_conversation_id";
  const stored = await storageGet(key);
  state.conversationId = typeof stored[key] === "string" ? stored[key] : createUuid();
  await storageSet({ [key]: state.conversationId });
}

function activateView(view) {
  state.activeView = view;
  for (const tab of $$("[data-view]")) {
    const selected = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(selected));
    tab.classList.toggle("is-active", selected);
    tab.tabIndex = selected ? 0 : -1;
  }
  for (const panel of $$("[data-view-panel]")) {
    panel.hidden = panel.dataset.viewPanel !== view;
  }
  if (view === "recommend") loadFeed();
  if (view === "watch_later" || view === "favorites") {
    state.activeCollection = view;
    loadLibrary();
  }
  if (view === "profile") loadProfile();
  if (view === "chat") loadChatHistory();
}

async function showPairing() {
  $(".tabs-shell").hidden = true;
  $("#settingsOverlay").hidden = true;
  $("#mobileQrOverlay").hidden = true;
  $("#pairingPanel").hidden = false;
  $("#onboardingPanel").hidden = true;
  for (const panel of $$("[data-view-panel]")) panel.hidden = true;
  const endpoint = await getBackendEndpointConfig();
  $("#endpointScheme").value = endpoint.scheme;
  $("#endpointHost").value = endpoint.host;
  $("#endpointPort").value = endpoint.port;
  setStatus("需要配对", "error");
}

function showProduct() {
  $(".tabs-shell").hidden = false;
  $("#pairingPanel").hidden = true;
  $("#onboardingPanel").hidden = true;
  activateView(state.activeView);
}

async function loadCore() {
  const [readiness, auth, settings, manifests, statuses, aiHealth] = await Promise.all([
    requestV1("v1_system_readiness"),
    requestV1("v1_auth_status"),
    requestV1("v1_settings_get"),
    requestV1("v1_sources_list"),
    requestV1("v1_sources_status"),
    requestV1("v1_system_ai_health"),
  ]);
  if (!auth.authenticated) throw new Error("扩展会话未通过验证");
  state.online = Boolean(readiness.ready);
  state.settings = settings;
  state.manifests = Array.isArray(manifests) ? manifests : [];
  state.sourceStatuses = Array.isArray(statuses) ? statuses : [];
  renderAiHealth(aiHealth);
  renderSources();
  renderSettings();
  setStatus(readiness.ready ? `已就绪 · ${readiness.version}` : "服务未就绪", readiness.ready ? "ready" : "error");
  if (!settings.onboarding_complete) showOnboarding();
  else showProduct();
}

async function boot() {
  setStatus("正在连接");
  try {
    const token = await ensurePopupSession();
    if (!token) {
      await showPairing();
      return;
    }
    await loadCore();
  } catch (error) {
    if (error?.status === 401 || /device|session|验证/.test(errorMessage(error))) {
      await clearPopupSession();
      await showPairing();
      return;
    }
    state.online = false;
    setStatus("后端不可用", "error");
    toast(errorMessage(error), "error");
  }
}

function sourceStatus(sourceId) {
  return state.sourceStatuses.find((item) => item.source_id === sourceId) || null;
}

function showOnboarding() {
  $(".tabs-shell").hidden = true;
  $("#pairingPanel").hidden = true;
  $("#onboardingPanel").hidden = false;
  for (const panel of $$("[data-view-panel]")) panel.hidden = true;
  const host = $("#onboardingSources");
  host.replaceChildren(...state.manifests.map((manifest) => {
    const status = sourceStatus(manifest.source_id);
    const checked = Boolean(status?.configured || state.settings?.sources?.enabled?.[manifest.source_id]);
    return node("label", { class: "settings-section settings-source-card settings-field-row" }, [
      node("input", { type: "checkbox", value: manifest.source_id, checked }),
      node("span", { text: manifest.display_name }),
      node("span", { class: "recommendation-state", text: status?.configured ? "已连接" : "可选" }),
    ]);
  }));
}

async function startOnboarding(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const sourceIds = $$("input[type=checkbox]:checked", $("#onboardingSources")).map((item) => item.value);
  if (!sourceIds.length) {
    toast("请至少选择一个来源", "error");
    return;
  }
  setFormBusy(form, true);
  try {
    const run = await requestV1("v1_onboarding_start", { body: { source_ids: sourceIds } });
    await readV1Sse("v1_onboarding_events", { path: { run_id: run.id } }, (message) => {
      if (message.event === "progress") {
        const progress = Number(message.data?.run?.progress || 0);
        const percent = Math.max(0, Math.min(100, progress <= 1 ? progress * 100 : progress));
        $("#onboardingBar").style.width = `${percent}%`;
        $("#onboardingStatus").textContent = `${message.data.stage} · ${message.data.run.status} · ${Math.round(percent)}%`;
      }
      if (message.event === "error") throw new Error(message.data?.code || "初始化失败");
      if (message.event === "done" && message.data?.status !== "succeeded") {
        throw new Error(`初始化${message.data?.status || "失败"}`);
      }
    });
    toast("初始化完成");
    await loadCore();
  } catch (error) {
    toast(errorMessage(error), "error");
  } finally {
    setFormBusy(form, false);
  }
}

function contentCard(feedItem, collection = "") {
  const content = feedItem.content || {};
  const sourceId = String(content.source_id || "unknown").toLowerCase();
  const article = node("article", { class: "recommendation-card" });
  const preview = node("div", { class: "recommendation-preview" });
  const coverUrl = content?.metadata?.thumbnail
    || content?.metadata?.cover
    || content?.metadata?.image
    || "";
  const cover = node(content.url ? "a" : "div", {
    class: "recommendation-cover",
    ...(content.url ? { href: content.url, target: "_blank", rel: "noreferrer" } : {}),
  });
  if (coverUrl) {
    cover.append(node("img", {
      src: coverUrl,
      alt: content.title ? `${content.title} 的封面` : "内容封面",
      loading: "lazy",
    }));
  } else {
    cover.classList.add("is-fallback", "is-text-card");
    cover.append(node("p", {
      class: "recommendation-cover-text",
      text: content.summary || content.body_text || "先看标题也行",
    }));
  }
  cover.append(node("span", {
    class: `recommendation-source-corner source-platform-${sourceId}`,
    text: sourceId,
  }));
  cover.addEventListener("click", () => recordInteraction(content.id, "open"));
  const title = node("a", {
    class: "recommendation-title",
    text: content.title || "未命名内容",
    href: content.url || "#",
    target: "_blank",
    rel: "noreferrer",
  });
  title.addEventListener("click", () => recordInteraction(content.id, "open"));
  const copy = node("div", { class: "recommendation-content" }, [
    node("div", { class: "recommendation-top" }, [
      node("span", {
        class: "recommendation-state",
        text: collection ? "已保存在本地" : "刚给你翻出来",
      }),
    ]),
    node("div", { class: "recommendation-copy-block" }, [title]),
    node("div", { class: "recommendation-meta-line" }, [
      node("span", { text: content.source_id || "unknown" }),
      node("span", { text: content.creator || "" }),
      node("span", { text: content.media_type || "" }),
    ]),
  ]);
  if (content.summary) copy.append(node("p", { class: "recommendation-expression", text: content.summary }));
  if (feedItem.entry?.explanation) copy.append(node("p", { class: "feedback-status", text: feedItem.entry.explanation }));
  preview.append(cover, copy);
  const actions = node("div", { class: "recommendation-actions" });
  if (collection) {
    const remove = node("button", { class: "action-button action-secondary", text: "移除", type: "button" });
    remove.addEventListener("click", () => removeLibraryItem(collection, content.id));
    actions.append(remove);
  } else {
    const actionSpecs = [
      ["喜欢", "positive"],
      ["不喜欢", "negative"],
      ["忽略", "dismiss"],
    ];
    for (const [label, kind] of actionSpecs) {
      const button = node("button", { class: "action-button action-secondary", text: label, type: "button" });
      button.addEventListener("click", () => recordInteraction(content.id, kind, button));
      actions.append(button);
    }
    for (const [label, kind] of [["收藏", "favorites"], ["稍后看", "watch_later"]]) {
      const button = node("button", { class: "action-button action-secondary", text: label, type: "button" });
      button.addEventListener("click", () => saveLibraryItem(kind, content.id, button));
      actions.append(button);
    }
  }
  article.append(preview, actions);
  return article;
}

async function loadFeed() {
  const host = $("#recommendationList");
  $("#emptyState").hidden = true;
  const empty = $("#emptyState");
  host.replaceChildren();
  try {
    const items = await requestV1("v1_feed_list", { query: { limit: 50, offset: 0 } });
    empty.hidden = items.length > 0;
    if (items.length) host.replaceChildren(...items.map((item) => contentCard(item)));
    else {
      $("#emptyTitle").textContent = "还没有发现结果";
      $("#emptyText").textContent = "完成初始化或等待后台补充内容。";
    }
  } catch (error) {
    empty.hidden = false;
    $("#emptyTitle").textContent = "发现结果读取失败";
    $("#emptyText").textContent = errorMessage(error);
  }
}

async function recordInteraction(contentId, kind, button = null) {
  if (!contentId) return;
  if (button) button.disabled = true;
  try {
    await requestV1("v1_interactions_create", { body: { content_id: contentId, kind, metadata: {} } });
    if (kind !== "open") toast("反馈已记录");
  } catch (error) {
    if (kind !== "open") toast(errorMessage(error), "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function saveLibraryItem(collection, contentId, button) {
  button.disabled = true;
  try {
    await requestV1("v1_library_add", { path: { collection }, body: { content_id: contentId, note: "" } });
    const interactionKind = collection === "favorites" ? "save_favorite" : "save_watch_later";
    await recordInteraction(contentId, interactionKind);
    toast(collection === "favorites" ? "已收藏" : "已加入稍后看");
  } catch (error) {
    toast(error?.status === 409 ? "已经保存过了" : errorMessage(error), error?.status === 409 ? "" : "error");
  } finally {
    button.disabled = false;
  }
}

async function loadLibrary() {
  const host = state.activeCollection === "watch_later"
    ? $("#watchLaterList")
    : $("#favoritesList");
  $("#watchLaterEmpty").hidden = true;
  $("#favoritesEmpty").hidden = true;
  const empty = state.activeCollection === "watch_later"
    ? $("#watchLaterEmpty")
    : $("#favoritesEmpty");
  empty.hidden = true;
  host.replaceChildren();
  try {
    const items = await requestV1("v1_library_list", { path: { collection: state.activeCollection } });
    empty.hidden = items.length > 0;
    if (items.length) host.replaceChildren(...items.map((item) => contentCard(item, state.activeCollection)));
  } catch (error) {
    empty.hidden = false;
    empty.querySelector("h3").textContent = "资料库读取失败";
    empty.querySelector("p").textContent = errorMessage(error);
  }
}

async function removeLibraryItem(collection, contentId) {
  try {
    await requestV1("v1_library_remove", { path: { collection, content_id: contentId } });
    toast("已移除");
    await loadLibrary();
  } catch (error) { toast(errorMessage(error), "error"); }
}

function renderProfile() {
  const card = $("#profileCard");
  const empty = $("#profileEmpty");
  const editBar = $("#profileEditBar");
  if (!state.profile) {
    card.hidden = true;
    empty.hidden = false;
    editBar.hidden = true;
    $("#profileNarrative").value = "";
    $("#profileFacets").value = "[]";
    return;
  }
  const facets = state.profile.facets || [];
  card.hidden = false;
  empty.hidden = true;
  editBar.hidden = false;
  $("#profilePortrait").replaceChildren(
    node("p", { class: "profile-portrait-paragraph", text: state.profile.narrative || "还没有叙述。" }),
    node("p", { class: "profile-phase-copy", text: `证据画像 · r${state.profile.revision} · 整体置信度 ${Math.round((state.profile.confidence || 0) * 100)}%` }),
  );
  $("#profileFacetsView").replaceChildren(...facets.map((facet) => node("span", {
      class: "chip",
      text: `${LABELS[facet.name] || facet.name} · ${facet.value} (${Number(facet.weight).toFixed(2)})`,
      title: `置信度 ${Math.round(Number(facet.confidence) * 100)}%`,
    })));
  $("#profileNarrative").value = state.profile.narrative || "";
  $("#profileFacets").value = JSON.stringify(facets.map(({ name, value, weight }) => ({ name, value, weight })), null, 2);
}

async function loadProfile() {
  try {
    state.profile = await requestV1("v1_profile_get");
  } catch (error) {
    if (error?.status !== 404) toast(errorMessage(error), "error");
    state.profile = null;
  }
  renderProfile();
}

async function saveProfile(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const facets = JSON.parse($("#profileFacets").value || "[]");
    if (!Array.isArray(facets)) throw new Error("画像维度必须是数组");
    const upserts = facets.map((facet) => {
      const value = String(facet?.value || "").trim();
      if (!FACET_NAMES.has(facet?.name) || !value) throw new Error("画像维度缺少有效的 name 或 value");
      const weight = Number(facet.weight);
      if (!Number.isFinite(weight)) throw new Error("画像权重必须是数字");
      return { name: facet.name, value, weight };
    });
    const nextKeys = new Set(upserts.map((facet) => `${facet.name}\0${facet.value.toLocaleLowerCase()}`));
    const removals = (state.profile?.facets || [])
      .filter((facet) => !nextKeys.has(`${facet.name}\0${facet.value.toLocaleLowerCase()}`))
      .map(({ name, value }) => ({ name, value }));
    setFormBusy(form, true);
    state.profile = await requestV1("v1_profile_edit", {
      body: {
        expected_revision: state.profile?.revision ?? null,
        narrative: $("#profileNarrative").value,
        upserts,
        removals,
      },
    });
    renderProfile();
    toast("画像已保存");
  } catch (error) {
    toast(errorMessage(error), "error");
  } finally { setFormBusy(form, false); }
}

function chatBubble(role, content = "", pending = false) {
  return node("div", { class: `chat-message ${role === "user" ? "user" : ""}${pending ? " pending" : ""}` }, [
    node("span", { class: "chat-role", text: role === "user" ? "你" : "阿B" }),
    node("div", { class: "chat-content", text: content }),
  ]);
}

function renderChatMessages(items) {
  const host = $("#chatMessages");
  host.replaceChildren(...items.map((item) => chatBubble(item.role, item.content)));
  host.scrollTop = host.scrollHeight;
}

async function loadChatHistory() {
  if (!state.conversationId) await loadConversationId();
  try {
    const page = await requestV1("v1_chat_history", {
      path: { conversation_id: state.conversationId },
      query: { limit: 100, offset: 0 },
    });
    renderChatMessages(page.items || []);
  } catch (error) {
    $("#chatMessages").replaceChildren(emptyState("对话历史读取失败", errorMessage(error)));
  }
}

async function sendChat(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = $("#chatInput");
  const message = input.value.trim();
  if (!message) return;
  const host = $("#chatMessages");
  const userBubble = chatBubble("user", message);
  const answerBubble = chatBubble("assistant", "", true);
  const answerContent = $(".chat-content", answerBubble);
  host.append(userBubble, answerBubble);
  host.scrollTop = host.scrollHeight;
  input.value = "";
  setFormBusy(form, true);
  try {
    await readV1Sse("v1_chat_stream", {
      body: { conversation_id: state.conversationId, message, learn: $("#chatLearn").checked },
    }, (streamEvent) => {
      if (streamEvent.event === "delta") {
        answerContent.textContent += streamEvent.data?.content || "";
        host.scrollTop = host.scrollHeight;
      }
      if (streamEvent.event === "error") throw new Error(streamEvent.data?.code || "对话失败");
      if (streamEvent.event === "done" && streamEvent.data?.status === "failed") throw new Error("对话失败");
    });
    answerBubble.classList.remove("pending");
  } catch (error) {
    answerBubble.classList.remove("pending");
    answerContent.textContent = answerContent.textContent || `发送失败：${errorMessage(error)}`;
    toast(errorMessage(error), "error");
  } finally {
    setFormBusy(form, false);
    input.focus();
  }
}

function capabilityLabel(value) {
  return ({
    authentication: "登录",
    bootstrap_import: "初始化导入",
    activity_collection: "活动采集",
    search: "搜索",
    trending_feed: "热门/信息流",
    related_discovery: "相关发现",
    creator_discovery: "创作者发现",
    community_discovery: "社区发现",
    browser_assisted: "浏览器辅助",
  })[value] || value;
}

function renderSources() {
  const host = $("#sourceList");
  host.replaceChildren(...state.manifests.map((manifest) => {
    const status = sourceStatus(manifest.source_id);
    const configured = Boolean(status?.configured);
    const card = node("section", { class: "settings-section settings-source-card" });
    const controls = node("div", { class: "settings-actions" });
    const configure = node("button", { class: "settings-secondary-btn", text: "配置", type: "button" });
    const detail = node("div", { class: "settings-section", hidden: true });
    configure.addEventListener("click", async () => {
      detail.hidden = !detail.hidden;
      if (!detail.hidden && !detail.dataset.loaded) await renderSourceEditor(manifest, status, detail);
    });
    controls.append(configure);
    if (configured) {
      const disconnect = node("button", { class: "settings-secondary-btn", text: "断开", type: "button" });
      disconnect.addEventListener("click", () => disconnectSource(manifest.source_id, status.account_key));
      controls.append(disconnect);
    }
    card.append(
      node("div", { class: "settings-status-cell" }, [
        node("span", { text: manifest.display_name }),
        node("strong", { text: configured ? "已连接" : "未连接" }),
      ]),
      node("div", { class: "recommendation-meta-line" }, (manifest.capabilities || []).map((capability) => node("span", { class: "recommendation-state", text: capabilityLabel(capability) }))),
      controls,
      detail,
    );
    return card;
  }));
  if (!state.manifests.length) host.replaceChildren(emptyState("没有可用来源", "后端尚未注册来源清单。"));
}

async function renderSourceEditor(manifest, status, host) {
  host.dataset.loaded = "true";
  try {
    const sourceSettings = await requestV1("v1_sources_get_settings", { path: { source_id: manifest.source_id } });
    const settings = node("textarea", { value: JSON.stringify(sourceSettings.settings || {}, null, 2), spellcheck: false });
    const saveSettings = node("button", { class: "settings-secondary-btn", text: "保存来源设置", type: "button" });
    saveSettings.addEventListener("click", async () => {
      try {
        const payload = JSON.parse(settings.value || "{}");
        await requestV1("v1_sources_update_settings", { path: { source_id: manifest.source_id }, body: { settings: payload } });
        toast("来源设置已保存");
      } catch (error) { toast(errorMessage(error), "error"); }
    });
    host.append(node("label", { class: "settings-field", text: "连接器设置（JSON）" }, [settings]), saveSettings);
    if ((manifest.capabilities || []).includes("authentication")) {
      const accountKey = node("input", { value: status?.account_key || "default", placeholder: "default" });
      const cookie = node("textarea", { placeholder: "平台 Cookie，仅加密保存在后端" });
      const connect = node("button", { class: "action-button action-primary", text: "保存凭据", type: "button" });
      connect.addEventListener("click", () => configureSource(manifest.source_id, accountKey.value, cookie.value, connect));
      host.append(
        node("label", { class: "settings-field", text: "账户标识" }, [accountKey]),
        node("label", { class: "settings-field", text: "Cookie" }, [cookie]),
        connect,
      );
    }
  } catch (error) {
    host.append(node("div", { class: "notice error", text: errorMessage(error) }));
  }
}

async function configureSource(sourceId, accountKey, cookie, button) {
  if (!accountKey.trim() || !cookie.trim()) {
    toast("账户标识和 Cookie 不能为空", "error");
    return;
  }
  button.disabled = true;
  try {
    await requestV1("v1_sources_configure_account", {
      path: { source_id: sourceId },
      body: { account_key: accountKey.trim(), credentials: { cookie: cookie.trim() } },
    });
    toast("来源已连接");
    await loadSources();
  } catch (error) { toast(errorMessage(error), "error"); }
  finally { button.disabled = false; }
}

async function disconnectSource(sourceId, accountKey) {
  try {
    await requestV1("v1_sources_disconnect_account", { path: { source_id: sourceId, account_key: accountKey } });
    toast("来源已断开");
    await loadSources();
  } catch (error) { toast(errorMessage(error), "error"); }
}

async function loadSources() {
  try {
    const [manifests, statuses] = await Promise.all([
      requestV1("v1_sources_list"),
      requestV1("v1_sources_status"),
    ]);
    state.manifests = Array.isArray(manifests) ? manifests : [];
    state.sourceStatuses = Array.isArray(statuses) ? statuses : [];
    renderSources();
  } catch (error) { toast(errorMessage(error), "error"); }
}

function renderAiHealth(health) {
  const host = $("#aliasHealth");
  host.replaceChildren(...(health?.aliases || []).map((alias) => node("section", { class: "settings-section alias-card" }, [
    node("div", { class: "settings-status-cell" }, [
      node("span", { text: alias.alias }),
      node("strong", { text: alias.state }),
    ]),
    node("div", { class: "settings-hint", text: alias.reason || "可用" }),
  ])));
  if (!(health?.aliases || []).length) host.replaceChildren(emptyState("没有别名状态", "LiteLLM 尚未返回健康信息。"));
  const admin = $("#litellmAdmin");
  admin.hidden = !health?.admin_url;
  if (health?.admin_url) admin.href = health.admin_url;
}

function labelFor(key) { return LABELS[key] || key.replaceAll("_", " "); }

function selectOptions(path, value) {
  if (path.endsWith("model_alias")) return ["obc-interactive", "obc-analysis"];
  if (path === "network.mode") return ["direct", "system", "custom"];
  if (path === "logging.console_level" || path === "logging.file_level") return ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
  return null;
}

function settingControl(pathParts, value) {
  const path = pathParts.join(".");
  const label = node("label", { class: typeof value === "boolean" ? "settings-field-row" : "settings-field" });
  let input;
  const options = selectOptions(path, value);
  if (options) {
    input = node("select");
    input.append(...options.map((option) => node("option", { value: option, text: option, selected: value === option })));
  } else if (typeof value === "boolean") {
    input = node("input", { type: "checkbox", checked: value });
  } else {
    input = node("input", {
      type: typeof value === "number" ? "number" : "text",
      value: value ?? "",
      step: typeof value === "number" ? "any" : undefined,
    });
  }
  input.dataset.settingPath = JSON.stringify(pathParts);
  input.dataset.valueType = typeof value;
  if (typeof value === "boolean") label.append(input, node("span", { text: labelFor(pathParts.at(-1)) }));
  else label.append(node("span", { text: labelFor(pathParts.at(-1)) }), input);
  return label;
}

function flattenSettingControls(value, pathParts, controls) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    if (!READ_ONLY_SETTINGS.has(pathParts.join("."))) controls.push(settingControl(pathParts, value));
    return;
  }
  for (const [key, nested] of Object.entries(value)) flattenSettingControls(nested, [...pathParts, key], controls);
}

function renderSettings() {
  const hosts = {
    ai: $("#settingsAiFields"),
    sources: $("#settingsSourceFields"),
    scheduler: $("#settingsSchedulerFields"),
    general: $("#settingsGeneralFields"),
    logging: $("#settingsLoggingFields"),
  };
  for (const host of Object.values(hosts)) host.replaceChildren();
  if (!state.settings) {
    hosts.general.replaceChildren(emptyState("设置不可用", "无法读取后端设置。"));
    return;
  }
  for (const groupName of SETTING_GROUPS) {
    const value = state.settings[groupName];
    if (!value || typeof value !== "object") continue;
    const controls = [];
    flattenSettingControls(value, [groupName], controls);
    const group = node("fieldset", { class: "settings-section settings-group" }, [
      node("legend", { text: labelFor(groupName) }),
      node("div", { class: "settings-panel" }, controls),
    ]);
    const destination = groupName === "sources"
      ? hosts.sources
      : groupName === "tasks"
        ? hosts.ai
        : groupName === "logging"
          ? hosts.logging
          : ["schedules", "feed", "jobs"].includes(groupName)
            ? hosts.scheduler
            : hosts.general;
    destination.append(group);
  }
}

function setNested(target, path, value) {
  let cursor = target;
  for (const part of path.slice(0, -1)) cursor = cursor[part] ||= {};
  cursor[path.at(-1)] = value;
}

function collectSettingsPatch() {
  const patch = {};
  for (const input of $$("[data-setting-path]", $("#settingsForm"))) {
    const path = JSON.parse(input.dataset.settingPath);
    let value;
    if (input.dataset.valueType === "boolean") value = input.checked;
    else if (input.dataset.valueType === "number") {
      value = Number(input.value);
      if (!Number.isFinite(value)) throw new Error(`${labelFor(path.at(-1))} 必须是数字`);
    } else value = input.value;
    setNested(patch, path, value);
  }
  return patch;
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    const patch = collectSettingsPatch();
    setFormBusy(form, true);
    state.settings = await requestV1("v1_settings_patch", { body: patch });
    renderSettings();
    toast("设置已保存");
  } catch (error) { toast(errorMessage(error), "error"); }
  finally { setFormBusy(form, false); }
}

async function loadSettingsView() {
  try {
    const [settings, health] = await Promise.all([
      requestV1("v1_settings_get"),
      requestV1("v1_system_ai_health"),
    ]);
    state.settings = settings;
    renderSettings();
    renderAiHealth(health);
  } catch (error) { toast(errorMessage(error), "error"); }
}

function activateSettingsPanel(panelName) {
  for (const tab of $$("[data-settings-tab]")) {
    const active = tab.dataset.settingsTab === panelName;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  for (const panel of $$("[data-settings-panel]")) {
    panel.hidden = panel.dataset.settingsPanel !== panelName;
  }
}

async function showSettings() {
  $("#settingsOverlay").hidden = false;
  activateSettingsPanel("models");
  await loadSettingsView();
}

function hideSettings() {
  $("#settingsOverlay").hidden = true;
}

function extensionPage(pathname) {
  return getBackendEndpointConfig().then(({ scheme, host, port }) => (
    `${scheme}://${host}:${port}${pathname}`
  ));
}

async function openExternal(url) {
  const tabs = globalThis.chrome?.tabs;
  if (tabs?.create) {
    await tabs.create({ url });
    return;
  }
  globalThis.open?.(url, "_blank", "noopener");
}

async function showMobileEntry() {
  const url = await extensionPage("/m");
  $("#mobileQrUrl").textContent = url;
  $("#mobileQrCode").replaceChildren(node("a", {
    class: "mobile-qr-link",
    text: "打开手机版",
    href: url,
    target: "_blank",
    rel: "noreferrer",
    title: "在浏览器中打开移动版",
  }));
  $("#mobileQrOverlay").hidden = false;
}

async function pair(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setFormBusy(form, true);
  try {
    await pairDeviceKey($("#deviceKey").value);
    $("#deviceKey").value = "";
    resetPopupApiClient();
    await loadCore();
    toast("扩展已配对");
  } catch (error) { toast(errorMessage(error), "error"); }
  finally { setFormBusy(form, false); }
}

async function saveEndpoint(event) {
  event.preventDefault();
  const form = event.currentTarget;
  setFormBusy(form, true);
  try {
    await updateBackendEndpoint($("#endpointScheme").value, $("#endpointHost").value, $("#endpointPort").value);
    await clearPopupSession();
    resetPopupApiClient();
    toast("后端地址已保存，请重新配对");
    await showPairing();
  } catch (error) { toast(errorMessage(error), "error"); }
  finally { setFormBusy(form, false); }
}

function bindEvents() {
  const primaryTabs = {
    tabRecommend: "recommend",
    tabWatchLater: "watch_later",
    tabFavorites: "favorites",
    tabProfile: "profile",
    tabChat: "chat",
  };
  for (const [tabId, view] of Object.entries(primaryTabs)) {
    const tab = $(`#${tabId}`);
    tab.addEventListener("click", () => activateView(view));
    tab.addEventListener("keydown", (event) => {
      const tabs = Object.keys(primaryTabs).map((id) => $(`#${id}`));
      const current = tabs.indexOf(tab);
      let next = current;
      if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      const nextTab = tabs[next];
      activateView(nextTab.dataset.view);
      nextTab.focus();
    });
  }
  for (const tab of $$("[data-settings-tab]")) {
    tab.addEventListener("click", () => activateSettingsPanel(tab.dataset.settingsTab));
  }
  $("#pairingForm").addEventListener("submit", pair);
  $("#endpointForm").addEventListener("submit", saveEndpoint);
  $("#onboardingForm").addEventListener("submit", startOnboarding);
  $("#profileForm").addEventListener("submit", saveProfile);
  $("#chatForm").addEventListener("submit", sendChat);
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#refreshRecommendationsButton").addEventListener("click", loadFeed);
  $("#reloadChat").addEventListener("click", loadChatHistory);
  $("#refreshSources").addEventListener("click", loadSources);
  $("#reconnect").addEventListener("click", boot);
  $("#settingsGear").addEventListener("click", showSettings);
  $("#settingsBack").addEventListener("click", hideSettings);
  $("#openWebButton").addEventListener("click", async () => openExternal(await extensionPage("/web")));
  $("#starButton").addEventListener("click", () => openExternal("https://github.com/whiteguo233/OpenBiliClaw"));
  $("#mobileQrButton").addEventListener("click", showMobileEntry);
  $("#mobileQrBack").addEventListener("click", () => { $("#mobileQrOverlay").hidden = true; });
  $("#mobileQrOpen").addEventListener("click", () => openExternal($("#mobileQrUrl").textContent));
  $("#mobileQrCopy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#mobileQrUrl").textContent);
      toast("移动端链接已复制");
    } catch {
      toast("无法复制，请手动选择链接", "error");
    }
  });
  $("#profileEditToggle").addEventListener("click", () => {
    const panel = $("#profileEditPanel");
    panel.hidden = !panel.hidden;
    $("#profileEditToggle").setAttribute("aria-expanded", String(!panel.hidden));
    $("#profileEditHint").hidden = panel.hidden;
  });
  $("#chatInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chatForm").requestSubmit();
    }
  });
}

bindEvents();
loadConversationId().then(boot);
