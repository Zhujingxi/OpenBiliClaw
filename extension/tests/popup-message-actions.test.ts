import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

// Field report 2026-07-06: message card buttons (确实不喜欢 / 不是 / 多聊聊 / ×)
// "sometimes don't respond". Root cause: per-button click listeners were
// orphaned every time renderMessagesList() did container.replaceChildren()
// (chat-turn polling, the post-fetch re-render in openMessagesPanel, another
// card's response). The fix is a SINGLE delegated listener on the persistent
// messages container, dispatched by data-msg-action — immune to child
// re-renders. These static guards lock that wiring in place.

const popupJs = readFileSync(resolve("popup", "popup.js"), "utf8");

// Body of buildMessageCard (the inbox probe card) — scoped so assertions
// don't catch the unrelated standalone probe card's own confirmBtn.
const buildMessageCard = popupJs.slice(
  popupJs.indexOf("function buildMessageCard"),
  popupJs.indexOf("function buildDelightCard"),
);

test("message card action buttons carry data-msg-action, not per-button listeners", () => {
  // Every action button tags itself with a data-msg-action value.
  for (const action of ["dismiss", "confirm", "reject", "chat"]) {
    assert.match(
      buildMessageCard,
      new RegExp(`dataset\\.msgAction = "${action}"`),
      `missing data-msg-action="${action}" on a message button`,
    );
  }

  // The old per-button listeners that the re-render orphaned must be gone
  // from the inbox card (delegation replaces them).
  assert.doesNotMatch(
    buildMessageCard,
    /addEventListener\(/,
    "message card buttons must not bind their own listeners (use delegation)",
  );
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
