import { buildVideoUrl, getPopupState } from "./popup-helpers.js";

const BACKEND_URL = "http://127.0.0.1:8420/api";

const elements = {
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  list: document.getElementById("recommendationList"),
  emptyState: document.getElementById("emptyState"),
  emptyTitle: document.getElementById("emptyTitle"),
  emptyText: document.getElementById("emptyText"),
  hintText: document.getElementById("hintText"),
};

function setStatus(online) {
  if (!elements.statusDot || !elements.statusText) return;

  elements.statusDot.classList.toggle("offline", !online);
  elements.statusText.textContent = online ? "已连接到本地后端" : "后端未连接";
}

function setHint(message) {
  if (!elements.hintText) return;
  elements.hintText.textContent = message;
}

async function checkBackendStatus() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`, { method: "GET" });
    return response.ok;
  } catch {
    return false;
  }
}

async function fetchRecommendations() {
  const response = await fetch(`${BACKEND_URL}/recommendations`, { method: "GET" });
  if (!response.ok) {
    throw new Error(`recommendations request failed: ${response.status}`);
  }
  const payload = await response.json();
  return Array.isArray(payload.items) ? payload.items : [];
}

function showEmptyState(title, message) {
  if (!elements.emptyState || !elements.emptyTitle || !elements.emptyText) return;
  elements.emptyState.hidden = false;
  elements.emptyTitle.textContent = title;
  elements.emptyText.textContent = message;
}

function hideEmptyState() {
  if (!elements.emptyState) return;
  elements.emptyState.hidden = true;
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
    event.stopPropagation();
    onClick();
  });
  return button;
}

function renderRecommendations(items) {
  if (!elements.list) return;
  elements.list.replaceChildren();

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "recommendation-card";
    card.tabIndex = 0;
    card.addEventListener("click", () => {
      void openRecommendation(item.bvid);
    });
    card.addEventListener("keydown", (event) => {
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

    card.append(title, meta);

    if (item.topic_label) {
      const badge = document.createElement("span");
      badge.className = "topic-badge";
      badge.textContent = item.topic_label;
      card.append(badge);
    }

    const expression = document.createElement("p");
    expression.className = "recommendation-expression";
    expression.textContent = item.expression;
    card.append(expression);

    const actions = document.createElement("div");
    actions.className = "recommendation-actions";
    actions.append(
      createActionButton("打开视频", "action-button action-primary", () => {
        void openRecommendation(item.bvid);
      }),
      createActionButton("喜欢", "action-button action-secondary", () => {
        setHint("插件反馈按钮即将支持。");
      }),
      createActionButton("不喜欢", "action-button action-secondary", () => {
        setHint("插件反馈按钮即将支持。");
      }),
    );

    card.append(actions);
    elements.list.append(card);
  }
}

function renderState(state) {
  if (state.kind === "ready") {
    hideEmptyState();
    renderRecommendations(state.items);
    setHint("点击卡片或“打开视频”即可跳转到 B 站。");
    return;
  }

  if (elements.list) {
    elements.list.replaceChildren();
  }

  if (state.kind === "offline") {
    showEmptyState("本地后端未启动", state.message);
    setHint("先在项目根目录运行 openbiliclaw start。");
    return;
  }

  if (state.kind === "error") {
    showEmptyState("推荐暂时不可用", state.message);
    setHint("后端已连接，但推荐接口当前不可用。");
    return;
  }

  showEmptyState("还没有推荐内容", state.message);
  setHint("先运行 init、discover 或 recommend，popup 才会显示推荐。");
}

async function initializePopup() {
  setHint("正在检查连接状态...");
  const online = await checkBackendStatus();
  setStatus(online);

  if (!online) {
    renderState(getPopupState({ online, items: [] }));
    return;
  }

  try {
    const items = await fetchRecommendations();
    renderState(getPopupState({ online, items }));
  } catch (error) {
    renderState(getPopupState({ online, items: [], error }));
  }
}

void initializePopup();
