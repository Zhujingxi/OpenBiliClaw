import assert from "node:assert/strict";
import test from "node:test";

await import("../../src/openbiliclaw/web/shared/dialogue-confirmation.js");

const dialogue = (globalThis as typeof globalThis & {
  OpenBiliClawDialogueConfirmation?: {
    applyOptimisticCardAction: (turn: Record<string, unknown>, action: string) => Record<string, unknown>;
    cardActionPath: (turnId: string) => string;
    executeCardAction: (
      turn: Record<string, unknown>,
      action: string,
      options: {
        request: (path: string, body: Record<string, string>) => Promise<Record<string, unknown>>;
        onUpdate: (turn: Record<string, unknown>) => void;
        fetchTurn?: (
          turnId: string,
          options?: { signal?: AbortSignal; timeoutMs?: number },
        ) => Promise<Record<string, unknown>>;
        sleep?: (milliseconds: number, signal?: AbortSignal) => Promise<void>;
        now?: () => number;
        signal?: AbortSignal;
      },
    ) => Promise<{ turn: Record<string, unknown>; response: Record<string, unknown> }>;
    executePendingConfirmationOpen: (
      ref: string,
      options: {
        session?: string;
        request: (
          path: string,
          body: Record<string, string>,
          options?: { signal?: AbortSignal },
        ) => Promise<Record<string, unknown>>;
        onWaiting?: (state: { attempt: number; message: string }) => void;
        sleep?: (milliseconds: number, signal?: AbortSignal) => Promise<void>;
        now?: () => number;
        deadlineMs?: number;
      },
    ) => Promise<Record<string, unknown>>;
    pendingConfirmationOpenPath: (ref: string) => string;
    renderPendingListMarkup: (items: Array<Record<string, unknown>>) => string;
    renderTurnMarkup: (
      turn: Record<string, unknown>,
      options?: { surface?: "popup" | "desktop" },
    ) => string;
    selectDialogueTurns: (items: Array<Record<string, unknown>>) => Array<Record<string, unknown>>;
  };
}).OpenBiliClawDialogueConfirmation;

assert.ok(dialogue, "shared dialogue confirmation helper should install its browser global");

function cardTurn(state = "pending") {
  return {
    turn_id: "turn/card 1",
    session: "popup",
    scope: "hypothesis",
    message: "阿b 的猜测",
    reply: "",
    status: "completed",
    payload: {
      type: "card",
      kind: "hypothesis",
      ref: "ref/alpha",
      title: "你更喜欢把复杂问题拆开讲清楚",
      evidence_refs: ["完整看完了三条长视频", "收藏了系统分析内容"],
      actions: ["confirm", "reject", "discuss", "defer"],
      state,
    },
    created_at: "2026-07-22T08:00:00Z",
  };
}

test("payload.type=card renders four semantic actions and expandable evidence", () => {
  const markup = dialogue!.renderTurnMarkup(cardTurn(), { surface: "popup" });

  assert.match(markup, /class="dialogue-card/);
  assert.match(markup, /你更喜欢把复杂问题拆开讲清楚/);
  assert.equal((markup.match(/data-card-action=/g) ?? []).length, 4);
  for (const action of ["confirm", "reject", "discuss", "defer"]) {
    assert.match(markup, new RegExp(`data-card-action="${action}"`));
  }
  assert.match(markup, /<details[^>]*class="dialogue-evidence"/);
  assert.match(markup, /<summary>依据（2）<\/summary>/);
  assert.match(markup, /完整看完了三条长视频/);
});

test("terminal card state replaces actions in place", () => {
  const markup = dialogue!.renderTurnMarkup(cardTurn("confirmed"), { surface: "popup" });

  assert.match(markup, /data-card-state="confirmed"/);
  assert.match(markup, /已确认/);
  assert.doesNotMatch(markup, /data-card-action=/);
});

test("turns without structured payload keep the text conversation fallback", () => {
  const markup = dialogue!.renderTurnMarkup(
    {
      turn_id: "plain-1",
      scope: "chat",
      message: "我最近更想看深度访谈",
      reply: "记下了，我们继续沿着这个方向聊。",
      status: "completed",
      payload: {},
    },
    { surface: "popup" },
  );

  assert.match(markup, /chat-message user/);
  assert.match(markup, /我最近更想看深度访谈/);
  assert.match(markup, /记下了，我们继续沿着这个方向聊。/);
  assert.doesNotMatch(markup, /dialogue-card/);
});

test("confusion question enters the flow as a pure assistant turn", () => {
  const markup = dialogue!.renderTurnMarkup(
    {
      turn_id: "question-1",
      scope: "confusion",
      message: "",
      reply: "我对这次收藏后马上退出有点没看懂，你愿意说说实际情况吗？",
      status: "completed",
      payload: { type: "question", kind: "confusion", ref: "7", state: "clarifying" },
    },
    { surface: "desktop" },
  );

  assert.match(markup, /dialogue-question/);
  assert.match(markup, /我对这次收藏后马上退出有点没看懂/);
  assert.doesNotMatch(markup, /chat-bubble user/);
});

test("card action posts to the encoded endpoint, updates optimistically, then rolls back to already-settled verdict", async () => {
  const updates: string[] = [];
  const requests: Array<{ path: string; body: Record<string, string> }> = [];
  let fetchCalls = 0;

  const result = await dialogue!.executeCardAction(cardTurn(), "confirm", {
    async request(path, body) {
      requests.push({ path, body });
      return { ok: true, outcome: "already_settled", state: "rejected", verdict: "rejected" };
    },
    async fetchTurn() {
      fetchCalls += 1;
      return cardTurn("confirmed");
    },
    onUpdate(turn) {
      updates.push(String((turn.payload as Record<string, unknown>)?.state ?? ""));
    },
  });

  assert.deepEqual(requests, [
    { path: "/chat/cards/turn%2Fcard%201/action", body: { action: "confirm" } },
  ]);
  assert.deepEqual(updates, ["confirmed", "rejected"]);
  assert.equal(fetchCalls, 0, "a synchronous 200 result must not start publication polling");
  assert.equal((result.turn.payload as Record<string, unknown>).state, "rejected");
});

test("processing response polls the durable turn with 1/2/5 backoff and keeps remote pending local-processing", async () => {
  const updates: string[] = [];
  const delays: number[] = [];
  const durablePending = cardTurn("pending");
  let fakeNow = 0;
  let fetchCalls = 0;

  const result = await dialogue!.executeCardAction(cardTurn(), "confirm", {
    async request() {
      return { ok: false, outcome: "processing", state: "processing" };
    },
    async fetchTurn() {
      fetchCalls += 1;
      return fetchCalls === 1 ? durablePending : cardTurn("confirmed");
    },
    async sleep(milliseconds) {
      delays.push(milliseconds);
      fakeNow += milliseconds;
    },
    now: () => fakeNow,
    onUpdate(turn) {
      updates.push(String((turn.payload as Record<string, unknown>)?.state ?? ""));
    },
  });

  assert.deepEqual(delays, [1_000, 2_000]);
  assert.deepEqual(updates, ["confirmed", "processing", "processing", "confirmed"]);
  assert.equal((durablePending.payload as Record<string, unknown>).state, "pending");
  assert.equal((result.turn.payload as Record<string, unknown>).state, "confirmed");
});

test("processing poll stops on every authoritative card terminal state", async () => {
  for (const terminal of ["confirmed", "rejected", "deferred", "discussing"]) {
    let fakeNow = 0;
    let fetchCalls = 0;
    const result = await dialogue!.executeCardAction(cardTurn(), "confirm", {
      async request() {
        return { ok: false, outcome: "processing", state: "processing" };
      },
      async fetchTurn() {
        fetchCalls += 1;
        return cardTurn(terminal);
      },
      async sleep(milliseconds) {
        fakeNow += milliseconds;
      },
      now: () => fakeNow,
      onUpdate() {},
    });

    assert.equal(fetchCalls, 1, `${terminal} should stop after its first durable read`);
    assert.equal((result.turn.payload as Record<string, unknown>).state, terminal);
  }
});

test("processing poll reaches the calibrated 30s deadline as local retryable_error without mutating durable pending", async () => {
  const delays: number[] = [];
  const durablePending = cardTurn("pending");
  let fakeNow = 0;
  let fetchCalls = 0;

  const result = await dialogue!.executeCardAction(cardTurn(), "confirm", {
    async request() {
      return { ok: false, outcome: "processing", state: "processing" };
    },
    async fetchTurn() {
      fetchCalls += 1;
      return durablePending;
    },
    async sleep(milliseconds) {
      delays.push(milliseconds);
      fakeNow += milliseconds;
    },
    now: () => fakeNow,
    onUpdate() {},
  });

  assert.equal(fakeNow, 30_000);
  assert.equal(delays.reduce((sum, delay) => sum + delay, 0), 30_000);
  assert.ok(fetchCalls > 1);
  assert.equal((durablePending.payload as Record<string, unknown>).state, "pending");
  assert.equal((result.turn.payload as Record<string, unknown>).state, "retryable_error");
  assert.equal(result.response.outcome, "retryable_error");
  const markup = dialogue!.renderTurnMarkup(result.turn, { surface: "popup" });
  assert.match(markup, /data-card-state="retryable_error"/);
  assert.match(markup, /data-card-action="confirm"(?! disabled)/);
});

test("page abort stops processing polling and leaves a refresh-or-retry state", async () => {
  const controller = new AbortController();
  const updates: string[] = [];
  let fetchCalls = 0;

  const result = await dialogue!.executeCardAction(cardTurn(), "reject", {
    async request() {
      return { ok: false, outcome: "processing", state: "processing" };
    },
    async fetchTurn() {
      fetchCalls += 1;
      return cardTurn("pending");
    },
    async sleep() {
      controller.abort();
    },
    signal: controller.signal,
    onUpdate(turn) {
      updates.push(String((turn.payload as Record<string, unknown>)?.state ?? ""));
    },
  });

  assert.equal(fetchCalls, 0);
  assert.deepEqual(updates, ["rejected", "processing", "retryable_error"]);
  assert.equal((result.turn.payload as Record<string, unknown>).state, "retryable_error");
  assert.equal(result.response.outcome, "retryable_error");
  assert.equal(result.response.reason, "aborted");
});

test("failed card action rolls the optimistic state back to the durable original", async () => {
  const updates: string[] = [];

  await assert.rejects(
    dialogue!.executeCardAction(cardTurn(), "confirm", {
      async request() {
        throw new Error("offline");
      },
      onUpdate(turn) {
        updates.push(String((turn.payload as Record<string, unknown>)?.state ?? ""));
      },
    }),
    /offline/,
  );

  assert.deepEqual(updates, ["confirmed", "pending"]);
});

test("stale_anchor refusal never presents confirmed; final markup stays retryable", async () => {
  // Real backend body when card A is confirmed while card B owns the anchor.
  // Regression: responseCardState("stale") fell through to optimistic
  // "confirmed", so both toast and card label lied that settlement worked.
  const realBackendResponse = {
    outcome: "stale_anchor",
    state: "stale",
    settlement_ref: "ref/alpha",
    settlement_verdict: "",
  };
  const updateSequence: Array<Record<string, unknown>> = [];
  let lastTurn: Record<string, unknown> | null = null;

  const result = await dialogue!.executeCardAction(cardTurn("pending"), "confirm", {
    async request() {
      return realBackendResponse;
    },
    onUpdate(turn, response) {
      lastTurn = turn;
      updateSequence.push({
        state: String((turn.payload as Record<string, unknown>)?.state ?? ""),
        ...(response ? { outcome: String(response.outcome ?? "") } : {}),
      });
    },
  });

  assert.deepEqual(updateSequence, [
    { state: "confirmed" },
    { state: "retryable_error", outcome: "retryable_error" },
  ]);
  assert.equal(result.response.outcome, "retryable_error");
  assert.equal(result.response.reason, "stale_anchor");
  assert.equal((result.turn.payload as Record<string, unknown>).state, "retryable_error");
  assert.notEqual((result.turn.payload as Record<string, unknown>).state, "confirmed");

  const markup = dialogue!.renderTurnMarkup(lastTurn!, { surface: "popup" });
  assert.match(markup, /data-card-state="retryable_error"/);
  assert.match(markup, /处理结果暂未同步/);
  assert.doesNotMatch(markup, /已确认/);
  assert.doesNotMatch(markup, /data-card-state="confirmed"/);
});

test("anchor_dependency_failed refusal also rolls back to retryable_error", async () => {
  const updates: string[] = [];
  const result = await dialogue!.executeCardAction(cardTurn("pending"), "reject", {
    async request() {
      return {
        outcome: "anchor_dependency_failed",
        state: "stale",
        settlement_ref: "ref/alpha",
        settlement_verdict: "",
      };
    },
    onUpdate(turn) {
      updates.push(String((turn.payload as Record<string, unknown>)?.state ?? ""));
    },
  });

  assert.deepEqual(updates, ["rejected", "retryable_error"]);
  assert.equal(result.response.outcome, "retryable_error");
  assert.equal(result.response.reason, "anchor_dependency_failed");
  const markup = dialogue!.renderTurnMarkup(result.turn, { surface: "desktop" });
  assert.match(markup, /data-card-state="retryable_error"/);
  assert.doesNotMatch(markup, /已标记不准/);
});

test("pending list markup opens an encoded ref and selected flow excludes probe/delight turns", () => {
  const markup = dialogue!.renderPendingListMarkup([
    { kind: "hypothesis", ref: "hash/8", title: "喜欢系统分析", confidence: 0.81 },
    { kind: "confusion", ref: "12", title: "收藏后马上退出", confidence: 0.72 },
  ]);

  assert.match(markup, /喜欢系统分析/);
  assert.match(markup, /收藏后马上退出/);
  assert.equal((markup.match(/data-confirmation-ref=/g) ?? []).length, 2);
  assert.equal(
    dialogue!.pendingConfirmationOpenPath("hash/8"),
    "/chat/pending-confirmations/hash%2F8/open",
  );

  const selected = dialogue!.selectDialogueTurns([
    { turn_id: "probe", scope: "probe", created_at: "2026-07-22T08:00:00Z" },
    { turn_id: "card", scope: "hypothesis", created_at: "2026-07-22T08:02:00Z" },
    { turn_id: "chat", scope: "chat", created_at: "2026-07-22T08:01:00Z" },
    { turn_id: "question", scope: "confusion", created_at: "2026-07-22T08:03:00Z" },
    { turn_id: "delight", scope: "delight", created_at: "2026-07-22T08:04:00Z" },
  ]);
  assert.deepEqual(selected.map((turn) => turn.turn_id), ["chat", "card", "question"]);
});

test("pending open retries only the explicit dialogue-busy response", async () => {
  const calls: string[] = [];
  const waits: string[] = [];
  const delays: number[] = [];
  const turn = await dialogue!.executePendingConfirmationOpen("12", {
    session: "webui",
    async request(path, body) {
      calls.push(`${path}:${body.session}`);
      if (calls.length === 1) {
        const error = new Error("busy") as Error & {
          status: number;
          details: Record<string, unknown>;
        };
        error.status = 503;
        error.details = {
          detail: { code: "dialogue_busy", message: "后台正在整理上一段对话" },
        };
        throw error;
      }
      return { turn_id: "confirmation-12", scope: "confusion" };
    },
    onWaiting(state) {
      waits.push(state.message);
    },
    async sleep(milliseconds) {
      delays.push(milliseconds);
    },
  });

  assert.equal(turn.turn_id, "confirmation-12");
  assert.equal(calls.length, 2);
  assert.deepEqual(waits, ["后台正在整理上一段对话"]);
  assert.deepEqual(delays, [1_000]);
});

test("pending open does not retry unrelated 409 conflicts", async () => {
  let calls = 0;
  await assert.rejects(
    dialogue!.executePendingConfirmationOpen("13", {
      async request() {
        calls += 1;
        const error = new Error("conflict") as Error & { status: number };
        error.status = 409;
        throw error;
      },
      async sleep() {
        throw new Error("must not sleep");
      },
    }),
    /conflict/,
  );
  assert.equal(calls, 1);
});
