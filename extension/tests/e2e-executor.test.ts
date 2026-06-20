import assert from "node:assert/strict";
import test from "node:test";

import {
  executeAction,
  registerE2EExecutor,
} from "../src/content/e2e-executor.ts";
import type { E2EAction, E2EPlatform } from "../src/shared/e2e.ts";

interface RectLike {
  width: number;
  height: number;
  top: number;
  left: number;
  bottom: number;
  right: number;
}

class FakeElement {
  public clicked = false;
  public scrolled = false;
  public readonly textContent: string;
  private readonly attrs: Record<string, string>;
  private readonly rect: RectLike;

  constructor(
    textContent: string,
    attrs: Record<string, string> = {},
    rect: RectLike = {
      width: 80,
      height: 24,
      top: 10,
      left: 10,
      bottom: 34,
      right: 90,
    },
  ) {
    this.textContent = textContent;
    this.attrs = attrs;
    this.rect = rect;
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  getBoundingClientRect(): RectLike {
    return this.rect;
  }

  scrollIntoView(): void {
    this.scrolled = true;
  }

  click(): void {
    this.clicked = true;
  }
}

function fakeEnv(elements: FakeElement[] = []) {
  const scrollCalls: unknown[] = [];
  return {
    scrollCalls,
    document: {
      querySelectorAll(_selector: string): FakeElement[] {
        return elements;
      },
    },
    window: {
      innerHeight: 800,
      scrollBy(options: unknown): void {
        scrollCalls.push(options);
      },
    },
    sleep: async () => {},
  };
}

test("twitter share clicks a visible matching target", async () => {
  const share = new FakeElement("", { "aria-label": "Share post" });
  const env = fakeEnv([share]);

  const result = await executeAction("twitter", "share", false, env);

  assert.deepEqual(result, { action: "share", status: "ok", detail: "clicked" });
  assert.equal(share.scrolled, true);
  assert.equal(share.clicked, true);
});

test("state-changing actions are skipped when not allowed", async () => {
  const like = new FakeElement("Like");
  const env = fakeEnv([like]);

  const result = await executeAction("twitter", "like", false, env);

  assert.deepEqual(result, {
    action: "like",
    status: "skipped",
    detail: "state_changing_action_blocked",
  });
  assert.equal(like.clicked, false);
});

test("state-changing actions click when allowed and matching text exists", async () => {
  const favorite = new FakeElement("收藏");
  const env = fakeEnv([favorite]);

  const result = await executeAction("xiaohongshu", "favorite", true, env);

  assert.deepEqual(result, { action: "favorite", status: "ok", detail: "clicked" });
  assert.equal(favorite.clicked, true);
});

test("click fails when no platform target is found", async () => {
  const hidden = new FakeElement("tweet", {}, {
    width: 0,
    height: 0,
    top: 0,
    left: 0,
    bottom: 0,
    right: 0,
  });
  const env = fakeEnv([hidden]);

  const result = await executeAction("twitter", "click", false, env);

  assert.deepEqual(result, {
    action: "click",
    status: "failed",
    detail: "target_not_found",
  });
  assert.equal(hidden.clicked, false);
});

test("scroll calls window.scrollBy with a smooth viewport-sized step", async () => {
  const env = fakeEnv();

  const result = await executeAction("douyin", "scroll", false, env);

  assert.deepEqual(result, { action: "scroll", status: "ok", detail: "scrolled" });
  assert.deepEqual(env.scrollCalls, [{ top: 600, behavior: "smooth" }]);
});

test("registerE2EExecutor registers an async chrome message listener", async () => {
  const listeners: Array<
    (
      message: unknown,
      sender: unknown,
      sendResponse: (response: unknown) => void,
    ) => boolean | undefined
  > = [];
  const originalChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = {
    runtime: {
      onMessage: {
        addListener(listener: (typeof listeners)[number]): void {
          listeners.push(listener);
        },
      },
    },
  };

  try {
    registerE2EExecutor("twitter");
    assert.equal(listeners.length, 1);

    const responsePromise = new Promise<unknown>((resolve) => {
      const keepAlive = listeners[0](
        {
          action: "OBC_E2E_EXECUTE",
          platform: "twitter" satisfies E2EPlatform,
          runId: "run-1",
          actions: ["snapshot"] satisfies E2EAction[],
          allowStateChanging: false,
        },
        {},
        resolve,
      );
      assert.equal(keepAlive, true);
    });

    assert.deepEqual(await responsePromise, {
      status: "ok",
      actions: [{ action: "snapshot", status: "ok", detail: "snapshot", executed: true }],
    });
  } finally {
    (globalThis as { chrome?: unknown }).chrome = originalChrome;
  }
});
