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
    // A revise is not a rejection — the user corrected the wording and
    // accepted it, and a derived hypothesis was recorded.
    revised: "已按你的修正记下",
    discussing: "正在聊这条",
    deferred: "已稍后再聊",
    processing: "正在处理，以后端结算为准",
    retryable_error: "处理结果暂未同步，可刷新或重试",
  };
  const CARD_STATES = new Set([
    "pending",
    "confirmed",
    "rejected",
    "revised",
    "discussing",
    "deferred",
    "processing",
    "retryable_error",
  ]);
  const TERMINAL_CARD_STATES = new Set(["confirmed", "rejected", "revised", "deferred"]);
  const POLL_TERMINAL_CARD_STATES = new Set([
    "confirmed",
    "rejected",
    "revised",
    "deferred",
    "discussing",
  ]);
  const CARD_ACTION_POLL_BACKOFF_MS = Object.freeze([1_000, 2_000, 5_000]);
  // Calibrated for several local 1/2/5s publication reads. This bounds a
  // non-durable restart spinner; it deliberately does not wait for a 300s
  // provider timeout or introduce a durable job table.
  const CARD_ACTION_POLL_DEADLINE_MS = 30_000;
  const PENDING_OPEN_RETRY_BACKOFF_MS = Object.freeze([1_000, 2_000, 3_000, 5_000]);
  // Match the backend's safe hot-reload drain window. The page/popup abort
  // signal still stops retries immediately when its owner goes away.
  const PENDING_OPEN_RETRY_DEADLINE_MS = 25 * 60_000;
  const DIALOGUE_SCOPES = new Set(["chat", "hypothesis", "confusion"]);
  // Backend refuses settlement when another card owns the dialogue anchor.
  // These outcomes are honest failures — never fall back to the optimistic
  // terminal state, or the UI will claim "已确认" while nothing was written.
  const ANCHOR_REFUSAL_OUTCOMES = new Set(["stale_anchor", "anchor_dependency_failed"]);

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

  function pendingOpenErrorCode(error) {
    return text(error?.details?.detail?.code || error?.details?.code).toLowerCase();
  }

  async function executePendingConfirmationOpen(ref, options = {}) {
    const {
      request,
      session = "popup",
      signal,
      onWaiting,
      sleep = waitForPoll,
      now = () => Date.now(),
      deadlineMs = PENDING_OPEN_RETRY_DEADLINE_MS,
    } = options;
    if (typeof request !== "function") {
      throw new TypeError("executePendingConfirmationOpen requires request");
    }
    const path = pendingConfirmationOpenPath(ref);
    const startedAt = now();
    let attempt = 0;
    while (true) {
      if (signal?.aborted) throw signal.reason || abortError();
      try {
        return await request(path, { session }, { signal });
      } catch (error) {
        if (Number(error?.status) !== 503 || pendingOpenErrorCode(error) !== "dialogue_busy") {
          throw error;
        }
        if (now() - startedAt >= deadlineMs) throw error;
        if (typeof onWaiting === "function") {
          onWaiting({
            attempt,
            message: text(error?.details?.detail?.message) || "后台正在整理上一段对话",
          });
        }
        const delay = PENDING_OPEN_RETRY_BACKOFF_MS[
          Math.min(attempt, PENDING_OPEN_RETRY_BACKOFF_MS.length - 1)
        ];
        attempt += 1;
        await sleep(delay, signal);
      }
    }
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

  function abortError() {
    if (typeof DOMException === "function") {
      return new DOMException("Card action polling aborted", "AbortError");
    }
    const error = new Error("Card action polling aborted");
    error.name = "AbortError";
    return error;
  }

  function isAbort(error, signal) {
    return Boolean(signal?.aborted) || error?.name === "AbortError";
  }

  function waitForPoll(milliseconds, signal) {
    if (signal?.aborted) return Promise.reject(signal.reason || abortError());
    return new Promise((resolve, reject) => {
      let timeoutId = null;
      const onAbort = () => {
        if (timeoutId !== null) clearTimeout(timeoutId);
        reject(signal.reason || abortError());
      };
      timeoutId = setTimeout(() => {
        if (signal) signal.removeEventListener("abort", onAbort);
        resolve();
      }, milliseconds);
      if (signal) {
        signal.addEventListener("abort", onAbort, { once: true });
        Promise.resolve().then(() => {
          if (signal.aborted) onAbort();
        });
      }
    });
  }

  function retryableCardResult(turn, action, reason, onUpdate) {
    const retryable = withCardState(turn, "retryable_error");
    retryable.payload.retry_action = text(action).toLowerCase();
    const response = {
      ok: false,
      outcome: "retryable_error",
      state: "retryable_error",
      reason,
    };
    onUpdate(retryable, response);
    return { turn: retryable, response };
  }

  async function pollProcessingCard(original, action, initialResponse, options) {
    if (typeof options.fetchTurn !== "function") {
      return retryableCardResult(
        original,
        action,
        "poll_unavailable",
        options.onUpdate,
      );
    }
    const now = typeof options.now === "function" ? options.now : Date.now;
    const sleep = typeof options.sleep === "function" ? options.sleep : waitForPoll;
    const startedAt = now();
    let backoffIndex = 0;
    let latest = cloneTurn(original);

    while (true) {
      if (options.signal?.aborted) {
        return retryableCardResult(latest, action, "aborted", options.onUpdate);
      }
      const remaining = CARD_ACTION_POLL_DEADLINE_MS - Math.max(0, now() - startedAt);
      if (remaining <= 0) {
        return retryableCardResult(latest, action, "deadline", options.onUpdate);
      }
      const configuredDelay = CARD_ACTION_POLL_BACKOFF_MS[
        Math.min(backoffIndex, CARD_ACTION_POLL_BACKOFF_MS.length - 1)
      ];
      const delay = Math.min(configuredDelay, remaining);
      try {
        await sleep(delay, options.signal);
      } catch (error) {
        if (isAbort(error, options.signal)) {
          return retryableCardResult(latest, action, "aborted", options.onUpdate);
        }
        return retryableCardResult(latest, action, "poll_failed", options.onUpdate);
      }
      if (options.signal?.aborted) {
        return retryableCardResult(latest, action, "aborted", options.onUpdate);
      }
      if (now() - startedAt >= CARD_ACTION_POLL_DEADLINE_MS) {
        return retryableCardResult(latest, action, "deadline", options.onUpdate);
      }
      try {
        const fetchTimeoutMs = Math.max(
          1,
          CARD_ACTION_POLL_DEADLINE_MS - Math.max(0, now() - startedAt),
        );
        latest = cloneTurn(
          await options.fetchTurn(text(original.turn_id), {
            signal: options.signal,
            timeoutMs: fetchTimeoutMs,
          }),
        );
      } catch (error) {
        if (isAbort(error, options.signal)) {
          return retryableCardResult(latest, action, "aborted", options.onUpdate);
        }
        backoffIndex += 1;
        continue;
      }
      const durableState = normalizedCardState(latest);
      if (POLL_TERMINAL_CARD_STATES.has(durableState)) {
        const response = {
          ...initialResponse,
          ok: true,
          outcome: "settled",
          state: durableState,
          verdict: durableState,
        };
        options.onUpdate(latest, response);
        return { turn: latest, response };
      }
      const processing = withCardState(latest, "processing");
      options.onUpdate(processing, initialResponse);
      backoffIndex += 1;
    }
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
      const outcome = text(response?.outcome).toLowerCase();
      if (outcome === "processing" || responseCardState(response, "") === "processing") {
        const processing = withCardState(original, "processing");
        options.onUpdate(processing, response);
        return await pollProcessingCard(original, action, response, options);
      }
      // Anchor owned by another card: backend wrote nothing. Reuse the
      // retryable path so the optimistic "confirmed" flash is rolled back.
      if (ANCHOR_REFUSAL_OUTCOMES.has(outcome)) {
        return retryableCardResult(original, action, outcome, options.onUpdate);
      }
      // `already_settled` is authoritative, including the opposite verdict.
      // Replacing the optimistic state here is the cross-screen rollback path.
      const settled = withCardState(
        optimistic,
        responseCardState(response, normalizedCardState(optimistic)),
      );
      options.onUpdate(settled, response);
      return { turn: settled, response };
    } catch (error) {
      if (isAbort(error, options.signal)) {
        return retryableCardResult(original, action, "aborted", options.onUpdate);
      }
      options.onUpdate(original, { outcome: "error", error });
      throw error;
    }
  }

  function isOpaqueEvidenceId(value) {
    const item = text(value);
    if (!item) return false;
    if (/^\d{1,24}$/.test(item)) return true;
    if (/^[0-9a-f]{8,64}$/i.test(item)) return true;
    if (/^[0-9a-f]{8}(?:-[0-9a-f]{4}){2,4}-[0-9a-f]{8,12}$/i.test(item)) return true;
    if (/^(?:BV[0-9A-Za-z]{10,}|av\d+|cv\d+)$/i.test(item)) return true;
    if (/^(?:event|evt|note|content|awareness|hypothesis|confusion|insight|turn)[#:/_-][A-Za-z0-9._:/-]+$/i.test(item)) {
      return true;
    }
    return (
      item.length >= 20 &&
      !/^https?:\/\//i.test(item) &&
      /^[A-Za-z0-9._:/+-]+$/.test(item)
    );
  }

  function evidenceRefs(payload) {
    if (!Array.isArray(payload?.evidence_refs)) return [];
    return [
      ...new Set(
        payload.evidence_refs
          .map((item) => (typeof item === "string" || typeof item === "number" ? text(item) : ""))
          .filter((item) => item && !isOpaqueEvidenceId(item)),
      ),
    ];
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
    const failed = ["error", "failed"].includes(text(turn?.status).toLowerCase());
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
    executePendingConfirmationOpen,
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
