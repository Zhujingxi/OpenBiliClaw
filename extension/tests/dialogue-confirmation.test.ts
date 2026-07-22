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
      },
    ) => Promise<{ turn: Record<string, unknown>; response: Record<string, unknown> }>;
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

  const result = await dialogue!.executeCardAction(cardTurn(), "confirm", {
    async request(path, body) {
      requests.push({ path, body });
      return { ok: true, outcome: "already_settled", state: "rejected", verdict: "rejected" };
    },
    onUpdate(turn) {
      updates.push(String((turn.payload as Record<string, unknown>)?.state ?? ""));
    },
  });

  assert.deepEqual(requests, [
    { path: "/chat/cards/turn%2Fcard%201/action", body: { action: "confirm" } },
  ]);
  assert.deepEqual(updates, ["confirmed", "rejected"]);
  assert.equal((result.turn.payload as Record<string, unknown>).state, "rejected");
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
