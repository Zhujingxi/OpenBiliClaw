import assert from "node:assert/strict";
import test from "node:test";

import {
  createZhihuBrowserEnvironment,
  saveZhihu,
  type ZhihuCollectionRow,
  type ZhihuNativeSaveEnvironment,
} from "../src/content/native-save/zhihu.ts";
import type { NativeSaveTask } from "../src/shared/native-save.ts";

const TASK_ID = "123e4567-e89b-42d3-a456-426614174008";

function taskFor(
  contentType: "question" | "answer" | "article",
  id: string,
): NativeSaveTask {
  const contentId = `${contentType}:${id}`;
  const contentUrl = contentType === "answer"
    ? `https://www.zhihu.com/question/101/answer/${id}`
    : contentType === "article"
      ? `https://zhuanlan.zhihu.com/p/${id}`
      : `https://www.zhihu.com/question/${id}`;
  return {
    id: TASK_ID,
    type: "native_save",
    platform: "zhihu",
    platform_slug: "zhihu",
    item_key: `zhihu:${contentId}`,
    content_id: contentId,
    content_url: contentUrl,
    content_type: contentType,
    requested_action: "favorite",
    resolved_action: "favorite",
    target_label: "OpenBiliClaw",
  };
}

interface FixtureOptions {
  currentUrl?: string;
  loginOverlay?: boolean;
  unavailable?: boolean;
  dialogAvailable?: boolean;
  initialRows?: Array<{ title: string; checked?: boolean }>;
  createSucceeds?: boolean;
  createdTitle?: string;
  createdChecked?: boolean;
  confirmAfterClick?: boolean;
  rateFingerprints?: string[];
  createdVisibleAfterLookups?: number;
  dialogReadyAfterSleeps?: number;
}

function fixture(
  task: NativeSaveTask,
  options: FixtureOptions = {},
): ZhihuNativeSaveEnvironment & {
  actions: string[];
  hasCollectionControl(): boolean;
  mutations: number;
} {
  let open = false;
  let postCreateLookups = 0;
  let created = false;
  let rateIndex = 0;
  let sleeps = 0;
  const rows = (options.initialRows ?? [{ title: "OpenBiliClaw" }]).map((row) => ({ ...row }));
  const env = {
    actions: [] as string[],
    currentUrl: options.currentUrl ?? task.content_url,
    mutations: 0,
    hasVisibleLoginOverlay: () => options.loginOverlay ?? false,
    isUnavailable: () => options.unavailable ?? false,
    hasCollectionControl: () => sleeps >= (options.dialogReadyAfterSleeps ?? 0),
    rateLimitFingerprint() {
      const values = options.rateFingerprints ?? [""];
      const value = values[Math.min(rateIndex, values.length - 1)] ?? "";
      rateIndex += 1;
      return value;
    },
    async openCollectionDialog() {
      env.actions.push("open");
      open = sleeps >= (options.dialogReadyAfterSleeps ?? 0) && (options.dialogAvailable ?? true);
      return open;
    },
    async closeCollectionDialog() {
      env.actions.push("close");
      open = false;
    },
    findNamedCollections(title: string) {
      if (!open) return [];
      if (created) postCreateLookups += 1;
      return rows.filter((candidate) => candidate.title === title).map((row): ZhihuCollectionRow => ({
        isChecked: () => row.checked ?? false,
        click() {
          env.actions.push(`select:${row.title}`);
          env.mutations += 1;
          if (options.confirmAfterClick ?? true) row.checked = true;
        },
      }));
    },
    async createCollection(title: string) {
      env.actions.push(`create:${title}`);
      env.mutations += 1;
      if (!(options.createSucceeds ?? true)) return false;
      created = true;
      const visibleAfter = options.createdVisibleAfterLookups ?? 0;
      const createdRow = { title: options.createdTitle ?? title, checked: options.createdChecked ?? false };
      if (visibleAfter === 0) rows.push(createdRow);
      else {
        const originalFind = env.findNamedCollections.bind(env);
        env.findNamedCollections = (lookupTitle: string) => {
          const found = originalFind(lookupTitle);
          if (postCreateLookups >= visibleAfter && !rows.includes(createdRow)) rows.push(createdRow);
          return found.length > 0 ? found : originalFind(lookupTitle);
        };
      }
      return true;
    },
    sleep: async () => { sleeps += 1; },
  } satisfies ZhihuNativeSaveEnvironment & {
    hasCollectionControl(): boolean;
    mutations: number;
  };
  return env;
}

test("Zhihu native save accepts exact typed question, answer, and article identities", async () => {
  for (const [type, id] of [
    ["question", "1001"],
    ["answer", "2002"],
    ["article", "3003"],
  ] as const) {
    const task = taskFor(type, id);
    const env = fixture(task);
    assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
    assert.equal(env.mutations, 1);
  }
});

test("Zhihu native save waits for the collection trigger before mutation", async () => {
  const task = taskFor("answer", "2002");
  const env = fixture(task, { dialogReadyAfterSleeps: 2 });
  assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
  assert.equal(env.mutations, 1);
  assert.equal(env.actions.filter((action) => action === "open").length, 1);
});

test("Zhihu native save reports the exact control, dialog, target, and confirmation stage", async () => {
  const task = taskFor("answer", "2002");
  const cases: Array<[ZhihuNativeSaveEnvironment, string]> = [
    [fixture(task, { dialogReadyAfterSleeps: 100 }), "native_control_not_found"],
    [fixture(task, { dialogAvailable: false }), "native_dialog_not_opened"],
    [
      fixture(task, {
        initialRows: [{ title: "OpenBiliClaw" }, { title: "OpenBiliClaw", checked: true }],
      }),
      "native_target_not_found",
    ],
    [fixture(task, { confirmAfterClick: false }), "native_confirmation_not_observed"],
    [
      fixture(task, { initialRows: [{ title: "Other" }], createSucceeds: false }),
      "native_request_rejected",
    ],
  ];

  for (const [env, errorCode] of cases) {
    assert.deepEqual(await saveZhihu(task, env), {
      status: "failed",
      error_code: errorCode,
    });
  }
});

test("Zhihu native save rejects task, page, item, and content-type mismatches before mutation", async () => {
  const task = taskFor("answer", "2002");
  const cases: Array<[NativeSaveTask, string | undefined]> = [
    [{ ...task, item_key: "zhihu:answer:9999" }, undefined],
    [{ ...task, content_type: "question" }, undefined],
    [{ ...task, content_url: "https://www.zhihu.com/question/101/answer/9999" }, undefined],
    [task, "https://www.zhihu.com/question/101/answer/9999"],
    [task, "https://www.zhihu.com/question/999/answer/2002"],
    [task, "https://www.zhihu.com/people/demo"],
  ];
  for (const [candidate, currentUrl] of cases) {
    const env = fixture(task, { currentUrl });
    assert.deepEqual(await saveZhihu(candidate, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.mutations, 0);
    assert.deepEqual(env.actions, []);
  }
});

test("Zhihu native save rejects unavailable or deleted content before opening the dialog", async () => {
  const task = taskFor("question", "1001");
  const env = fixture(task, { unavailable: true });
  assert.deepEqual(await saveZhihu(task, env), {
    status: "unsupported",
    error_code: "unsupported_content_type",
  });
  assert.deepEqual(env.actions, []);
});

test("Zhihu native save returns login_required for a visible overlay before mutation", async () => {
  const task = taskFor("article", "3003");
  const env = fixture(task, { loginOverlay: true });
  assert.deepEqual(await saveZhihu(task, env), { status: "login_required" });
  assert.equal(env.mutations, 0);
  assert.deepEqual(env.actions, []);
});

test("Zhihu favorite and watch_later both resolve only to exact OpenBiliClaw favorite", async () => {
  const task = taskFor("answer", "2002");
  for (const requested_action of ["favorite", "watch_later"] as const) {
    const env = fixture(task);
    assert.deepEqual(await saveZhihu({ ...task, requested_action }, env), { status: "synced" });
  }
  for (const mismatch of [
    { ...task, target_label: "openbiliclaw" },
    { ...task, requested_action: "watch_later" as const, resolved_action: "watch_later" as const },
  ]) {
    const env = fixture(task);
    assert.deepEqual(await saveZhihu(mismatch, env), {
      status: "failed",
      error_code: "native_save_failed",
    });
    assert.equal(env.mutations, 0);
  }
});

test("Zhihu native save uses exact case-sensitive Unicode title and fails ambiguous duplicates", async () => {
  const task = taskFor("question", "1001");
  const caseEnv = fixture(task, { initialRows: [{ title: "openbiliclaw" }] });
  assert.deepEqual(await saveZhihu(task, caseEnv), { status: "synced" });
  assert.deepEqual(caseEnv.actions, ["open", "create:OpenBiliClaw", "close", "open", "select:OpenBiliClaw"]);

  const duplicateEnv = fixture(task, {
    initialRows: [{ title: "OpenBiliClaw" }, { title: "OpenBiliClaw", checked: true }],
  });
  assert.deepEqual(await saveZhihu(task, duplicateEnv), {
    status: "failed",
    error_code: "native_target_not_found",
  });
  assert.equal(duplicateEnv.mutations, 0);
});

test("Zhihu native save creates exactly once then closes, reopens, re-queries, and selects unchecked row", async () => {
  const task = taskFor("article", "3003");
  const env = fixture(task, { initialRows: [], createdChecked: false });
  assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
  assert.deepEqual(env.actions, ["open", "create:OpenBiliClaw", "close", "open", "select:OpenBiliClaw"]);
  assert.equal(env.mutations, 2);
});

test("Zhihu native save polls the reopened dialog until the created exact row materializes", async () => {
  const task = taskFor("article", "3003");
  const env = fixture(task, {
    initialRows: [],
    createdVisibleAfterLookups: 3,
  });
  assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
  assert.equal(env.actions.filter((action) => action === "create:OpenBiliClaw").length, 1);
});

test("Zhihu native save never falls back after create failure or re-query mismatch", async () => {
  const task = taskFor("answer", "2002");
  for (const [env, errorCode] of [
    [fixture(task, { initialRows: [{ title: "Other" }], createSucceeds: false }), "native_request_rejected"],
    [fixture(task, { initialRows: [{ title: "Other" }], createdTitle: "openbiliclaw" }), "native_target_not_found"],
  ] as const) {
    assert.deepEqual(await saveZhihu(task, env), {
      status: "failed",
      error_code: errorCode,
    });
    assert.equal(env.actions.some((action) => action === "select:Other"), false);
    assert.equal(env.actions.some((action) => action === "select:openbiliclaw"), false);
  }
});

test("Zhihu native save is idempotent and gives selected proof precedence", async () => {
  const task = taskFor("question", "1001");
  const existing = fixture(task, {
    initialRows: [{ title: "OpenBiliClaw", checked: true }],
    rateFingerprints: ["stale-rate", "stale-rate\nnew-rate"],
  });
  assert.deepEqual(await saveZhihu(task, existing), { status: "already_synced" });
  assert.equal(existing.mutations, 0);

  const selectedAfterClick = fixture(task, {
    rateFingerprints: ["", "new-rate"],
  });
  assert.deepEqual(await saveZhihu(task, selectedAfterClick), { status: "synced" });
});

test("Zhihu native save detects only directional new action-local rate evidence", async () => {
  const task = taskFor("answer", "2002");
  const newRate = fixture(task, {
    confirmAfterClick: false,
    rateFingerprints: ["stale", "stale\nnew"],
  });
  assert.deepEqual(await saveZhihu(task, newRate), { status: "rate_limited" });

  for (const rateFingerprints of [["stale", "stale"], ["a\nb", "b\na"]]) {
    const stale = fixture(task, { confirmAfterClick: false, rateFingerprints });
    assert.deepEqual(await saveZhihu(task, stale), {
      status: "failed",
      error_code: "native_confirmation_not_observed",
    });
  }
});

test("Zhihu native save rejects extra-colon typed identities before mutation", async () => {
  for (const invalidContentId of [
    "question:1001:extra",
    "answer:2002:extra",
    "article:3003:extra",
  ]) {
    const type = invalidContentId.split(":", 1)[0] as "question" | "answer" | "article";
    const validTask = taskFor(type, invalidContentId.split(":")[1]);
    const invalidTask = {
      ...validTask,
      content_id: invalidContentId,
      item_key: `zhihu:${invalidContentId}`,
    };
    const env = fixture(validTask);
    assert.deepEqual(await saveZhihu(invalidTask, env), {
      status: "unsupported",
      error_code: "unsupported_content_type",
    });
    assert.equal(env.mutations, 0);
  }
});

function domElement(options: {
  attrs?: Record<string, string>;
  hidden?: boolean;
  parent?: unknown;
  text?: string;
  query?: (selector: string) => unknown[];
  click?: () => void;
} = {}): HTMLElement {
  const attrs = new Map(Object.entries(options.attrs ?? {}));
  return {
    hidden: options.hidden ?? false,
    style: { display: "", visibility: "" },
    parentElement: options.parent ?? null,
    textContent: options.text ?? "",
    hasAttribute(name: string) { return attrs.has(name); },
    getAttribute(name: string) { return attrs.get(name) ?? null; },
    querySelectorAll(selector: string) { return options.query?.(selector) ?? []; },
    querySelector(selector: string) { return options.query?.(selector)?.[0] ?? null; },
    click() { options.click?.(); },
    dispatchEvent() { return true; },
  } as unknown as HTMLElement;
}

test("Zhihu browser environment applies full ancestor visibility to login and unavailable overlays", () => {
  const hiddenAncestor = domElement({ hidden: true });
  const hiddenLogin = domElement({ parent: hiddenAncestor });
  const hiddenDeleted = domElement({ parent: hiddenAncestor, text: "内容已删除" });
  const visibleLogin = domElement();
  let loginNodes = [hiddenLogin];
  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      if (selector.includes("SignFlow")) return loginNodes;
      if (selector.includes("ErrorPage")) return [hiddenDeleted];
      return [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, taskFor("question", "1001").content_url);
  assert.equal(env.hasVisibleLoginOverlay(), false);
  assert.equal(env.isUnavailable(), false);
  loginNodes = [visibleLogin];
  assert.equal(env.hasVisibleLoginOverlay(), true);
});

test("Zhihu browser environment binds control, dialog, and row to the exact closest identity", async () => {
  const task = taskFor("answer", "2002");
  let targetClicks = 0;
  let relatedClicks = 0;
  let rowChecked = false;
  let dialogsOpen = false;

  let targetContainer: HTMLElement;
  let nestedRelatedContainer: HTMLElement;
  const targetButton = domElement({
    attrs: { "aria-label": "收藏" },
    click: () => { targetClicks += 1; dialogsOpen = true; },
  });
  const relatedButton = domElement({
    attrs: { "aria-label": "收藏" },
    click: () => { relatedClicks += 1; },
  });
  nestedRelatedContainer = domElement({
    attrs: { "data-answer-id": "9999" },
    query: (selector) => selector.includes("button") ? [relatedButton] : [],
  });
  targetContainer = domElement({
    attrs: { "data-answer-id": "2002" },
    query: (selector) => selector.includes("button")
      ? [targetButton, relatedButton]
      : [],
  });
  (targetButton as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (nestedRelatedContainer as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (relatedButton as unknown as { parentElement: HTMLElement }).parentElement = nestedRelatedContainer;

  let exactDialog: HTMLElement;
  const checkbox = domElement({
    attrs: { role: "checkbox", "aria-checked": "false" },
    click: () => {
      rowChecked = true;
      (checkbox as unknown as { getAttribute(name: string): string | null }).getAttribute =
        (name) => name === "aria-checked" ? "true" : name === "role" ? "checkbox" : null;
    },
  });
  const row = domElement({
    attrs: { "data-collection-id": "88" },
    text: "OpenBiliClaw",
    query: (selector) => selector.includes("checkbox") ? [checkbox] : [],
  });
  let spacedClicks = 0;
  const spacedRow = domElement({
    attrs: { "data-collection-id": "89" },
    text: " OpenBiliClaw ",
    click: () => { spacedClicks += 1; },
  });
  let nestedRowContainer: HTMLElement;
  const nestedRow = domElement({
    attrs: { "data-collection-id": "90" },
    text: "OpenBiliClaw",
  });
  nestedRowContainer = domElement({ attrs: { "data-answer-id": "9999" } });
  exactDialog = domElement({
    attrs: { role: "dialog", "data-answer-id": "2002" },
    query: (selector) => selector.includes("collection") || selector.includes("favlist")
      ? [row, spacedRow, nestedRow]
      : [],
  });
  (row as unknown as { parentElement: HTMLElement }).parentElement = exactDialog;
  (checkbox as unknown as { parentElement: HTMLElement }).parentElement = row;
  (spacedRow as unknown as { parentElement: HTMLElement }).parentElement = exactDialog;
  (nestedRowContainer as unknown as { parentElement: HTMLElement }).parentElement = exactDialog;
  (nestedRow as unknown as { parentElement: HTMLElement }).parentElement = nestedRowContainer;
  const relatedDialog = domElement({
    attrs: { role: "dialog" },
    parent: nestedRelatedContainer,
  });
  const hiddenAncestor = domElement({ hidden: true });
  const hiddenExactDialog = domElement({
    attrs: { role: "dialog", "data-answer-id": "2002" },
    parent: hiddenAncestor,
  });

  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      if (selector.includes("data-zop") || selector.includes("data-answer-id")) {
        return [targetContainer, nestedRelatedContainer];
      }
      if (selector.includes("[role='dialog']")) {
        return dialogsOpen ? [relatedDialog, hiddenExactDialog, exactDialog] : [];
      }
      return [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, task.content_url);
  assert.deepEqual(await saveZhihu(task, env), { status: "synced" });
  assert.equal(targetClicks, 1);
  assert.equal(relatedClicks, 0);
  assert.equal(rowChecked, true);
  assert.equal(spacedClicks, 0);
});

test("Zhihu browser rate evidence ignores stale, unrelated, and nested recommendation alerts but detects reused action alert", () => {
  const task = taskFor("question", "1001");
  let targetContainer: HTMLElement;
  let nestedRecommendation: HTMLElement;
  const stale = domElement({ text: "操作频繁，请稍后再试" });
  const reused = domElement({ hidden: true, text: "操作频繁，请稍后再试" });
  const nested = domElement({ text: "操作频繁，请稍后再试" });
  nestedRecommendation = domElement({ attrs: { "data-answer-id": "9999" } });
  targetContainer = domElement({
    attrs: { "data-question-id": "1001" },
    query: (selector) => selector.includes("role='alert'") ? [stale, reused, nested] : [],
  });
  for (const alert of [stale, reused]) {
    (alert as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  }
  (nestedRecommendation as unknown as { parentElement: HTMLElement }).parentElement = targetContainer;
  (nested as unknown as { parentElement: HTMLElement }).parentElement = nestedRecommendation;
  const unrelated = domElement({ text: "操作频繁，请稍后再试" });
  const root = {
    defaultView: null,
    querySelectorAll(selector: string) {
      if (selector.includes("data-zop") || selector.includes("data-question-id")) {
        return [targetContainer, nestedRecommendation];
      }
      if (selector.includes("role='alert'")) return [unrelated];
      return [];
    },
  } as unknown as Document;
  const env = createZhihuBrowserEnvironment(root, task.content_url);
  const before = env.rateLimitFingerprint();
  assert.match(before, /^1:操作频繁/);
  assert.equal(before.split("\n").length, 1);
  reused.hidden = false;
  const after = env.rateLimitFingerprint();
  assert.equal(after.split("\n").length, 2);
  assert.notEqual(after, before);
});

test("Zhihu browser creation waits for async form and deterministic confirmation proof exactly once", async () => {
  const originalInput = (globalThis as { HTMLInputElement?: unknown }).HTMLInputElement;
  class FakeInputElement {
    _value = "";
    set value(value: string) { this._value = value; }
    get value(): string { return this._value; }
  }
  (globalThis as { HTMLInputElement?: unknown }).HTMLInputElement = FakeInputElement;

  const makeEnvironment = (
    proofAppears: boolean,
    detachedForm = false,
    confirmAvailable = true,
  ) => {
    const task = taskFor("question", "1001");
    let dialogOpen = false;
    let newClicked = false;
    let confirmClicks = 0;
    let inputQueries = 0;
    let container: HTMLElement;
    let dialog: HTMLElement;
    let formDialog: HTMLElement;
    const openButton = domElement({
      attrs: { "aria-label": "收藏" },
      click: () => { dialogOpen = true; },
    });
    const newButton = domElement({
      text: detachedForm ? "+ 新建收藏夹" : "新建收藏夹",
      click: () => { newClicked = true; },
    });
    const confirmButton = domElement({
      text: detachedForm ? "确认创建" : "创建",
      click: () => { confirmClicks += 1; },
    });
    const input = domElement() as HTMLInputElement & { _value?: string };
    dialog = domElement({
      attrs: { role: "dialog" },
      query(selector) {
        if (selector.includes("button")) {
          return newClicked && !detachedForm && confirmAvailable
            ? [newButton, confirmButton]
            : [newButton];
        }
        if (selector.includes("input")) {
          if (detachedForm) return [];
          inputQueries += 1;
          if (!newClicked || inputQueries === 1) return [];
          if (confirmClicks > 0 && proofAppears) return [];
          return [input];
        }
        return [];
      },
    });
    formDialog = domElement({
      attrs: { role: "dialog" },
      query(selector) {
        if (!newClicked || !detachedForm) return [];
        if (selector.includes("button")) return confirmAvailable ? [confirmButton] : [];
        if (selector.includes("input")) {
          inputQueries += 1;
          if (inputQueries === 1) return [];
          if (confirmClicks > 0 && proofAppears) return [];
          return [input];
        }
        return [];
      },
    });
    container = domElement({
      attrs: { "data-question-id": "1001" },
      query: (selector) => selector.includes("button") ? [openButton] : [],
    });
    for (const element of [openButton]) {
      (element as unknown as { parentElement: HTMLElement }).parentElement = container;
    }
    (newButton as unknown as { parentElement: HTMLElement }).parentElement = dialog;
    for (const element of [confirmButton, input]) {
      (element as unknown as { parentElement: HTMLElement }).parentElement = detachedForm
        ? formDialog
        : dialog;
    }
    const root = {
      defaultView: null,
      querySelectorAll(selector: string) {
        if (selector.includes("data-zop") || selector.includes("data-question-id")) return [container];
        if (selector.includes("[role='dialog']")) {
          return dialogOpen ? [dialog, ...(newClicked && detachedForm ? [formDialog] : [])] : [];
        }
        return [];
      },
    } as unknown as Document;
    return {
      env: createZhihuBrowserEnvironment(root, task.content_url),
      confirmClicks: () => confirmClicks,
      input,
    };
  };

  try {
    const confirmed = makeEnvironment(true);
    assert.equal(await confirmed.env.openCollectionDialog(), true);
    assert.equal(await confirmed.env.createCollection("OpenBiliClaw"), true);
    assert.equal(confirmed.confirmClicks(), 1);
    assert.equal(confirmed.input._value, "OpenBiliClaw");

    const detached = makeEnvironment(true, true);
    assert.equal(await detached.env.openCollectionDialog(), true);
    assert.equal(await detached.env.createCollection("OpenBiliClaw"), true);
    assert.equal(detached.confirmClicks(), 1);
    assert.equal(detached.input._value, "OpenBiliClaw");

    const noConfirm = makeEnvironment(true, true, false);
    assert.equal(await noConfirm.env.openCollectionDialog(), true);
    assert.equal(await noConfirm.env.createCollection("OpenBiliClaw"), false);
    assert.equal(noConfirm.env.creationFailureCode?.(), "native_request_rejected");

    const uncertain = makeEnvironment(false);
    assert.equal(await uncertain.env.openCollectionDialog(), true);
    assert.equal(await uncertain.env.createCollection("OpenBiliClaw"), false);
    assert.equal(uncertain.env.creationFailureCode?.(), "native_confirmation_not_observed");
    assert.equal(uncertain.confirmClicks(), 1);
  } finally {
    (globalThis as { HTMLInputElement?: unknown }).HTMLInputElement = originalInput;
  }
});
