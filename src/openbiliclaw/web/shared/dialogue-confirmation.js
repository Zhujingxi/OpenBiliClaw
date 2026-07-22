(function installDialogueConfirmation(global) {
  "use strict";

  const CARD_ACTIONS = ["confirm", "reject", "discuss", "defer"];
  const CARD_ACTION_LABELS = {
    confirm: "准",
    reject: "不准",
    discuss: "聊聊",
    defer: "稍后",
  };
  const CARD_STATE_LABELS = {
    confirmed: "已确认",
    rejected: "已标记不准",
    discussing: "正在聊这条",
    deferred: "已稍后再聊",
    processing: "正在处理，以后端结算为准",
  };
  const CARD_STATES = new Set([
    "pending",
    "confirmed",
    "rejected",
    "discussing",
    "deferred",
    "processing",
  ]);
  const TERMINAL_CARD_STATES = new Set(["confirmed", "rejected", "deferred"]);
  const DIALOGUE_SCOPES = new Set(["chat", "hypothesis", "confusion"]);

  function isRecord(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function text(value) {
    return typeof value === "string" ? value.trim() : String(value ?? "").trim();
  }

  function escapeHtml(value) {
    return text(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function cloneTurn(turn) {
    const source = isRecord(turn) ? turn : {};
    const payload = isRecord(source.payload) ? { ...source.payload } : {};
    if (Array.isArray(payload.actions)) payload.actions = [...payload.actions];
    if (Array.isArray(payload.evidence_refs)) payload.evidence_refs = [...payload.evidence_refs];
    return { ...source, payload };
  }

  function normalizedCardState(turn) {
    const state = text(isRecord(turn?.payload) ? turn.payload.state : "").toLowerCase();
    return CARD_STATES.has(state) ? state : "pending";
  }

  function withCardState(turn, state) {
    const next = cloneTurn(turn);
    next.payload.state = CARD_STATES.has(state) ? state : "pending";
    return next;
  }

  function isCardTurn(turn) {
    return isRecord(turn?.payload) && turn.payload.type === "card";
  }

  function isQuestionTurn(turn) {
    return isRecord(turn?.payload) && turn.payload.type === "question";
  }

  function cardActionPath(turnId) {
    return `/chat/cards/${encodeURIComponent(text(turnId))}/action`;
  }

  function pendingConfirmationOpenPath(ref) {
    return `/chat/pending-confirmations/${encodeURIComponent(text(ref))}/open`;
  }

  function applyOptimisticCardAction(turn, action) {
    const normalizedAction = text(action).toLowerCase();
    if (!CARD_ACTIONS.includes(normalizedAction)) {
      throw new TypeError(`Unsupported card action: ${normalizedAction}`);
    }
    const state = normalizedCardState(turn);
    if (TERMINAL_CARD_STATES.has(state)) return cloneTurn(turn);
    const nextState = {
      confirm: "confirmed",
      reject: "rejected",
      discuss: "discussing",
      defer: "deferred",
    }[normalizedAction];
    return withCardState(turn, nextState);
  }

  function responseCardState(response, fallbackState) {
    const state = text(response?.state || response?.verdict).toLowerCase();
    return CARD_STATES.has(state) ? state : fallbackState;
  }

  async function executeCardAction(turn, action, options = {}) {
    if (typeof options.request !== "function" || typeof options.onUpdate !== "function") {
      throw new TypeError("card action requires request and onUpdate callbacks");
    }
    const original = cloneTurn(turn);
    const optimistic = applyOptimisticCardAction(original, action);
    options.onUpdate(optimistic);
    try {
      const response = await options.request(cardActionPath(original.turn_id), {
        action: text(action).toLowerCase(),
      });
      // `already_settled` is authoritative, including the opposite verdict.
      // Replacing the optimistic state here is the cross-screen rollback path.
      const settled = withCardState(
        optimistic,
        responseCardState(response, normalizedCardState(optimistic)),
      );
      options.onUpdate(settled, response);
      return { turn: settled, response };
    } catch (error) {
      options.onUpdate(original, { outcome: "error", error });
      throw error;
    }
  }

  function evidenceRefs(payload) {
    if (!Array.isArray(payload?.evidence_refs)) return [];
    return payload.evidence_refs.map(text).filter(Boolean);
  }

  function evidenceMarkup(payload) {
    const evidence = evidenceRefs(payload);
    if (!evidence.length) return "";
    return `<details class="dialogue-evidence"><summary>依据（${evidence.length}）</summary><ul>${evidence
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("")}</ul></details>`;
  }

  function turnAttributes(turn, extraClass = "") {
    const turnId = escapeHtml(turn?.turn_id);
    return `class="${extraClass}" data-dialogue-turn-id="${turnId}"`;
  }

  function textBubbleMarkup(role, content, turn, part, surface, extraClass = "") {
    const cleanContent = text(content);
    if (!cleanContent) return "";
    const isUser = role === "user";
    const turnId = escapeHtml(turn?.turn_id);
    const safePart = escapeHtml(part);
    if (surface === "desktop") {
      return `<div class="chat-bubble ${isUser ? "user" : "agent"}${extraClass ? ` ${extraClass}` : ""}" data-dialogue-turn-id="${turnId}" data-part="${safePart}">${escapeHtml(cleanContent)}</div>`;
    }
    return `<div class="chat-message${isUser ? " user" : ""}${extraClass ? ` ${extraClass}` : ""}" data-dialogue-turn-id="${turnId}" data-part="${safePart}"><span class="chat-role">${isUser ? "你" : "助手"}</span><p class="chat-content">${escapeHtml(cleanContent)}</p></div>`;
  }

  function cardActions(payload, state) {
    if (TERMINAL_CARD_STATES.has(state)) return "";
    const configured = Array.isArray(payload?.actions)
      ? payload.actions.map((item) => text(item).toLowerCase()).filter((item) => CARD_ACTIONS.includes(item))
      : [];
    const actions = configured.length ? [...new Set(configured)] : CARD_ACTIONS;
    return `<div class="dialogue-card-actions" aria-label="确认这条猜测">${actions
      .map((action) => {
        const disabled = state === "processing" || (state === "discussing" && action === "discuss");
        return `<button type="button" class="dialogue-card-action is-${action}" data-card-action="${action}"${disabled ? " disabled" : ""}>${escapeHtml(CARD_ACTION_LABELS[action])}</button>`;
      })
      .join("")}</div>`;
  }

  function renderCardMarkup(turn) {
    const payload = turn.payload;
    const state = normalizedCardState(turn);
    const title = text(payload.title) || text(turn.subject_title) || text(turn.message) || "这条猜测";
    const stateLabel = CARD_STATE_LABELS[state] || "";
    return `<article ${turnAttributes(turn, "dialogue-card")} data-card-state="${state}"><p class="dialogue-card-kicker">阿B 的猜测</p><h3 class="dialogue-card-title">${escapeHtml(title)}</h3>${evidenceMarkup(payload)}${stateLabel ? `<p class="dialogue-card-state" role="status">${escapeHtml(stateLabel)}</p>` : ""}${cardActions(payload, state)}</article>`;
  }

  function renderQuestionMarkup(turn, surface) {
    const payload = turn.payload;
    const reply = text(turn.reply) || text(payload.title) || text(turn.subject_title);
    const bubble = textBubbleMarkup("agent", reply, turn, "assistant", surface, "dialogue-question");
    if (!bubble || !evidenceRefs(payload).length) return bubble;
    return `<div ${turnAttributes(turn, "dialogue-question-shell")}>${bubble}${evidenceMarkup(payload)}</div>`;
  }

  function renderTextTurnMarkup(turn, surface) {
    const failed = text(turn?.status).toLowerCase() === "failed";
    const reply = failed
      ? text(turn?.error) || "这句还没发出去，稍后再试。"
      : text(turn?.reply) || text(turn?.assistant_message);
    return [
      textBubbleMarkup("user", turn?.message || turn?.user_message, turn, "user", surface),
      textBubbleMarkup("agent", reply, turn, "assistant", surface),
    ].join("");
  }

  function renderTurnMarkup(turn, options = {}) {
    const surface = options.surface === "desktop" ? "desktop" : "popup";
    if (isCardTurn(turn)) return renderCardMarkup(turn);
    if (isQuestionTurn(turn)) return renderQuestionMarkup(turn, surface);
    return renderTextTurnMarkup(turn, surface);
  }

  function renderPendingListMarkup(items) {
    const list = Array.isArray(items) ? items.filter(isRecord) : [];
    if (!list.length) {
      return '<p class="dialogue-pending-empty">暂时没有待聊的确认。</p>';
    }
    return list
      .map((item) => {
        const kind = text(item.kind) === "confusion" ? "有点疑惑" : "想确认";
        const confidence = Number(item.confidence);
        const confidenceText = Number.isFinite(confidence)
          ? `<span class="dialogue-pending-confidence">${Math.round(Math.max(0, Math.min(1, confidence)) * 100)}%</span>`
          : "";
        return `<article class="dialogue-pending-item" data-confirmation-kind="${escapeHtml(item.kind)}"><div class="dialogue-pending-copy"><span class="dialogue-pending-kind">${kind}</span><strong>${escapeHtml(item.title || "这件事")}</strong>${confidenceText}</div><button type="button" data-confirmation-ref="${escapeHtml(item.ref)}">打开</button></article>`;
      })
      .join("");
  }

  function selectDialogueTurns(items) {
    const list = Array.isArray(items) ? items : [];
    return list
      .map((turn, index) => ({ turn, index }))
      .filter(({ turn }) => isRecord(turn) && DIALOGUE_SCOPES.has(text(turn.scope)))
      .sort((left, right) => {
        const byTime = text(left.turn.created_at).localeCompare(text(right.turn.created_at));
        return byTime || left.index - right.index;
      })
      .map(({ turn }) => turn);
  }

  const api = {
    CARD_ACTIONS,
    applyOptimisticCardAction,
    cardActionPath,
    executeCardAction,
    isCardTurn,
    isQuestionTurn,
    pendingConfirmationOpenPath,
    renderPendingListMarkup,
    renderTurnMarkup,
    selectDialogueTurns,
  };
  global.OpenBiliClawDialogueConfirmation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
