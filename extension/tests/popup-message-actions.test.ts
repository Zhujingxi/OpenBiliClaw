import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

// Field report 2026-07-06: message card buttons (probe actions / ×)
// "sometimes don't respond". Root cause: per-button click listeners were
// orphaned every time renderMessagesList() did container.replaceChildren()
// (chat-turn polling, the post-fetch re-render in openMessagesPanel, another
// card's response). The fix is a SINGLE delegated listener on the persistent
// messages container, dispatched by data-msg-action — immune to child
// re-renders. These static guards lock that wiring in place.

const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");
const popupHtml = readFileSync(resolve("popup", "popup.html"), "utf8");

// Body of buildMessageCard (the inbox probe card) — scoped so assertions
// don't catch the unrelated standalone probe card's own confirmBtn.
const buildMessageCard = popupJs.slice(
  popupJs.indexOf("function buildMessageCard"),
  popupJs.indexOf("function buildDelightCard"),
);

test("message card action buttons carry delegated semantic actions", () => {
  assert.match(buildMessageCard, /dismiss\.dataset\.msgAction = "dismiss"/);
  assert.match(buildMessageCard, /probeActionDescriptors\(type\)/);
  assert.match(buildMessageCard, /dataset\.msgAction = descriptor\.action/);
  assert.match(buildMessageCard, /button\.type = "button"/);
  assert.match(buildMessageCard, /button\.setAttribute\("aria-label", descriptor\.label\)/);
  assert.match(buildMessageCard, /button\.title = descriptor\.label/);

  // The old per-button listeners that the re-render orphaned must be gone
  // from the inbox card (delegation replaces them).
  assert.doesNotMatch(
    buildMessageCard,
    /addEventListener\(/,
    "message card buttons must not bind their own listeners (use delegation)",
  );
});

test("probe action groups wrap their semantic buttons", () => {
  for (const selector of ["probe-actions", "spec-actions", "message-actions"]) {
    assert.match(
      popupHtml,
      new RegExp(`\\.${selector}[^\\{]*\\{[^\\}]*flex-wrap:\\s*wrap`),
    );
  }
  assert.match(popupHtml, /\.probe-btn:focus-visible/);
  assert.match(popupHtml, /box-shadow:\s*var\(--focus-ring\)/);
});

test("message cards expose all semantic probe actions", () => {
  const descriptors = popupJs.slice(
    popupJs.indexOf("function probeActionDescriptors"),
    popupJs.indexOf("function probeResponseMessage"),
  );
  for (const action of ["confirm", "defer", "reject", "chat"]) {
    assert.match(descriptors, new RegExp(`action: "${action}"`));
  }
  for (const label of [
    "确认喜欢",
    "暂时搁置",
    "确认不喜欢",
    "确认避雷",
    "搁置避雷",
    "不是雷点",
    "多聊聊",
  ]) {
    assert.match(descriptors, new RegExp(label));
  }
});

test("messages container binds ONE delegated action handler that survives re-renders", () => {
  // Delegated handler exists and is bound once on the container.
  assert.match(popupJs, /function onMessageActionClick/);
  assert.match(popupJs, /dataset\.actionsDelegated/);
  assert.match(popupJs, /container\.addEventListener\("click", onMessageActionClick\)/);

  // The handler dispatches every action off the clicked button + its card.
  const handler = popupJs.slice(
    popupJs.indexOf("function onMessageActionClick"),
    popupJs.indexOf("function renderMessagesList"),
  );
  assert.match(handler, /closest\("\[data-msg-action\]"\)/);
  assert.match(handler, /closest\("\.message-item"\)/);
  assert.match(handler, /dismissMessage\(/);
  assert.match(handler, /expandInlineChat\(/);
  assert.match(handler, /handleMessageResponse\(/);
  // Disabled buttons (pending chat) and double-clicks are guarded.
  assert.match(handler, /btn\.disabled/);
  assert.match(handler, /dataset\.responding/);
});

test("delegated message handler submits defer like confirm and reject", () => {
  const handler = popupJs.slice(
    popupJs.indexOf("function onMessageActionClick"),
    popupJs.indexOf("function renderMessagesList"),
  );
  assert.match(
    handler,
    /action === "confirm" \|\| action === "defer" \|\| action === "reject"/,
  );
});
