import {
  buildFeedbackPayload,
  buildVideoUrl,
  getPopupState,
  getTabButtonState,
  normalizeProfileSummary,
  validateCommentInput,
} from "./popup-helpers.js";
import {
  checkBackendStatus,
  fetchProfileSummary,
  fetchRecommendations,
  sendChatMessage,
  submitFeedback,
} from "./popup-api.js";

const state = {
  activeTab: "recommend",
  online: false,
  recommendations: [],
  profile: null,
  profileLoaded: false,
};

const elements = {
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  hintText: document.getElementById("hintText"),
  emptyState: document.getElementById("emptyState"),
  emptyTitle: document.getElementById("emptyTitle"),
  emptyText: document.getElementById("emptyText"),
  list: document.getElementById("recommendationList"),
  tabRecommend: document.getElementById("tabRecommend"),
  tabProfile: document.getElementById("tabProfile"),
  tabChat: document.getElementById("tabChat"),
  viewRecommend: document.getElementById("viewRecommend"),
  viewProfile: document.getElementById("viewProfile"),
  viewChat: document.getElementById("viewChat"),
  profileEmpty: document.getElementById("profileEmpty"),
  profileEmptyTitle: document.getElementById("profileEmptyTitle"),
  profileEmptyText: document.getElementById("profileEmptyText"),
  profileCard: document.getElementById("profileCard"),
  profilePortrait: document.getElementById("profilePortrait"),
  profileTraits: document.getElementById("profileTraits"),
  profileNeeds: document.getElementById("profileNeeds"),
  profileInterests: document.getElementById("profileInterests"),
  chatMessages: document.getElementById("chatMessages"),
  chatForm: document.getElementById("chatForm"),
  chatInput: document.getElementById("chatInput"),
  chatSendButton: document.getElementById("chatSendButton"),
};

function setHint(message) {
  if (elements.hintText) {
    elements.hintText.textContent = message;
  }
}

function setStatus(online) {
  if (!elements.statusDot || !elements.statusText) {
    return;
  }
  elements.statusDot.classList.toggle("offline", !online);
  elements.statusText.textContent = online ? "已连接到本地后端" : "后端未连接";
}

function setActiveTab(tabName) {
  state.activeTab = tabName;

  const tabs = [
    ["recommend", elements.tabRecommend, elements.viewRecommend],
    ["profile", elements.tabProfile, elements.viewProfile],
    ["chat", elements.tabChat, elements.viewChat],
  ];

  for (const [name, tabButton, view] of tabs) {
    if (!(tabButton instanceof HTMLButtonElement) || !(view instanceof HTMLElement)) {
      continue;
    }
    const tabState = getTabButtonState(tabName, name);
    tabButton.classList.toggle("is-active", tabState.selected);
    tabButton.setAttribute("aria-selected", String(tabState.selected));
    tabButton.tabIndex = tabState.tabIndex;
    view.hidden = !tabState.selected;
  }

  if (tabName === "profile") {
    void loadProfileSummary();
  }
}

function showRecommendationEmptyState(title, message) {
  if (
    !(elements.emptyState instanceof HTMLElement) ||
    !(elements.emptyTitle instanceof HTMLElement) ||
    !(elements.emptyText instanceof HTMLElement)
  ) {
    return;
  }
  elements.emptyState.hidden = false;
  elements.emptyTitle.textContent = title;
  elements.emptyText.textContent = message;
}

function hideRecommendationEmptyState() {
  if (elements.emptyState instanceof HTMLElement) {
    elements.emptyState.hidden = true;
  }
}

function renderChipList(container, items, fallback) {
  if (!(container instanceof HTMLElement)) {
    return;
  }
  container.replaceChildren();
  const values = items.length > 0 ? items : [fallback];
  for (const item of values) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item;
    container.append(chip);
  }
}

function renderProfileSummary(summary) {
  if (
    !(elements.profileEmpty instanceof HTMLElement) ||
    !(elements.profileCard instanceof HTMLElement) ||
    !(elements.profileEmptyTitle instanceof HTMLElement) ||
    !(elements.profileEmptyText instanceof HTMLElement) ||
    !(elements.profilePortrait instanceof HTMLElement)
  ) {
    return;
  }

  if (!summary.initialized) {
    elements.profileCard.hidden = true;
    elements.profileEmpty.hidden = false;
    elements.profileEmptyTitle.textContent = "画像还没准备好";
    elements.profileEmptyText.textContent = "先运行 openbiliclaw init，再回来看看它怎么理解你。";
    return;
  }

  elements.profileEmpty.hidden = true;
  elements.profileCard.hidden = false;
  elements.profilePortrait.textContent = summary.personality_portrait;
  renderChipList(elements.profileTraits, summary.core_traits, "还在观察你的稳定特质");
  renderChipList(elements.profileNeeds, summary.deep_needs, "还在摸你的内在驱动力");
  renderChipList(elements.profileInterests, summary.top_interests, "先多用一会儿，它会慢慢摸清你的偏好");
}

function appendChatMessage(role, content) {
  if (!(elements.chatMessages instanceof HTMLElement)) {
    return;
  }
  const item = document.createElement("div");
  item.className = `chat-message${role === "你" ? " user" : ""}`;

  const label = document.createElement("span");
  label.className = "chat-role";
  label.textContent = role;

  const text = document.createElement("p");
  text.className = "chat-content";
  text.textContent = content;

  item.append(label, text);
  elements.chatMessages.append(item);
  elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

async function openRecommendation(bvid) {
  if (!bvid) {
    setHint("这条推荐暂时缺少 BV 号，稍后再试。");
    return;
  }
  await chrome.tabs.create({ url: buildVideoUrl(bvid) });
}

function createActionButton(label, className, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    onClick();
  });
  return button;
}

function createCommentComposer(item, statusLine) {
  const wrapper = document.createElement("div");
  wrapper.className = "comment-composer";
  wrapper.hidden = true;

  const input = document.createElement("textarea");
  input.className = "comment-input";
  input.rows = 3;
  input.placeholder = "写一句你对这条推荐的真实感觉";

  const submit = createActionButton("发送", "action-button action-primary", async () => {
    const validation = validateCommentInput(input.value);
    if (!validation.valid) {
      setHint(validation.message);
      input.focus();
      return;
    }
    try {
      await submitFeedback(buildFeedbackPayload(item.id, "comment", input.value));
      setHint("这句反馈已经记下了。");
      statusLine.textContent = "已记录：你写下了一条真实感受。";
      wrapper.hidden = true;
      input.value = "";
    } catch {
      setHint("反馈失败，请确认本地后端已启动。");
    }
  });

  wrapper.append(input, submit);
  return { wrapper, input };
}

function renderRecommendations(items) {
  if (!(elements.list instanceof HTMLElement)) {
    return;
  }
  elements.list.replaceChildren();

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "recommendation-card";

    const preview = document.createElement("div");
    preview.className = "recommendation-preview";
    preview.tabIndex = 0;
    preview.setAttribute("role", "button");
    preview.addEventListener("click", () => {
      void openRecommendation(item.bvid);
    });
    preview.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        void openRecommendation(item.bvid);
      }
    });

    const title = document.createElement("h3");
    title.className = "recommendation-title";
    title.textContent = item.title;

    const meta = document.createElement("p");
    meta.className = "recommendation-meta";
    meta.textContent = `UP 主：${item.up_name}`;

    preview.append(title, meta);

    if (item.topic_label) {
      const badge = document.createElement("span");
      badge.className = "topic-badge";
      badge.textContent = item.topic_label;
      preview.append(badge);
    }

    const expression = document.createElement("p");
    expression.className = "recommendation-expression";
    expression.textContent = item.expression;
    preview.append(expression);

    const feedbackStatus = document.createElement("p");
    feedbackStatus.className = "feedback-status";
    feedbackStatus.textContent = item.presented ? "这条已经展示给你了。" : "";

    const actions = document.createElement("div");
    actions.className = "recommendation-actions";
    const composer = createCommentComposer(item, feedbackStatus);
    actions.append(
      createActionButton("打开视频", "action-button action-primary", () => {
        void openRecommendation(item.bvid);
      }),
      createActionButton("喜欢", "action-button action-secondary", async () => {
        try {
          await submitFeedback(buildFeedbackPayload(item.id, "like"));
          setHint("已记录你喜欢这条推荐。");
          feedbackStatus.textContent = "已记录：你对这条内容是喜欢的。";
        } catch {
          setHint("反馈失败，请确认本地后端已启动。");
        }
      }),
      createActionButton("不喜欢", "action-button action-secondary", async () => {
        try {
          await submitFeedback(buildFeedbackPayload(item.id, "dislike"));
          setHint("已记录你不喜欢这条推荐。");
          feedbackStatus.textContent = "已记录：你不太想再看到这种方向。";
        } catch {
          setHint("反馈失败，请确认本地后端已启动。");
        }
      }),
      createActionButton("写一句", "action-button action-secondary", () => {
        composer.wrapper.hidden = !composer.wrapper.hidden;
        if (!composer.wrapper.hidden) {
          composer.input.focus();
        }
      }),
    );

    card.append(preview, actions, composer.wrapper, feedbackStatus);
    elements.list.append(card);
  }
}

function renderRecommendationState(stateShape) {
  if (stateShape.kind === "ready") {
    hideRecommendationEmptyState();
    renderRecommendations(stateShape.items);
    setHint("推荐卡片里可以直接打开视频，也可以在这里给出即时反馈。");
    return;
  }

  if (elements.list instanceof HTMLElement) {
    elements.list.replaceChildren();
  }

  if (stateShape.kind === "offline") {
    showRecommendationEmptyState("本地后端未启动", stateShape.message);
    setHint("先在项目根目录运行 openbiliclaw start。");
    return;
  }

  if (stateShape.kind === "error") {
    showRecommendationEmptyState("推荐暂时不可用", stateShape.message);
    setHint("后端已连接，但推荐接口当前不可用。");
    return;
  }

  showRecommendationEmptyState("还没有推荐内容", stateShape.message);
  setHint("先运行 init、discover 或 recommend，再回来看看。");
}

async function loadProfileSummary() {
  if (!state.online || state.profileLoaded) {
    if (!state.online) {
      renderProfileSummary(normalizeProfileSummary({ initialized: false }));
    }
    return;
  }

  try {
    const summary = await fetchProfileSummary();
    state.profile = normalizeProfileSummary(summary);
  } catch {
    state.profile = normalizeProfileSummary({ initialized: false });
  }
  state.profileLoaded = true;
  renderProfileSummary(state.profile);
}

async function initializeRecommendations() {
  const online = await checkBackendStatus();
  state.online = online;
  setStatus(online);

  if (!online) {
    renderRecommendationState(getPopupState({ online, items: [] }));
    renderProfileSummary(normalizeProfileSummary({ initialized: false }));
    return;
  }

  try {
    state.recommendations = await fetchRecommendations();
    renderRecommendationState(getPopupState({ online, items: state.recommendations }));
  } catch (error) {
    renderRecommendationState(getPopupState({ online, items: [], error }));
  }
}

function bindTabs() {
  const bindings = [
    [elements.tabRecommend, "recommend"],
    [elements.tabProfile, "profile"],
    [elements.tabChat, "chat"],
  ];

  for (const [button, tabName] of bindings) {
    if (!(button instanceof HTMLButtonElement)) {
      continue;
    }
    button.addEventListener("click", () => {
      setActiveTab(tabName);
    });
  }
}

function bindChat() {
  if (
    !(elements.chatForm instanceof HTMLFormElement) ||
    !(elements.chatInput instanceof HTMLTextAreaElement) ||
    !(elements.chatSendButton instanceof HTMLButtonElement)
  ) {
    return;
  }

  elements.chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = elements.chatInput.value.trim();
    if (!message) {
      setHint("先写一句你现在真正想聊的。");
      elements.chatInput.focus();
      return;
    }
    if (!state.online) {
      setHint("后端还没连上，暂时没法和阿B聊。");
      return;
    }

    appendChatMessage("你", message);
    elements.chatInput.value = "";
    elements.chatSendButton.disabled = true;
    elements.chatSendButton.textContent = "思考中...";

    try {
      const payload = await sendChatMessage(message);
      appendChatMessage("阿B", payload.reply);
      setHint("阿B 已经接住了你刚才的问题。");
    } catch {
      appendChatMessage("阿B", "我刚刚断了一下线，你再换个说法告诉我一次。");
      setHint("聊天接口暂时不可用，请确认本地后端已启动。");
    } finally {
      elements.chatSendButton.disabled = false;
      elements.chatSendButton.textContent = "发送";
    }
  });
}

async function initializePopup() {
  bindTabs();
  bindChat();
  setActiveTab("recommend");
  setHint("正在检查连接状态...");
  await initializeRecommendations();
}

void initializePopup();
