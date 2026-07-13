import assert from "node:assert/strict";
import test from "node:test";

import {
  createDouyinBrowserEnvironment,
  saveDouyin,
  type DouyinFavoriteRequestResult,
  type DouyinNativeSaveEnvironment,
  type DouyinSaveControl,
} from "../src/content/native-save/douyin.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const CONTENT_ID = "7300000000000000000";
const task: NativeSaveTask = {
  id: "123e4567-e89b-42d3-a456-426614174012",
  type: "native_save",
  platform: "douyin",
  platform_slug: "dy",
  item_key: `douyin:${CONTENT_ID}`,
  content_id: CONTENT_ID,
  content_url: `https://www.douyin.com/video/${CONTENT_ID}`,
  content_type: "video",
  requested_action: "favorite",
  resolved_action: "favorite",
  target_label: "抖音收藏",
};

function fixture(options: {
  currentUrl?: string;
  loggedIn?: boolean;
  unavailable?: boolean;
  initialSelected?: boolean;
  controls?: number;
  controlsAfterSleeps?: number;
  requestResult?: DouyinFavoriteRequestResult;
  rejectRequest?: boolean;
  confirmAfterRequest?: boolean;
  confirmAfterClick?: boolean;
  rateBefore?: string;
  rateAfterMutation?: string;
} = {}): DouyinNativeSaveEnvironment & { clicks: number; requests: number; sleeps: number } {
  let selected = options.initialSelected ?? false;
  let mutated = false;
  const control: DouyinSaveControl = {
    isSelected: () => selected,
    click() {
      env.clicks += 1;
      mutated = true;
      if (options.confirmAfterClick ?? true) selected = true;
    },
  };
  const env = {
    clicks: 0,
    requests: 0,
    sleeps: 0,
    currentUrl: options.currentUrl ?? task.content_url,
    isLoggedIn: () => options.loggedIn ?? true,
    isUnavailable: () => options.unavailable ?? false,
    isContentReady: () => env.sleeps >= (options.controlsAfterSleeps ?? 0),
    rateLimitFingerprint: () => mutated ? (options.rateAfterMutation ?? options.rateBefore ?? "") : (options.rateBefore ?? ""),
    async requestFavorite() {
      env.requests += 1;
      if (options.rejectRequest) throw new Error("network outcome unknown");
      if (options.requestResult !== null && options.requestResult !== undefined) mutated = true;
      if (options.confirmAfterRequest) selected = true;
      return options.requestResult ?? null;
    },
    findFavoriteControls: () => Array.from({
      length: env.sleeps >= (options.controlsAfterSleeps ?? 0) ? (options.controls ?? 1) : 0,
    }, () => control),
    sleep: async () => { env.sleeps += 1; },
  } satisfies DouyinNativeSaveEnvironment & { clicks: number; requests: number; sleeps: number };
  return env;
}

test("Douyin native save accepts only exact correlated aweme/video identities", async () => {
  for (const content_type of ["video", "aweme"]) {
    assert.deepEqual(await saveDouyin({ ...task, content_type }, fixture()), { status: "synced" });
  }
  for (const candidate of [
    { ...task, platform: "xiaohongshu" as const, platform_slug: "xhs" as const },
    { ...task, item_key: "douyin:other" },
    { ...task, content_type: "creator", content_url: "https://www.douyin.com/user/alice" },
    { ...task, content_type: "video", content_url: `https://www.douyin.com/video/${CONTENT_ID}/extra` },
    { ...task, content_id: "other", item_key: "douyin:other" },
  ]) {
    const env = fixture({ currentUrl: candidate.content_url });
    assert.deepEqual(await saveDouyin(candidate, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.clicks + env.requests, 0);
  }
  const wrongPage = fixture({ currentUrl: "https://www.douyin.com/video/other" });
  assert.deepEqual(await saveDouyin(task, wrongPage), {
    status: "unsupported",
    error_code: "unsupported_content_type",
  });
});

test("Douyin native save waits for the correlated favorite control before mutation", async () => {
  const env = fixture({ controlsAfterSleeps: 2 });
  assert.deepEqual(await saveDouyin(task, env), { status: "synced" });
  assert.ok(env.sleeps >= 2);
  assert.equal(env.clicks, 1);
});

test("Douyin native save checks login and deleted content before mutation", async () => {
  const loggedOut = fixture({ loggedIn: false, requestResult: "success", confirmAfterRequest: true });
  assert.deepEqual(await saveDouyin(task, loggedOut), { status: "login_required" });
  assert.equal(loggedOut.clicks + loggedOut.requests, 0);
  const deleted = fixture({ unavailable: true, requestResult: "success", confirmAfterRequest: true });
  assert.deepEqual(await saveDouyin(task, deleted), {
    status: "unsupported",
    error_code: "unsupported_content_type",
  });
  assert.equal(deleted.clicks + deleted.requests, 0);
});

test("Douyin native save validates favorite and watch-later fallback target exactly", async () => {
  assert.deepEqual(await saveDouyin({ ...task, requested_action: "watch_later" }, fixture()), { status: "synced" });
  for (const mismatch of [
    { ...task, target_label: "抖音点赞" },
    { ...task, requested_action: "watch_later" as const, resolved_action: "watch_later" as const },
    { ...task, requested_action: "favorite" as const, resolved_action: "watch_later" as const },
  ]) {
    const env = fixture();
    assert.deepEqual(await saveDouyin(mismatch, env), {
      status: "failed",
      error_code: "native_save_failed",
    });
    assert.equal(env.clicks + env.requests, 0);
  }
});

test("Douyin native save returns already_synced without a second mutation", async () => {
  const env = fixture({ initialSelected: true, requestResult: "success" });
  assert.deepEqual(await saveDouyin(task, env), { status: "already_synced" });
  assert.equal(env.clicks + env.requests, 0);
});

test("Douyin native save handles stable request success, rejection fallback, 429, and uncertainty", async () => {
  const confirmed = fixture({ requestResult: "success", confirmAfterRequest: true });
  assert.deepEqual(await saveDouyin(task, confirmed), { status: "synced" });
  assert.equal(confirmed.requests, 1);
  assert.equal(confirmed.clicks, 0);
  const rejected = fixture({ requestResult: "rejected" });
  assert.deepEqual(await saveDouyin(task, rejected), { status: "synced" });
  assert.equal(rejected.clicks, 1);
  const concurrentlySelected = fixture({ requestResult: "rejected", confirmAfterRequest: true });
  assert.deepEqual(await saveDouyin(task, concurrentlySelected), { status: "synced" });
  assert.equal(concurrentlySelected.requests, 1);
  assert.equal(concurrentlySelected.clicks, 0);
  const limited = fixture({ requestResult: "rate_limited" });
  assert.deepEqual(await saveDouyin(task, limited), { status: "rate_limited" });
  assert.equal(limited.clicks, 0);
  const uncertain = fixture({ rejectRequest: true });
  assert.deepEqual(await saveDouyin(task, uncertain), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(uncertain.clicks, 0);
});

test("Douyin native save never retries after accepted-but-unconfirmed request", async () => {
  const env = fixture({ requestResult: "success", confirmAfterClick: true });
  assert.deepEqual(await saveDouyin(task, env), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(env.requests, 1);
  assert.equal(env.clicks, 0);
});

test("Douyin native save never treats favorite count change alone as confirmation", async () => {
  const env = fixture({ confirmAfterClick: false });
  assert.deepEqual(await saveDouyin(task, env), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(env.clicks, 1);
});

test("Douyin native save ignores stale risk UI but maps a new correlated alert", async () => {
  assert.deepEqual(await saveDouyin(task, fixture({ rateBefore: "old", confirmAfterClick: true })), { status: "synced" });
  assert.deepEqual(await saveDouyin(task, fixture({ rateAfterMutation: "new", confirmAfterClick: false })), { status: "rate_limited" });
  for (const env of [
    fixture({ rateBefore: "old", rateAfterMutation: "", confirmAfterClick: false }),
    fixture({ rateBefore: "1:first\n2:second", rateAfterMutation: "2:second\n1:first", confirmAfterClick: false }),
  ]) {
    assert.deepEqual(await saveDouyin(task, env), {
      status: "failed",
      error_code: "native_save_failed",
    });
  }
});

interface FakeElement {
  hidden: boolean;
  style: { display: string; visibility: string };
  title: string;
  textContent: string;
  parentElement: FakeElement | null;
  attributes: Map<string, string>;
  clicks: number;
  hasAttribute(name: string): boolean;
  getAttribute(name: string): string | null;
  querySelectorAll(selector: string): FakeElement[];
  closest(selector: string): FakeElement | null;
  click(): void;
}

function domElement(attributes: Record<string, string> = {}): FakeElement {
  const values = new Map(Object.entries(attributes));
  return {
    hidden: false,
    style: { display: "", visibility: "" },
    title: "",
    textContent: "",
    parentElement: null,
    attributes: values,
    clicks: 0,
    hasAttribute: (name) => values.has(name),
    getAttribute: (name) => values.get(name) ?? null,
    querySelectorAll: () => [],
    closest(selector) {
      let current: FakeElement | null = this;
      while (current) {
        if (
          selector.includes("data-aweme-id") && current.attributes.has("data-aweme-id") ||
          selector.includes("data-video-id") && current.attributes.has("data-video-id") ||
          selector.includes("data-content-id") && current.attributes.has("data-content-id")
        ) return current;
        current = current.parentElement;
      }
      return null;
    },
    click() { this.clicks += 1; values.set("aria-pressed", "true"); },
  };
}

function browserDocument(controls: FakeElement[], options: {
  routeOnly?: boolean;
  login?: boolean;
  hiddenLogin?: boolean;
  unavailable?: boolean;
  hiddenUnavailable?: boolean;
  unrelatedControls?: FakeElement[];
  nestedUnrelatedControls?: FakeElement[];
  targetRiskElements?: () => FakeElement[];
  nestedUnrelatedRiskElements?: () => FakeElement[];
  unrelatedRiskElements?: () => FakeElement[];
} = {}): Document {
  const login = domElement();
  if (options.hiddenLogin) login.style.visibility = "hidden";
  const unavailable = domElement();
  if (options.hiddenUnavailable) unavailable.hidden = true;
  const target = domElement(options.routeOnly ? {} : { "data-aweme-id": CONTENT_ID });
  const nestedUnrelatedContainer = domElement({ "data-aweme-id": "nested-unrelated-video" });
  nestedUnrelatedContainer.parentElement = target;
  for (const control of options.nestedUnrelatedControls ?? []) {
    control.parentElement ??= nestedUnrelatedContainer;
  }
  target.querySelectorAll = (selector) => {
    if (selector.includes("video-favorite")) return [...controls, ...(options.nestedUnrelatedControls ?? [])];
    if (selector.includes("data-e2e='toast'")) {
      const targetRisk = options.targetRiskElements?.() ?? [];
      for (const element of targetRisk) element.parentElement ??= target;
      const nestedRisk = options.nestedUnrelatedRiskElements?.() ?? [];
      for (const element of nestedRisk) element.parentElement ??= nestedUnrelatedContainer;
      return [...targetRisk, ...nestedRisk];
    }
    return [];
  };
  for (const control of controls) control.parentElement ??= target;
  const unrelatedContainer = domElement({ "data-aweme-id": "unrelated-video" });
  unrelatedContainer.querySelectorAll = (selector) => selector.includes("video-favorite")
    ? (options.unrelatedControls ?? [])
    : [];
  for (const control of options.unrelatedControls ?? []) control.parentElement ??= unrelatedContainer;
  return {
    defaultView: {
      getComputedStyle(element: FakeElement) {
        return element.style;
      },
    },
    querySelector(selector: string) {
      if ((options.login || options.hiddenLogin) && selector.includes("login-modal")) return login;
      if ((options.unavailable || options.hiddenUnavailable) && selector.includes("not-found")) return unavailable;
      return null;
    },
    querySelectorAll(selector: string) {
      if (selector.includes("data-aweme-id")) {
        return options.routeOnly ? [] : [target, unrelatedContainer];
      }
      if (selector.includes("video-favorite")) return [...controls, ...(options.unrelatedControls ?? [])];
      if (selector.includes("login-modal")) return options.login || options.hiddenLogin ? [login] : [];
      if (selector.includes("not-found")) return options.unavailable || options.hiddenUnavailable ? [unavailable] : [];
      if (selector.includes("data-e2e='toast'")) {
        return [...(options.targetRiskElements?.() ?? []), ...(options.unrelatedRiskElements?.() ?? [])];
      }
      return [];
    },
  } as unknown as Document;
}

test("Douyin browser environment uses one exact route-scoped favorite control without identity attributes", async () => {
  const exact = domElement({ "data-e2e": "video-favorite" });
  const env = createDouyinBrowserEnvironment(
    browserDocument([exact], { routeOnly: true }),
    task.content_url,
  );
  env.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, env), { status: "synced" });
  assert.equal(exact.clicks, 1);

  const ambiguousControls = [
    domElement({ "data-e2e": "video-favorite" }),
    domElement({ "data-e2e": "video-favorite" }),
  ];
  const ambiguous = createDouyinBrowserEnvironment(
    browserDocument(ambiguousControls, { routeOnly: true }),
    task.content_url,
  );
  ambiguous.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, ambiguous), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(ambiguousControls.reduce((sum, control) => sum + control.clicks, 0), 0);
});

test("Douyin browser environment ignores hidden controls and fails closed on ambiguity", async () => {
  const hidden = domElement({ "aria-label": "收藏" });
  hidden.style.visibility = "hidden";
  const exact = domElement({ "aria-label": "收藏" });
  const countOnly = domElement({ "aria-label": "收藏数 123" });
  const env = createDouyinBrowserEnvironment(browserDocument([hidden, countOnly, exact]), task.content_url);
  env.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, env), { status: "synced" });
  assert.equal(hidden.clicks, 0);
  assert.equal(countOnly.clicks, 0);
  assert.equal(exact.clicks, 1);

  const ambiguous = createDouyinBrowserEnvironment(
    browserDocument([domElement({ "aria-label": "收藏" }), domElement({ "aria-label": "收藏" })]),
    task.content_url,
  );
  assert.deepEqual(await saveDouyin(task, ambiguous), {
    status: "failed",
    error_code: "native_save_failed",
  });
});

test("Douyin browser environment scopes mutation to the exact aweme and rejects hidden ancestors", async () => {
  const exact = domElement({ "aria-label": "收藏" });
  const hiddenAncestor = domElement();
  hiddenAncestor.style.display = "none";
  const hiddenDescendant = domElement({ "aria-label": "收藏" });
  hiddenDescendant.parentElement = hiddenAncestor;
  const unrelated = domElement({ "aria-label": "收藏" });
  const env = createDouyinBrowserEnvironment(
    browserDocument([hiddenDescendant, exact], { unrelatedControls: [unrelated] }),
    task.content_url,
  );
  env.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, env), { status: "synced" });
  assert.equal(exact.clicks, 1);
  assert.equal(hiddenDescendant.clicks, 0);
  assert.equal(unrelated.clicks, 0);

  const soleUnrelated = domElement({ "aria-label": "收藏" });
  const failClosed = createDouyinBrowserEnvironment(
    browserDocument([], { unrelatedControls: [soleUnrelated] }),
    task.content_url,
  );
  assert.deepEqual(await saveDouyin(task, failClosed), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(soleUnrelated.clicks, 0);

  const nestedUnrelated = domElement({ "aria-label": "收藏" });
  const nestedFailClosed = createDouyinBrowserEnvironment(
    browserDocument([], { nestedUnrelatedControls: [nestedUnrelated] }),
    task.content_url,
  );
  assert.deepEqual(await saveDouyin(task, nestedFailClosed), {
    status: "failed",
    error_code: "native_save_failed",
  });
  assert.equal(nestedUnrelated.clicks, 0);
});

test("Douyin browser environment detects login overlay before exact control mutation", async () => {
  const control = domElement({ "aria-label": "收藏" });
  const env = createDouyinBrowserEnvironment(browserDocument([control], { login: true }), task.content_url);
  assert.deepEqual(await saveDouyin(task, env), { status: "login_required" });
  assert.equal(control.clicks, 0);
});

test("Douyin browser environment ignores hidden login and unavailable SPA templates", async () => {
  for (const options of [{ hiddenLogin: true }, { hiddenUnavailable: true }]) {
    const control = domElement({ "aria-label": "收藏" });
    const env = createDouyinBrowserEnvironment(browserDocument([control], options), task.content_url);
    env.sleep = async () => {};
    assert.deepEqual(await saveDouyin(task, env), { status: "synced" });
    assert.equal(control.clicks, 1);
  }
  const unavailableControl = domElement({ "aria-label": "收藏" });
  const unavailable = createDouyinBrowserEnvironment(
    browserDocument([unavailableControl], { unavailable: true }),
    task.content_url,
  );
  assert.deepEqual(await saveDouyin(task, unavailable), {
    status: "unsupported",
    error_code: "unsupported_content_type",
  });
  assert.equal(unavailableControl.clicks, 0);
});

test("Douyin browser environment fingerprints only new target-local risk events", async () => {
  let unrelatedAppeared = false;
  const unrelatedRisk = domElement();
  unrelatedRisk.textContent = "操作频繁";
  const unconfirmed = domElement({ "aria-label": "收藏" });
  unconfirmed.click = function click() { this.clicks += 1; unrelatedAppeared = true; };
  const unrelatedEnv = createDouyinBrowserEnvironment(browserDocument([unconfirmed], {
    unrelatedRiskElements: () => unrelatedAppeared ? [unrelatedRisk] : [],
  }), task.content_url);
  unrelatedEnv.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, unrelatedEnv), {
    status: "failed",
    error_code: "native_save_failed",
  });

  let nestedAppeared = false;
  const nestedRisk = domElement();
  nestedRisk.textContent = "操作频繁";
  const nestedUnconfirmed = domElement({ "aria-label": "收藏" });
  nestedUnconfirmed.click = function click() { this.clicks += 1; nestedAppeared = true; };
  const nestedEnv = createDouyinBrowserEnvironment(browserDocument([nestedUnconfirmed], {
    nestedUnrelatedRiskElements: () => nestedAppeared ? [nestedRisk] : [],
  }), task.content_url);
  nestedEnv.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, nestedEnv), {
    status: "failed",
    error_code: "native_save_failed",
  });

  let replaced = false;
  const staleRisk = domElement();
  staleRisk.textContent = "操作频繁";
  const freshRisk = domElement();
  freshRisk.textContent = "操作频繁";
  const targetUnconfirmed = domElement({ "aria-label": "收藏" });
  targetUnconfirmed.click = function click() { this.clicks += 1; replaced = true; };
  const targetEnv = createDouyinBrowserEnvironment(browserDocument([targetUnconfirmed], {
    targetRiskElements: () => replaced ? [freshRisk] : [staleRisk],
  }), task.content_url);
  targetEnv.sleep = async () => {};
  assert.deepEqual(await saveDouyin(task, targetEnv), { status: "rate_limited" });
});
