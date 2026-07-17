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

const COLLECTIONS = new Set(["favorites", "watch_later"]);
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
  activeView: "feed",
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
  target.style.background = kind === "error" ? "var(--danger)" : "var(--text)";
  target.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { target.hidden = true; }, 3200);
}

function setStatus(label, status = "loading") {
  $("#statusLabel").textContent = label;
  $("#statusBadge").dataset.state = status;
}

function setFormBusy(form, busy) {
  for (const control of form.elements) control.disabled = busy;
}

function emptyState(title, description) {
  return node("div", { class: "card empty" }, [
    node("strong", { text: title }),
    node("span", { text: description }),
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
    tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  }
  for (const panel of $$("[data-view-panel]")) {
    panel.hidden = panel.dataset.viewPanel !== view;
  }
  if (view === "feed") loadFeed();
  if (view === "library") loadLibrary();
  if (view === "profile") loadProfile();
  if (view === "chat") loadChatHistory();
  if (view === "sources") loadSources();
  if (view === "settings") loadSettingsView();
}

async function showPairing() {
  $(".tabs").hidden = true;
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
  $(".tabs").hidden = false;
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
  $(".tabs").hidden = true;
  $("#pairingPanel").hidden = true;
  $("#onboardingPanel").hidden = false;
  for (const panel of $$("[data-view-panel]")) panel.hidden = true;
  const host = $("#onboardingSources");
  host.replaceChildren(...state.manifests.map((manifest) => {
    const status = sourceStatus(manifest.source_id);
    const checked = Boolean(status?.configured || state.settings?.sources?.enabled?.[manifest.source_id]);
    return node("label", { class: "source-card check" }, [
      node("input", { type: "checkbox", value: manifest.source_id, checked }),
      node("span", { text: manifest.display_name }),
      node("span", { class: `pill ${status?.configured ? "ok" : ""}`, text: status?.configured ? "已连接" : "可选" }),
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
  const article = node("article", { class: "card content-card" });
  const title = node("a", {
    class: "content-title",
    text: content.title || "未命名内容",
    href: content.url || "#",
    target: "_blank",
    rel: "noreferrer",
  });
  title.addEventListener("click", () => recordInteraction(content.id, "open"));
  article.append(
    title,
    node("div", { class: "content-meta" }, [
      node("span", { text: content.source_id || "unknown" }),
      node("span", { text: content.creator || "" }),
      node("span", { text: content.media_type || "" }),
    ]),
  );
  if (content.summary) article.append(node("p", { class: "content-summary", text: content.summary }));
  if (feedItem.entry?.explanation) article.append(node("div", { class: "notice", text: feedItem.entry.explanation }));
  const actions = node("div", { class: "button-row" });
  if (collection) {
    const remove = node("button", { class: "button small danger", text: "移除", type: "button" });
    remove.addEventListener("click", () => removeLibraryItem(collection, content.id));
    actions.append(remove);
  } else {
    const actionSpecs = [
      ["喜欢", "positive"],
      ["不喜欢", "negative"],
      ["忽略", "dismiss"],
    ];
    for (const [label, kind] of actionSpecs) {
      const button = node("button", { class: "button small", text: label, type: "button" });
      button.addEventListener("click", () => recordInteraction(content.id, kind, button));
      actions.append(button);
    }
    for (const [label, kind] of [["收藏", "favorites"], ["稍后看", "watch_later"]]) {
      const button = node("button", { class: "button small soft", text: label, type: "button" });
      button.addEventListener("click", () => saveLibraryItem(kind, content.id, button));
      actions.append(button);
    }
  }
  article.append(actions);
  return article;
}

async function loadFeed() {
  const host = $("#feedList");
  host.replaceChildren(emptyState("正在读取发现结果", "请稍候"));
  try {
    const items = await requestV1("v1_feed_list", { query: { limit: 50, offset: 0 } });
    host.replaceChildren(...(items.length
      ? items.map((item) => contentCard(item))
      : [emptyState("还没有发现结果", "完成初始化或等待后台补充内容。")]
    ));
  } catch (error) {
    host.replaceChildren(emptyState("发现结果读取失败", errorMessage(error)));
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
  const host = $("#libraryList");
  host.replaceChildren(emptyState("正在读取资料库", "请稍候"));
  try {
    const items = await requestV1("v1_library_list", { path: { collection: state.activeCollection } });
    host.replaceChildren(...(items.length
      ? items.map((item) => contentCard(item, state.activeCollection))
      : [emptyState("这里还是空的", "从发现页保存内容后，会出现在这里。")]
    ));
  } catch (error) {
    host.replaceChildren(emptyState("资料库读取失败", errorMessage(error)));
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
  if (!state.profile) {
    card.replaceChildren(...emptyState("画像尚未生成", "完成初始化后会建立证据画像。").childNodes);
    $("#profileNarrative").value = "";
    $("#profileFacets").value = "[]";
    return;
  }
  const facets = state.profile.facets || [];
  card.replaceChildren(
    node("div", { class: "section-head" }, [
      node("div", {}, [node("h2", { text: `证据画像 · r${state.profile.revision}` }), node("p", { text: `整体置信度 ${Math.round((state.profile.confidence || 0) * 100)}%` })]),
    ]),
    node("p", { text: state.profile.narrative || "还没有叙述。" }),
    node("div", { class: "profile-facets" }, facets.map((facet) => node("span", {
      class: "facet",
      text: `${LABELS[facet.name] || facet.name} · ${facet.value} (${Number(facet.weight).toFixed(2)})`,
      title: `置信度 ${Math.round(Number(facet.confidence) * 100)}%`,
    }))),
  );
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

function renderChatMessages(items) {
  const host = $("#chatLog");
  host.replaceChildren(...items.map((item) => node("div", {
    class: `message ${item.role === "user" ? "user" : ""}`,
    text: item.content,
  })));
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
    $("#chatLog").replaceChildren(emptyState("对话历史读取失败", errorMessage(error)));
  }
}

async function sendChat(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = $("#chatInput");
  const message = input.value.trim();
  if (!message) return;
  const host = $("#chatLog");
  const userBubble = node("div", { class: "message user", text: message });
  const answerBubble = node("div", { class: "message pending", text: "" });
  host.append(userBubble, answerBubble);
  host.scrollTop = host.scrollHeight;
  input.value = "";
  setFormBusy(form, true);
  try {
    await readV1Sse("v1_chat_stream", {
      body: { conversation_id: state.conversationId, message, learn: $("#chatLearn").checked },
    }, (streamEvent) => {
      if (streamEvent.event === "delta") {
        answerBubble.textContent += streamEvent.data?.content || "";
        host.scrollTop = host.scrollHeight;
      }
      if (streamEvent.event === "error") throw new Error(streamEvent.data?.code || "对话失败");
      if (streamEvent.event === "done" && streamEvent.data?.status === "failed") throw new Error("对话失败");
    });
    answerBubble.classList.remove("pending");
  } catch (error) {
    answerBubble.classList.remove("pending");
    answerBubble.textContent = answerBubble.textContent || `发送失败：${errorMessage(error)}`;
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
    const card = node("div", { class: "source-card" });
    const controls = node("div", { class: "button-row" });
    const configure = node("button", { class: "button small soft", text: "配置", type: "button" });
    const detail = node("div", { class: "fields", hidden: true });
    configure.addEventListener("click", async () => {
      detail.hidden = !detail.hidden;
      if (!detail.hidden && !detail.dataset.loaded) await renderSourceEditor(manifest, status, detail);
    });
    controls.append(configure);
    if (configured) {
      const disconnect = node("button", { class: "button small danger", text: "断开", type: "button" });
      disconnect.addEventListener("click", () => disconnectSource(manifest.source_id, status.account_key));
      controls.append(disconnect);
    }
    card.append(
      node("div", { class: "source-title" }, [
        node("span", { text: manifest.display_name }),
        node("span", { class: `pill ${configured ? "ok" : ""}`, text: configured ? "已连接" : "未连接" }),
      ]),
      node("div", { class: "capabilities" }, (manifest.capabilities || []).map((capability) => node("span", { class: "pill", text: capabilityLabel(capability) }))),
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
    const saveSettings = node("button", { class: "button small", text: "保存来源设置", type: "button" });
    saveSettings.addEventListener("click", async () => {
      try {
        const payload = JSON.parse(settings.value || "{}");
        await requestV1("v1_sources_update_settings", { path: { source_id: manifest.source_id }, body: { settings: payload } });
        toast("来源设置已保存");
      } catch (error) { toast(errorMessage(error), "error"); }
    });
    host.append(node("label", { class: "field", text: "连接器设置（JSON）" }, [settings]), saveSettings);
    if ((manifest.capabilities || []).includes("authentication")) {
      const accountKey = node("input", { value: status?.account_key || "default", placeholder: "default" });
      const cookie = node("textarea", { placeholder: "平台 Cookie，仅加密保存在后端" });
      const connect = node("button", { class: "button small primary", text: "保存凭据", type: "button" });
      connect.addEventListener("click", () => configureSource(manifest.source_id, accountKey.value, cookie.value, connect));
      host.append(
        node("label", { class: "field", text: "账户标识" }, [accountKey]),
        node("label", { class: "field", text: "Cookie" }, [cookie]),
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
  host.replaceChildren(...(health?.aliases || []).map((alias) => node("div", { class: "alias-card" }, [
    node("div", { class: "alias-title" }, [
      node("span", { text: alias.alias }),
      node("span", { class: `pill ${alias.available ? "ok" : "bad"}`, text: alias.state }),
    ]),
    node("div", { class: "muted", text: alias.reason || "可用" }),
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
  const label = node("label", { class: typeof value === "boolean" ? "check" : "field" });
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
  const host = $("#settingsFields");
  if (!state.settings) {
    host.replaceChildren(emptyState("设置不可用", "无法读取后端设置。"));
    return;
  }
  const groups = [];
  for (const groupName of SETTING_GROUPS) {
    const value = state.settings[groupName];
    if (!value || typeof value !== "object") continue;
    const controls = [];
    flattenSettingControls(value, [groupName], controls);
    groups.push(node("fieldset", { class: "settings-group" }, [
      node("legend", { text: labelFor(groupName) }),
      node("div", { class: "settings-fields" }, controls),
    ]));
  }
  host.replaceChildren(...groups);
}

function setNested(target, path, value) {
  let cursor = target;
  for (const part of path.slice(0, -1)) cursor = cursor[part] ||= {};
  cursor[path.at(-1)] = value;
}

function collectSettingsPatch() {
  const patch = {};
  for (const input of $$("[data-setting-path]", $("#settingsFields"))) {
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
  for (const tab of $$("[data-view]")) tab.addEventListener("click", () => activateView(tab.dataset.view));
  for (const button of $$("[data-collection]")) {
    button.addEventListener("click", () => {
      if (!COLLECTIONS.has(button.dataset.collection)) return;
      state.activeCollection = button.dataset.collection;
      for (const sibling of $$("[data-collection]")) sibling.classList.toggle("primary", sibling === button);
      loadLibrary();
    });
  }
  $("#pairingForm").addEventListener("submit", pair);
  $("#endpointForm").addEventListener("submit", saveEndpoint);
  $("#onboardingForm").addEventListener("submit", startOnboarding);
  $("#profileForm").addEventListener("submit", saveProfile);
  $("#chatForm").addEventListener("submit", sendChat);
  $("#settingsForm").addEventListener("submit", saveSettings);
  $("#refreshFeed").addEventListener("click", loadFeed);
  $("#reloadChat").addEventListener("click", loadChatHistory);
  $("#refreshSources").addEventListener("click", loadSources);
  $("#reconnect").addEventListener("click", boot);
  $("#chatInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chatForm").requestSubmit();
    }
  });
}

bindEvents();
loadConversationId().then(boot);
