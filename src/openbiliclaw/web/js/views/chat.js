import {
  startChatTurn,
  fetchChatTurn,
  fetchChatTurns,
  fetchProfileSummary,
  fetchActivityFeed,
  fetchPendingNotifications,
  fetchPendingProbes,
  fetchPendingAvoidanceProbes,
  fetchDelightBatch,
  respondToDelight,
  markDelightSent,
  respondToProbe,
  respondToAvoidanceProbe
} from "../api.js";
import { setUnreadCount, navigateToTab } from "../app.js";
import {
  forgetHandledProbe,
  mergeProbeNotifications,
  probeNotificationKey,
  rememberHandledProbe,
  removeProbeFromNotifications,
  shouldDisplayProbeFromWebSocket
} from "./probe-notification-helpers.js";
import {
  normalizeChatTurn,
  normalizeProfileSummary,
  normalizeActivityFeed,
  normalizeDelightCandidate,
  getDelightActionState,
  getProbeMessageActions,
  getAvoidanceProbeMessageActions,
  getMobileChatSession,
  buildContentUrl
} from "../view-models.js";
import { openContentUrl } from "../app-launch.js";
import { state, patchState } from "../state.js";
let $root = null;
let loaded = false;
let turns = [];
let sending = false;
let pendingTurnId = null;
let pollTimer = null;
let userScrolledUp = false;
let overlayOpen = false;
let notifications = [];
let delightMsgs = [];
const pendingProbeActions = /* @__PURE__ */ new Map();
function pendingProbeAction(type, domain) {
  return pendingProbeActions.get(probeNotificationKey(type, domain)) || null;
}
function setProbeCardBusy(card, busy) {
  if (!card) return;
  card.classList.toggle("is-processing", busy);
  card.setAttribute("aria-busy", busy ? "true" : "false");
  for (const actionBtn of card.querySelectorAll("[data-probe]")) {
    actionBtn.disabled = busy;
  }
}
const PLACEHOLDERS = [
  "最近有什么想聊的？",
  "对哪条推荐有想法？",
  "想探索什么新领域？",
  "觉得画像准不准？",
  "有什么不想再看到的？"
];
let placeholderIdx = 0;
let placeholderTimer = null;
let inputFocused = false;
function chatSession(scope = "chat") {
  return getMobileChatSession(scope);
}
function esc(s) {
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}
function isChallengeProbe(item) {
  const mode = String(item?.probe_mode || "").toLowerCase();
  return Boolean(item?.challenge) || mode === "lateral" || mode === "bridge" || mode === "wildcard";
}
function render() {
  if (!$root) return;
  $root.innerHTML = "";
  const shell = document.createElement("div");
  shell.className = "chat-shell";
  const messages = document.createElement("div");
  messages.className = "chat-messages";
  messages.id = "chat-messages";
  if (turns.length === 0 && !sending) {
    messages.innerHTML = `<div class="empty-state"><div class="empty-state-icon">💬</div><div class="empty-state-text">和 AI 聊聊你的兴趣和想法</div></div>`;
  }
  for (const turn of turns) {
    if (turn.message) {
      const userBubble = document.createElement("div");
      userBubble.className = "chat-bubble user";
      userBubble.textContent = turn.message;
      messages.appendChild(userBubble);
    }
    if (turn.response) {
      const aiBubble = document.createElement("div");
      aiBubble.className = "chat-bubble assistant";
      aiBubble.textContent = turn.response;
      messages.appendChild(aiBubble);
    } else if (turn.status === "pending" || turn.status === "processing") {
      const thinking = document.createElement("div");
      thinking.className = "chat-bubble thinking";
      thinking.innerHTML = `<div class="spinner" style="width:16px;height:16px;display:inline-block;vertical-align:middle;margin-right:6px"></div>思考中…`;
      messages.appendChild(thinking);
    } else if (turn.status === "error" || turn.status === "failed") {
      const errBubble = document.createElement("div");
      errBubble.className = "chat-bubble error";
      errBubble.textContent = turn.error || "回复失败";
      const retryBtn = document.createElement("button");
      retryBtn.className = "chat-retry-btn";
      retryBtn.textContent = "重试";
      retryBtn.addEventListener("click", () => retryTurn(turn));
      errBubble.appendChild(retryBtn);
      messages.appendChild(errBubble);
    }
  }
  messages.addEventListener("scroll", () => {
    userScrolledUp = messages.scrollTop + messages.clientHeight < messages.scrollHeight - 40;
  });
  shell.appendChild(messages);
  const inputRow = document.createElement("div");
  inputRow.className = "chat-input-row";
  const textarea = document.createElement("textarea");
  textarea.className = "chat-input";
  textarea.id = "chat-input";
  textarea.placeholder = PLACEHOLDERS[placeholderIdx];
  textarea.rows = 2;
  textarea.addEventListener("input", autoGrow);
  textarea.addEventListener("focus", () => {
    inputFocused = true;
  });
  textarea.addEventListener("blur", () => {
    inputFocused = false;
  });
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      handleSend();
    }
  });
  if (state.pendingChatContext && !sending) {
    const ctx = state.pendingChatContext;
    textarea.value = `关于「${ctx.subjectTitle || ctx.subjectId}」，我想聊聊`;
    patchState({ pendingChatContext: null });
  }
  const sendBtn = document.createElement("button");
  sendBtn.className = "chat-send-btn";
  sendBtn.id = "chat-send";
  sendBtn.innerHTML = "📨";
  sendBtn.disabled = sending;
  sendBtn.addEventListener("click", handleSend);
  inputRow.appendChild(textarea);
  inputRow.appendChild(sendBtn);
  shell.appendChild(inputRow);
  $root.appendChild(shell);
  if (!userScrolledUp) {
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  }
  startPlaceholderCarousel();
  renderOverlay();
}
function autoGrow(e) {
  const el = e.target;
  el.style.height = "auto";
  el.style.height = Math.min(Math.max(el.scrollHeight, 60), 112) + "px";
}
function startPlaceholderCarousel() {
  if (placeholderTimer) clearInterval(placeholderTimer);
  placeholderTimer = setInterval(() => {
    if (inputFocused) return;
    placeholderIdx = (placeholderIdx + 1) % PLACEHOLDERS.length;
    const input = document.getElementById("chat-input");
    if (input && !input.value) {
      input.placeholder = PLACEHOLDERS[placeholderIdx];
    }
  }, 4e3);
}
async function handleSend() {
  const input = document.getElementById("chat-input");
  const text = input?.value?.trim();
  if (!text || sending) return;
  sending = true;
  const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  turns.push({ turn_id: turnId, message: text, response: null, status: "pending" });
  userScrolledUp = false;
  render();
  try {
    await startChatTurn({ turnId, ...chatSession(), message: text });
    pendingTurnId = turnId;
    pollForResponse();
  } catch {
    const t = turns.find((t2) => t2.turn_id === turnId);
    if (t) {
      t.status = "error";
      t.error = "发送失败";
    }
    sending = false;
    render();
  }
}
async function retryTurn(failedTurn) {
  if (sending) return;
  failedTurn.status = "pending";
  failedTurn.error = "";
  sending = true;
  render();
  try {
    await startChatTurn({
      turnId: failedTurn.turn_id,
      ...chatSession(failedTurn.scope || "chat"),
      message: failedTurn.message,
      subjectId: failedTurn.subject_id || "",
      subjectTitle: failedTurn.subject_title || ""
    });
    pendingTurnId = failedTurn.turn_id;
    pollForResponse();
  } catch {
    failedTurn.status = "error";
    failedTurn.error = "重试失败";
    sending = false;
    render();
  }
}
function pollForResponse() {
  if (!pendingTurnId) return;
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    try {
      const turn = normalizeChatTurn(await fetchChatTurn(pendingTurnId));
      const idx = turns.findIndex((t) => t.turn_id === pendingTurnId);
      if (idx >= 0) turns[idx] = turn;
      if (turn.status === "done" || turn.status === "completed" || turn.response) {
        pendingTurnId = null;
        sending = false;
        userScrolledUp = false;
        render();
        refreshAfterChatTurn();
      } else if (turn.status === "error" || turn.status === "failed") {
        pendingTurnId = null;
        sending = false;
        render();
      } else {
        render();
        pollForResponse();
      }
    } catch {
      pollForResponse();
    }
  }, 1500);
}
function renderOverlay() {
  let overlay = document.querySelector(".messages-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "messages-overlay";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) toggleMessages();
    });
    document.body.appendChild(overlay);
  }
  overlay.classList.toggle("open", overlayOpen);
  if (!overlayOpen) {
    overlay.innerHTML = "";
    return;
  }
  const panel = document.createElement("div");
  panel.className = "messages-panel";
  const header = document.createElement("div");
  header.className = "messages-header";
  header.innerHTML = `<span class="messages-title">消息</span>`;
  const closeBtn = document.createElement("button");
  closeBtn.className = "messages-close";
  closeBtn.textContent = "✕";
  closeBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    overlayOpen = false;
    renderOverlay();
  });
  header.appendChild(closeBtn);
  panel.appendChild(header);
  for (const n of notifications) {
    const domain = n.domain || n.title || "";
    const isAvoidance = (n.type || "") === "avoidance.probe";
    const isChallenge = !isAvoidance && isChallengeProbe(n);
    const actions = isAvoidance ? getAvoidanceProbeMessageActions() : getProbeMessageActions();
    const pending = pendingProbeAction(n.type, domain);
    const card = document.createElement("div");
    card.className = `message-card ${isAvoidance ? "is-avoidance-probe" : isChallenge ? "is-challenge-probe" : "is-interest-probe"}`;
    card.dataset.probeDomain = domain;
    card.setAttribute("aria-busy", pending ? "true" : "false");
    card.classList.toggle("is-processing", Boolean(pending));
    const prompt = isAvoidance ? "想少看这类，就确认这是雷点；如果阿B猜错了，点不是。" : isChallenge ? "这是挑战方向，会把口味往侧边推一点；想继续试探就点喜欢，不准就点不喜欢。" : "想继续探索这个方向，就点喜欢；不准就点不喜欢。";
    card.innerHTML = `
      <div class="message-card-type"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>${isAvoidance ? "避雷确认" : isChallenge ? "挑战探针" : "兴趣探测"}</div>
      <div class="message-card-prompt">${esc(prompt)}</div>
      <div class="message-card-title">${esc(domain)}</div>
      <div class="message-card-body">${esc(n.description || n.reason || n.message || "")}</div>
      <div class="message-card-actions">
        ${actions.map(
      (item) => `
          <button type="button" class="message-action-btn ${item.primary ? "primary" : "secondary"}" data-probe="${esc(item.action)}" data-probe-kind="${isAvoidance ? "avoidance" : "interest"}" data-domain="${esc(n.domain || "")}">${esc(item.label)}</button>
        `
    ).join("")}
      </div>`;
    for (const button of card.querySelectorAll("button")) {
      button.disabled = Boolean(pending);
    }
    panel.appendChild(card);
  }
  if (notifications.length === 0) {
    const emptyState = document.createElement("div");
    emptyState.className = "messages-empty-state";
    emptyState.innerHTML = `
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>
        <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>
      </svg>
      <span class="messages-empty-title">暂时没有新消息</span>
      <span class="messages-empty-subtitle">兴趣探测会在这里出现</span>`;
    panel.appendChild(emptyState);
  }
  overlay.innerHTML = "";
  overlay.appendChild(panel);
  for (const btn of panel.querySelectorAll("[data-probe]")) {
    btn.addEventListener("click", async () => {
      const domain = btn.dataset.domain;
      const action = btn.dataset.probe;
      const isAvoidance = btn.dataset.probeKind === "avoidance";
      const probeType = isAvoidance ? "avoidance.probe" : "interest.probe";
      const card = btn.closest(".message-card");
      if (action === "chat") {
        expandInlineChatOnCard(card, {
          scope: isAvoidance ? "avoidance_probe" : "probe",
          subjectId: domain,
          subjectTitle: domain,
          placeholder: isAvoidance ? `聊聊你为什么想避开「${domain}」…` : `聊聊你对「${domain}」的想法…`
        });
        return;
      }
      const key = probeNotificationKey(probeType, domain);
      if (!key || pendingProbeActions.has(key)) return;
      pendingProbeActions.set(key, { response: action });
      setProbeCardBusy(card, true);
      renderOverlay();
      try {
        await (isAvoidance ? respondToAvoidanceProbe(domain, action) : respondToProbe(domain, action));
        pendingProbeActions.delete(key);
        rememberHandledProbe(domain, probeType);
        notifications = removeProbeFromNotifications(notifications, domain, probeType);
        updateBadgeCount();
        renderOverlay();
      } catch {
        pendingProbeActions.delete(key);
        setProbeCardBusy(card, false);
        renderOverlay();
      }
    });
  }
  for (const btn of panel.querySelectorAll("[data-delight]")) {
    btn.addEventListener("click", async () => {
      const bvid = btn.dataset.bvid;
      const action = btn.dataset.delight;
      const title = btn.dataset.title || "";
      if (action === "chat") {
        const card = btn.closest(".message-card");
        expandInlineChatOnCard(card, {
          scope: "delight",
          subjectId: bvid,
          subjectTitle: title,
          placeholder: `聊聊你对「${title}」的想法…`
        });
        return;
      }
      const { apiResponse, permanent } = getDelightActionState(action);
      btn.disabled = true;
      if (apiResponse) {
        try {
          await respondToDelight(bvid, apiResponse, title);
        } catch {
        }
      }
      if (permanent) {
        markDelightSent(bvid).catch(() => {
        });
        delightMsgs = delightMsgs.filter((d) => d.bvid !== bvid);
        updateBadgeCount();
        renderOverlay();
      } else {
        delightMsgs = delightMsgs.map(
          (d) => d.bvid === bvid ? {
            ...d,
            state: action === "like" ? "liked" : action === "view" ? "viewed" : d.state,
            response_message: action === "like" ? "好，这类多来点。" : action === "view" ? "已打开，阿B 会把这次点击当成强信号。" : d.response_message
          } : d
        );
        updateBadgeCount();
        renderOverlay();
      }
      if (action === "view") {
        const item = normalizeDelightCandidate({ bvid, title });
        const url = buildContentUrl(item);
        if (url) openContentUrl(url);
      }
    });
  }
}
function updateBadgeCount() {
  const msgs = { notifications: [...notifications], delights: [] };
  patchState({ messages: msgs });
  setUnreadCount(notifications.length);
}
async function loadHistory() {
  try {
    const data = await fetchChatTurns({ ...chatSession(), limit: 50 });
    turns = Array.isArray(data?.items || data?.turns) ? (data.items || data.turns).map(normalizeChatTurn) : [];
    const last = turns[turns.length - 1];
    if (last && (last.status === "pending" || last.status === "processing")) {
      pendingTurnId = last.turn_id;
      sending = true;
      pollForResponse();
    }
  } catch {
  }
  render();
}
async function refreshAfterChatTurn() {
  try {
    const [profileResult, activityResult] = await Promise.allSettled([
      fetchProfileSummary({ limit: 5 }),
      fetchActivityFeed({ limit: 5 })
    ]);
    const next = {};
    if (profileResult.status === "fulfilled") {
      next.profile = normalizeProfileSummary(profileResult.value);
    }
    if (activityResult.status === "fulfilled") {
      next.activityFeed = normalizeActivityFeed(activityResult.value);
    }
    if (Object.keys(next).length > 0) patchState(next);
  } catch {
  }
}
export async function loadNotifications({ includeDelights = false } = {}) {
  try {
    const [notifData, probeData, avoidanceProbeData, delightData] = await Promise.all([
      fetchPendingNotifications().catch(() => ({})),
      fetchPendingProbes().catch(() => []),
      fetchPendingAvoidanceProbes().catch(() => []),
      includeDelights ? fetchDelightBatch().catch(() => []) : Promise.resolve(delightMsgs)
    ]);
    const probes = [
      ...Array.isArray(probeData) ? probeData.map((p) => ({ ...p, type: "interest.probe" })) : [],
      ...Array.isArray(avoidanceProbeData) ? avoidanceProbeData.map((p) => ({ ...p, type: "avoidance.probe" })) : []
    ];
    notifications = mergeProbeNotifications(probes, notifications);
    if (includeDelights) {
      delightMsgs = delightData;
    }
    updateBadgeCount();
  } catch {
  }
  if (overlayOpen) renderOverlay();
}
export function initChatView(root) {
  $root = root;
  if (!loaded) {
    loaded = true;
    loadNotifications();
  }
  loadHistory();
}
export async function toggleMessages() {
  overlayOpen = !overlayOpen;
  if (overlayOpen) {
    renderOverlay();
    await loadNotifications({ includeDelights: true });
    renderOverlay();
  } else {
    renderOverlay();
  }
}
export function updateBadge() {
  updateBadgeCount();
}
export function onStreamEvent(payload) {
  const type = payload?.type || payload?.event;
  if (type === "interest.probe" || type === "avoidance.probe") {
    const item = payload.data || payload;
    if (shouldDisplayProbeFromWebSocket(item, type)) {
      notifications = mergeProbeNotifications(notifications, [{ ...item, type }]);
      updateBadgeCount();
    }
  } else if (type === "delight.liked") {
    const data = payload.data || payload;
    const bvid = data?.bvid || data?.domain;
    if (bvid) {
      delightMsgs = delightMsgs.map(
        (d) => d.bvid === bvid ? { ...d, state: "liked", response_message: data?.message || "好，这类多来点。" } : d
      );
      if (overlayOpen) renderOverlay();
    }
  } else if (type === "delight.disliked") {
    const bvid = (payload.data || payload)?.bvid || (payload.data || payload)?.domain;
    if (bvid) {
      const before = delightMsgs.length;
      delightMsgs = delightMsgs.filter((d) => d.bvid !== bvid);
      if (delightMsgs.length !== before) {
        if (overlayOpen) renderOverlay();
      }
    }
  }
}
export async function startContextualChat({ scope, subjectId, subjectTitle, message }) {
  patchState({ pendingChatContext: { scope, subjectId, subjectTitle } });
  navigateToTab("chat");
  if (!message) return;
  const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  turns.push({
    turn_id: turnId,
    message,
    response: null,
    status: "pending",
    scope,
    subject_id: subjectId,
    subject_title: subjectTitle
  });
  sending = true;
  userScrolledUp = false;
  render();
  try {
    await startChatTurn({ turnId, ...chatSession(scope), subjectId, subjectTitle, message });
    pendingTurnId = turnId;
    pollForResponse();
  } catch {
    const t = turns.find((t2) => t2.turn_id === turnId);
    if (t) {
      t.status = "error";
      t.error = "发送失败";
    }
    sending = false;
    render();
  }
}
function expandInlineChatOnCard(card, { scope, subjectId, subjectTitle, placeholder }) {
  if (!card || card.querySelector(".inline-chat-area")) return;
  const actions = card.querySelector(".message-card-actions");
  if (actions) actions.style.display = "none";
  const chatArea = document.createElement("div");
  chatArea.className = "inline-chat-area";
  const input = document.createElement("textarea");
  input.className = "inline-chat-input";
  input.rows = 2;
  input.placeholder = placeholder || "聊聊你的想法…";
  const sendBtn = document.createElement("button");
  sendBtn.className = "inline-chat-send";
  sendBtn.textContent = "发送";
  async function doSend() {
    const message = input.value.trim();
    if (!message) return;
    sendBtn.disabled = true;
    input.disabled = true;
    const thinking = document.createElement("div");
    thinking.className = "inline-chat-thinking";
    thinking.textContent = "阿B 正在思考…";
    chatArea.appendChild(thinking);
    const turnId = `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const isProbeScope = scope === "probe" || scope === "avoidance_probe";
    const probeType = scope === "avoidance_probe" ? "avoidance.probe" : "interest.probe";
    if (isProbeScope) {
      rememberHandledProbe(subjectId, probeType);
    }
    try {
      const turn = await startChatTurn({
        turnId,
        ...chatSession(scope),
        subjectId,
        subjectTitle,
        message
      });
      const showReply = (t) => {
        thinking.remove();
        input.remove();
        sendBtn.remove();
        const replyEl = document.createElement("div");
        replyEl.className = "inline-chat-reply";
        replyEl.textContent = t.reply || t.response || "收到了，我会结合这个方向继续观察。";
        chatArea.appendChild(replyEl);
        setTimeout(() => {
          const domain = subjectId;
          if (isProbeScope) {
            notifications = removeProbeFromNotifications(notifications, domain, probeType);
          }
          updateBadgeCount();
          renderOverlay();
        }, 3500);
      };
      const settleTurn = (t) => {
        if (t.status === "failed") {
          thinking.remove();
          sendBtn.disabled = false;
          input.disabled = false;
          if (isProbeScope) {
            forgetHandledProbe(subjectId, probeType);
          }
          const errEl = document.createElement("div");
          errEl.className = "inline-chat-error";
          errEl.textContent = t.error || "刚刚没发出去，换个说法再试试。";
          chatArea.appendChild(errEl);
          return;
        }
        if (t.status === "completed") showReply(t);
      };
      if (turn.status === "completed" || turn.status === "failed") {
        settleTurn(turn);
      } else {
        const poll = async () => {
          try {
            const t = await fetchChatTurn(turnId);
            if (t.status === "completed" || t.status === "failed") {
              settleTurn(t);
            } else {
              setTimeout(poll, 1500);
            }
          } catch {
            setTimeout(poll, 2e3);
          }
        };
        setTimeout(poll, 1500);
      }
    } catch {
      thinking.remove();
      sendBtn.disabled = false;
      input.disabled = false;
      if (isProbeScope) {
        forgetHandledProbe(subjectId, probeType);
      }
      const errEl = document.createElement("div");
      errEl.className = "inline-chat-error";
      errEl.textContent = "后台正忙，等一下再聊。";
      chatArea.appendChild(errEl);
      setTimeout(() => errEl.remove(), 3e3);
    }
  }
  sendBtn.addEventListener("click", doSend);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  });
  chatArea.append(input, sendBtn);
  card.appendChild(chatArea);
  input.focus();
}
//# sourceMappingURL=chat.js.map
