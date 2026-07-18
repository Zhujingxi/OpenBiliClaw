import assert from "node:assert/strict";
import test from "node:test";

import {
  buildInitChecklist,
  describeInitFailure,
  describeInitReason,
  describeInitStatusReason,
  describeInitStartError,
  embeddingPullProgressView,
  embeddingRepairAction,
  embeddingRepairStartAccepted,
  getEnabledPlatforms,
  hardPrereqsSatisfied,
  initProgressView,
  initSelectedSourcesNeedingEnable,
  initSourceLabels,
  initStartMode,
  INIT_SOURCE_LOGIN_HINT,
  INIT_SOURCE_OPTIONS,
  initStartButtonState,
  isInitTerminal,
  shouldAttachRunningInitProgress,
} from "../popup/popup-init-control.js";

function statusWith(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    initialized: false,
    running: false,
    current_stage: 0,
    total_stages: 4,
    stages: [
      { n: 1, label: "拉取数据", status: "pending", reason: null },
      { n: 2, label: "分析偏好", status: "pending", reason: null },
      { n: 3, label: "生成画像", status: "pending", reason: null },
      { n: 4, label: "发现内容池", status: "pending", reason: null },
    ],
    partial_success: false,
    can_start: false,
    can_manage: true,
    prerequisites: {
      bilibili_logged_in: false,
      bilibili_check: "failed",
      llm_ready: false,
      embedding_ready: false,
      enabled_platforms: [],
    },
    reason: "bilibili_not_logged_in",
    detail: "",
    ...overrides,
  };
}

test("checklist marks hard prereqs and surfaces hints when missing", () => {
  const rows = buildInitChecklist(statusWith());
  const bili = rows.find((r) => r.key === "bilibili");
  const llm = rows.find((r) => r.key === "llm");
  assert.equal(bili?.hard, true);
  assert.equal(bili?.ok, false);
  assert.ok(bili?.hint.length > 0);
  assert.equal(llm?.hard, true);
  assert.equal(llm?.ok, false);
});

test("embeddingPullProgressView reports live bge-m3 pull percent + label", () => {
  const idle = embeddingPullProgressView({ embedding_ready: true });
  assert.equal(idle.active, false);
  assert.equal(idle.pct, 0);

  const pulling = embeddingPullProgressView({
    embedding_repair_running: true,
    embedding_repair_completed: 50,
    embedding_repair_total: 200,
    embedding_pull_status: "downloading",
  });
  assert.equal(pulling.active, true);
  assert.equal(pulling.pct, 25);
  assert.ok(pulling.label.includes("downloading"));

  const starting = embeddingPullProgressView({
    embedding_repair_running: true,
    ollama_phase: "starting",
  });
  assert.equal(starting.pct, 1); // no totals yet → 1% floor while active
  assert.ok(starting.label.includes("Ollama"));
});

test("embeddingRepairAction picks the right button per embedding_check", () => {
  assert.deepEqual(embeddingRepairAction({ embedding_ready: true }), {
    repairable: false,
    label: "",
  });
  assert.equal(
    embeddingRepairAction({ embedding_check: "model_missing" }).label,
    "自动下载向量模型",
  );
  assert.equal(
    embeddingRepairAction({ embedding_check: "model_path_encoding" }).label,
    "迁移模型目录并修复",
  );
  assert.equal(embeddingRepairAction({ embedding_check: "disk_full" }).label, "重新检测");
  assert.equal(embeddingRepairAction({ embedding_check: "not_running" }).repairable, false);
  assert.equal(embeddingRepairStartAccepted({ status: 202 }), true);
  assert.equal(
    embeddingRepairStartAccepted({ status: 409, error: "already_running" }),
    true,
  );
  assert.equal(embeddingRepairStartAccepted({ status: 0 }), false);
  assert.equal(embeddingRepairStartAccepted({ status: 404 }), false);
  assert.equal(embeddingRepairStartAccepted({ status: 500 }), false);
});

test("embedding checklist row carries pull progress + repair action", () => {
  const rows = buildInitChecklist(
    statusWith({
      prerequisites: {
        embedding_ready: false,
        embedding_required: true,
        embedding_check: "model_missing",
        embedding_repair_running: true,
        embedding_repair_completed: 10,
        embedding_repair_total: 100,
      },
    }),
  );
  const emb = rows.find((r) => r.key === "embedding");
  assert.equal(emb?.pull.active, true);
  assert.equal(emb?.pull.pct, 10);
  assert.equal(emb?.repair.repairable, true);
});

test("hardPrereqsSatisfied is false until both bilibili and llm are ready", () => {
  assert.equal(hardPrereqsSatisfied(statusWith()), false);
  assert.equal(
    hardPrereqsSatisfied(
      statusWith({ prerequisites: { bilibili_logged_in: true, llm_ready: false } }),
    ),
    false,
  );
  assert.equal(
    hardPrereqsSatisfied(
      statusWith({
        prerequisites: { bilibili_logged_in: true, llm_ready: true, embedding_ready: false },
      }),
    ),
    true,
  );
});

test("enabled platforms surface in the checklist label", () => {
  const status = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      llm_ready: true,
      embedding_ready: true,
      enabled_platforms: ["bilibili", "youtube"],
    },
  });
  assert.deepEqual(getEnabledPlatforms(status), ["bilibili", "youtube"]);
  const platformRow = buildInitChecklist(status).find((r) => r.key === "platforms");
  assert.ok(platformRow?.label.includes("YouTube"));
  assert.equal(platformRow?.ok, true);
});

test("start button disabled with reason when prereqs missing", () => {
  const btn = initStartButtonState(statusWith());
  assert.equal(btn.enabled, false);
  assert.ok(btn.reason.includes("B 站"));
});

test("start button enabled exactly when can_start is true and idle", () => {
  const btn = initStartButtonState(
    statusWith({
      can_start: true,
      reason: "none",
      prerequisites: { bilibili_logged_in: true, llm_ready: true, embedding_ready: false },
    }),
  );
  assert.equal(btn.enabled, true);
  assert.equal(btn.label, "开始初始化");
});

test("start button gates on selection: empty selection and bilibili-without-login block", () => {
  const noBiliLogin = statusWith({
    can_start: true,
    reason: "none",
    prerequisites: { bilibili_logged_in: false, llm_ready: true, embedding_ready: true },
  });
  // Nothing checked → blocked regardless of can_start.
  const empty = initStartButtonState(noBiliLogin, []);
  assert.equal(empty.enabled, false);
  assert.ok(empty.reason.includes("至少"));
  // Bilibili checked but not logged in → blocked with the B 站 reason.
  const withBili = initStartButtonState(noBiliLogin, ["bilibili", "xiaohongshu"]);
  assert.equal(withBili.enabled, false);
  assert.ok(withBili.reason.includes("B 站"));
  // Bilibili deselected → B 站 login no longer blocks.
  const withoutBili = initStartButtonState(noBiliLogin, ["xiaohongshu"]);
  assert.equal(withoutBili.enabled, true);
  // Legacy callers (no selection passed) keep treating bilibili as required.
  assert.equal(initStartButtonState(noBiliLogin).enabled, false);
});

test("checklist B 站 row is hard only while bilibili is selected", () => {
  const status = statusWith();
  const withBili = buildInitChecklist(status, ["bilibili", "xiaohongshu"]).find(
    (r) => r.key === "bilibili",
  );
  assert.equal(withBili?.hard, true);
  const withoutBili = buildInitChecklist(status, ["xiaohongshu"]).find(
    (r) => r.key === "bilibili",
  );
  assert.equal(withoutBili?.hard, false);
  assert.ok(withoutBili?.label.includes("可跳过"));
  // hardPrereqsSatisfied honours the same selection.
  const llmReady = statusWith({
    prerequisites: { bilibili_logged_in: false, llm_ready: true, embedding_ready: false },
  });
  assert.equal(hardPrereqsSatisfied(llmReady, ["xiaohongshu"]), true);
  assert.equal(hardPrereqsSatisfied(llmReady, ["bilibili", "xiaohongshu"]), false);
});

test("start button reflects running and already-initialized states", () => {
  assert.equal(initStartButtonState(statusWith({ running: true })).enabled, false);
  const done = initStartButtonState(statusWith({ initialized: true, can_start: false }));
  assert.equal(done.enabled, false);
  assert.equal(done.label, "已初始化");
});

test("progress view advances mid-stage and reports parallel stage 3/4", () => {
  const status = statusWith({
    running: true,
    current_stage: 3,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "ok", reason: null },
      { n: 3, label: "生成画像", status: "running", reason: null },
      { n: 4, label: "发现内容池", status: "running", reason: null },
    ],
  });
  const view = initProgressView(status);
  assert.equal(view.active, true);
  assert.equal(view.doneCount, 2);
  assert.ok(view.stageLabel.includes("生成画像"));
  // 2 done + 0.5 in-flight over 4 → ~63%.
  assert.ok(view.pct > 50 && view.pct < 75);
  assert.equal(view.failed, false);
});

test("progress view reports completion and failure terminals", () => {
  const ok = statusWith({
    initialized: true,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "ok", reason: null },
      { n: 3, label: "生成画像", status: "ok", reason: null },
      { n: 4, label: "发现内容池", status: "ok", reason: null },
    ],
  });
  assert.equal(initProgressView(ok).pct, 100);
  assert.equal(isInitTerminal(ok), true);

  const failed = statusWith({
    reason: "llm_not_ready",
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "failed", reason: "llm_not_ready" },
      { n: 3, label: "生成画像", status: "pending", reason: null },
      { n: 4, label: "发现内容池", status: "pending", reason: null },
    ],
  });
  assert.equal(initProgressView(failed).failed, true);
  assert.equal(isInitTerminal(failed), true);
});

test("idle status is not terminal", () => {
  assert.equal(isInitTerminal(statusWith()), false);
  assert.equal(isInitTerminal(null), false);
});

test("reason + start-error text mapping", () => {
  assert.ok(describeInitReason("bilibili_not_logged_in").includes("B 站"));
  assert.equal(describeInitReason("none"), "");
  assert.equal(describeInitReason("no_profile_signal_sources"), "");
  assert.equal(describeInitReason("totally_unknown"), "");
  const err = Object.assign(new Error("boom"), {
    status: 409,
    details: { error: "already_running" },
  });
  assert.ok(describeInitStartError(err).includes("进行中"));
});

test("failure text appends backend detail so internal_error is diagnosable", () => {
  // Mapped reason + stored crash detail → generic copy with specifics appended.
  const crashed = statusWith({
    reason: "internal_error",
    detail: "RuntimeError: provider exploded mid-run",
  });
  const text = describeInitFailure(crashed);
  assert.ok(text.includes("初始化过程中出错了"));
  assert.ok(text.includes("RuntimeError: provider exploded mid-run"));
  // Mapped reason without detail (pre-v0.3.156 backend) → generic copy only.
  assert.equal(
    describeInitFailure(statusWith({ reason: "internal_error", detail: "" })),
    "初始化过程中出错了，请稍后重试。",
  );
  // Unmapped typed reason (empty_signals …) → its human message stands alone.
  assert.equal(
    describeInitFailure(statusWith({ reason: "empty_signals", detail: "没有拉到任何行为信号。" })),
    "没有拉到任何行为信号。",
  );
  // Nothing at all → stage reason, then the generic retry hint.
  assert.equal(
    describeInitFailure(statusWith({ reason: "none" }), { failedReason: "stage-2-broke" }),
    "stage-2-broke",
  );
  assert.equal(describeInitFailure(statusWith({ reason: "none" })), "请稍后重试");
  // interrupted / cancelled now map to human copy instead of raw codes.
  assert.ok(describeInitReason("interrupted").includes("打断"));
  assert.ok(describeInitReason("cancelled").includes("取消"));
});

test("timeout and account-sync details explain cause and recovery without machine codes", () => {
  const timeoutDetail =
    "偏好分析等待 AI 服务超过 6 分钟仍未返回结果。请到模型设置测试 AI 服务后重试初始化。";
  const hardFailure = statusWith({ reason: "analyze_failed", detail: timeoutDetail });
  assert.equal(describeInitFailure(hardFailure), timeoutDetail);
  assert.equal(describeInitStatusReason(hardFailure), timeoutDetail);
  assert.ok(!describeInitFailure(hardFailure).includes("analyze_failed"));

  const accountDetail =
    "画像分析失败：AI 偏好分析等待模型服务超过 6 分钟仍未返回结果，请检查 Base URL。";
  const accountFailure = statusWith({ reason: "llm_not_ready", detail: accountDetail });
  assert.equal(describeInitStatusReason(accountFailure), accountDetail);
  assert.equal(initStartButtonState(accountFailure).reason, accountDetail);

  const partialDetail =
    "画像已生成，但首轮内容池等待超过 10 分钟；系统会在后台继续补池。";
  const partial = statusWith({
    initialized: true,
    partial_success: true,
    reason: "discovery_timeout",
    detail: partialDetail,
  });
  const partialButton = initStartButtonState(partial);
  assert.equal(describeInitStatusReason(partial), partialDetail);
  assert.equal(partialButton.label, "画像已生成");
  assert.equal(partialButton.reason, partialDetail);
});

// ── Per-run platform source selection ──────────────────────────────────────

test("init source options: bilibili is default-checked but deselectable, others opt-in", () => {
  const bili = INIT_SOURCE_OPTIONS.find((o) => o.key === "bilibili");
  assert.ok(bili && bili.defaultChecked === true);
  assert.ok(!("required" in bili), "bilibili must no longer be marked required");
  const optional = INIT_SOURCE_OPTIONS.filter((o) => !o.defaultChecked).map((o) => o.key);
  assert.deepEqual(optional, ["xiaohongshu", "douyin", "youtube", "twitter", "zhihu", "reddit"]);
  // The login reminder copy mentions logging in on this browser.
  assert.ok(INIT_SOURCE_LOGIN_HINT.includes("登录"));
});

test("init source options: X (twitter) is present, opt-in, labelled X", () => {
  const x = INIT_SOURCE_OPTIONS.find((o) => o.key === "twitter");
  assert.ok(x, "twitter option must exist");
  assert.ok(!x?.defaultChecked);
  assert.equal(x?.label, "X");
});

test("init source options: Zhihu is present, opt-in, labelled 知乎", () => {
  const zhihu = INIT_SOURCE_OPTIONS.find((o) => o.key === "zhihu");
  assert.ok(zhihu, "zhihu option must exist");
  assert.ok(!zhihu?.defaultChecked);
  assert.equal(zhihu?.label, "知乎");
});

test("init source options: Reddit is present, opt-in, labelled Reddit", () => {
  const reddit = INIT_SOURCE_OPTIONS.find((o) => o.key === "reddit");
  assert.ok(reddit, "reddit option must exist");
  assert.ok(!reddit?.defaultChecked);
  assert.equal(reddit?.label, "Reddit");
});

test("start button allows Reddit as the only profile signal source", () => {
  const state = initStartButtonState(
    statusWith({
      can_start: true,
      reason: "none",
      prerequisites: {
        bilibili_logged_in: true,
        bilibili_check: "ok",
        llm_ready: true,
        embedding_ready: true,
        enabled_platforms: ["reddit"],
      },
    }),
    ["reddit"],
  );

  assert.equal(state.enabled, true);
  assert.equal(state.reason, "");
});

test("initSourceLabels maps known keys and passes unknowns through", () => {
  assert.deepEqual(initSourceLabels(["bilibili", "xiaohongshu", "zhihu", "reddit", "weibo"]), [
    "B 站",
    "小红书",
    "知乎",
    "Reddit",
    "weibo",
  ]);
  assert.deepEqual(initSourceLabels(undefined as unknown as string[]), []);
});

test("needs-enable: selected optional sources are guided-init opt-ins", () => {
  const status = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: true,
      enabled_platforms: ["bilibili", "xiaohongshu"],
    },
  });
  // User checked xhs (enabled) + douyin (NOT enabled). The checkbox is now an
  // explicit opt-in, so the UI must not block before POST /api/init.
  assert.deepEqual(
    initSelectedSourcesNeedingEnable(["bilibili", "xiaohongshu", "douyin"], status),
    [],
  );
  // Everything checked is enabled → nothing to flag.
  assert.deepEqual(
    initSelectedSourcesNeedingEnable(["bilibili", "xiaohongshu"], status),
    [],
  );
  // Bilibili follows the same rule: selected means effective for this run.
  const biliDisabled = statusWith({
    prerequisites: { enabled_platforms: [] },
  });
  assert.deepEqual(initSelectedSourcesNeedingEnable(["bilibili"], biliDisabled), []);
});

test("embedding hint prefers backend detail, falls back to check code, then generic", () => {
  const rows = (prereqOverrides: Record<string, unknown>) =>
    buildInitChecklist(
      statusWith({
        prerequisites: {
          bilibili_logged_in: true,
          bilibili_check: "ok",
          llm_ready: true,
          embedding_ready: false,
          enabled_platforms: ["bilibili"],
          ...prereqOverrides,
        },
      }),
    ).find((r) => r.key === "embedding");

  // Backend-provided detail wins verbatim (v0.3.155+ embedding_detail).
  const withDetail = rows({
    embedding_check: "model_broken",
    embedding_detail: "bge-m3 已安装但调用返回 HTTP 500",
  });
  assert.equal(withDetail?.hint, "bge-m3 已安装但调用返回 HTTP 500");

  // No detail → per-code fallback copy.
  const byCode = rows({ embedding_check: "model_missing", embedding_detail: "" });
  assert.ok(byCode?.hint.includes("ollama pull bge-m3"));
  const notRunning = rows({ embedding_check: "not_running", embedding_detail: "" });
  assert.ok(notRunning?.hint.includes("ollama serve"));

  // Older backend (no embedding_check at all) → legacy generic copy.
  const legacy = rows({});
  assert.ok(legacy?.hint.includes("语义检索会弱一些"));

  // Ready → no hint.
  const ready = rows({ embedding_ready: true, embedding_check: "ok" });
  assert.equal(ready?.hint, "");
});

test("embedding row becomes a hard prereq when the backend requires it", () => {
  // v0.3.137+ a configured embedding provider hard-gates can_start server-side;
  // the popup used to hardcode the row soft + "非必须", contradicting the
  // blocked start button (field report 2026-07-05).
  const status = statusWith({
    can_start: false,
    reason: "embedding_not_ready",
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: false,
      embedding_required: true,
      enabled_platforms: ["bilibili"],
    },
  });
  const row = buildInitChecklist(status, ["bilibili"]).find((r) => r.key === "embedding");
  assert.equal(row?.hard, true);
  assert.equal(row?.label, "向量模型可用");
  assert.ok(!row?.hint.includes("也能初始化"));
  assert.equal(hardPrereqsSatisfied(status, ["bilibili"]), false);

  // embedding_not_ready now maps to a real message instead of the generic
  // "以下条件未满足" fallback.
  assert.ok(describeInitReason("embedding_not_ready").includes("向量模型"));

  // Optional (not required) keeps the soft row and legacy label.
  const optional = statusWith({
    prerequisites: {
      bilibili_logged_in: true,
      bilibili_check: "ok",
      llm_ready: true,
      embedding_ready: false,
      embedding_required: false,
      enabled_platforms: ["bilibili"],
    },
  });
  const softRow = buildInitChecklist(optional, ["bilibili"]).find((r) => r.key === "embedding");
  assert.equal(softRow?.hard, false);
  assert.equal(softRow?.label, "向量模型可用（推荐，非必须）");
});

// ── init-progress-visibility Phase 2: intra-stage fraction / clamp / staleness ──

import {
  INIT_EXPECTATION_HINT,
  INIT_STALL_THRESHOLD_SECONDS,
  resetInitProgressViewState,
  stageEtaText,
  stalenessView,
} from "../popup/popup-init-control.js";

function runningStage2Status(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return statusWith({
    running: true,
    run_id: (overrides.run_id as string) || "run-frac",
    current_stage: 2,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "running", reason: null },
      { n: 3, label: "生成画像", status: "pending", reason: null },
      { n: 4, label: "发现内容池", status: "pending", reason: null },
    ],
    ...overrides,
  });
}

function stage2With(progress: unknown, eta: number | null, runId: string) {
  return runningStage2Status({
    run_id: runId,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null, eta_seconds: 90 },
      { n: 2, label: "分析偏好", status: "running", reason: null, progress, eta_seconds: eta },
      { n: 3, label: "生成画像", status: "pending", reason: null, eta_seconds: 70 },
      { n: 4, label: "发现内容池", status: "pending", reason: null, eta_seconds: 120 },
    ],
  });
}

test("pct advances per completed chunk when stage progress is present", () => {
  resetInitProgressViewState();
  const t = 1_000_000;
  const pcts = [0, 2, 4, 8].map(
    (done) =>
      initProgressView(stage2With({ done, total: 8, note: `第 ${done}/8 批` }, 180, "run-chunks"), t)
        .pct,
  );
  // done/total fraction: 0/8→25, 2/8→31, 4/8→38, 8/8 capped 0.95→49.
  assert.deepEqual(pcts, [25, 31, 38, 49]);
});

test("running stage label appends the sub-progress note", () => {
  resetInitProgressViewState();
  const view = initProgressView(
    stage2With({ done: 3, total: 8, note: "第 3/8 批" }, 180, "run-note"),
    5_000,
  );
  assert.equal(view.stageLabel, "2/4 分析偏好 · 第 3/8 批");
});

test("eta-based pseudo progress advances with elapsed time and caps at 0.95", () => {
  resetInitProgressViewState();
  const t0 = 2_000_000;
  const status = () => stage2With(null, 180, "run-eta");
  const first = initProgressView(status(), t0);
  assert.equal(first.pct, 25); // elapsed 0 → fraction 0
  const later = initProgressView(status(), t0 + 180_000); // one eta → 1-1/e ≈ .63
  assert.ok(later.pct >= 40 && later.pct <= 41, `got ${later.pct}`);
  const capped = initProgressView(status(), t0 + 3_600_000);
  assert.equal(capped.pct, 49); // (1 + 0.95) / 4 → never claims the stage done
});

test("legacy status without new fields keeps the historic static ticks", () => {
  resetInitProgressViewState();
  // No run_id, no progress, no eta_seconds → the old 0.5 half-step (38%).
  const legacy = statusWith({
    running: true,
    current_stage: 2,
    stages: [
      { n: 1, label: "拉取数据", status: "ok", reason: null },
      { n: 2, label: "分析偏好", status: "running", reason: null },
      { n: 3, label: "生成画像", status: "pending", reason: null },
      { n: 4, label: "发现内容池", status: "pending", reason: null },
    ],
  });
  assert.equal(initProgressView(legacy).pct, 38);
});

test("pct is monotonic per run_id even when statuses regress out of order", () => {
  resetInitProgressViewState();
  const t = 3_000_000;
  const high = initProgressView(
    stage2With({ done: 6, total: 8, note: null }, 180, "run-clamp"),
    t,
  ).pct;
  assert.ok(high >= 43);
  // A stale poll result arrives late with less progress → clamp holds.
  const regressed = initProgressView(
    stage2With({ done: 1, total: 8, note: null }, 180, "run-clamp"),
    t + 1000,
  ).pct;
  assert.equal(regressed, high);
  // A different run starts fresh (no clamp bleed across runs).
  resetInitProgressViewState();
  const fresh = initProgressView(
    stage2With({ done: 1, total: 8, note: null }, 180, "run-clamp-2"),
    t + 2000,
  ).pct;
  assert.ok(fresh < high);
});

test("20-step simulated status sequence is non-decreasing and ends at 100", () => {
  resetInitProgressViewState();
  const runId = "run-sim";
  const t0 = 9_000_000;
  const mk = (stages: unknown[], extra: Record<string, unknown> = {}) =>
    statusWith({ running: true, run_id: runId, stages, ...extra });
  const S = (n: number, label: string, status: string, progress: unknown = null, eta: number | null = null) => {
    const s: Record<string, unknown> = { n, label, status, reason: null };
    if (progress) s.progress = progress;
    if (eta !== null) s.eta_seconds = eta;
    return s;
  };
  const L = ["拉取数据", "分析偏好", "生成画像", "发现内容池"];
  const seq: Array<Record<string, unknown>> = [
    // stage 1 running, per-source progress
    mk([S(1, L[0], "running", { done: 0, total: 2, note: "正在采集 B 站" }, 90), S(2, L[1], "pending"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 1 }),
    mk([S(1, L[0], "running", { done: 1, total: 2, note: "正在采集 Reddit" }, 90), S(2, L[1], "pending"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 1 }),
    // stale out-of-order frame (regressed to done 0)
    mk([S(1, L[0], "running", { done: 0, total: 2 }, 90), S(2, L[1], "pending"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 1 }),
    // stage 1 done, stage 2 running with chunk progress 0..8 (one frame missing fields)
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 0, total: 8, note: "第 0/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 1, total: 8, note: "第 1/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running"), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }), // legacy-shaped frame, no new fields
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 3, total: 8, note: "第 3/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 2, total: 8 }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }), // out-of-order regress
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 5, total: 8, note: "第 5/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 6, total: 8 }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 7, total: 8 }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "running", { done: 8, total: 8, note: "第 8/8 批" }, 180), S(3, L[2], "pending"), S(4, L[3], "pending")], { current_stage: 2 }),
    // stages 3+4 run in parallel (eta pseudo progress, then one gets progress)
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "running", null, 70), S(4, L[3], "running", null, 120)], { current_stage: 3 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "running", null, 70), S(4, L[3], "running", null, 120)], { current_stage: 3 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "running", null, 70), S(4, L[3], "running", null, 120)], { current_stage: 3 }),
    // profile lands, discovery still running
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    mk([S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "running", null, 120)], { current_stage: 4 }),
    // terminal frame
    statusWith({
      running: false,
      initialized: true,
      run_id: runId,
      current_stage: 4,
      stages: [S(1, L[0], "ok"), S(2, L[1], "ok"), S(3, L[2], "ok"), S(4, L[3], "ok")],
    }),
  ];
  assert.equal(seq.length, 20);
  let prev = -1;
  seq.forEach((status, i) => {
    const pct = initProgressView(status, t0 + i * 10_000).pct;
    assert.ok(pct >= prev, `step ${i}: pct ${pct} regressed below ${prev}`);
    prev = pct;
  });
  assert.equal(prev, 100);
});

test("stalenessView flips to the stall copy after 90s without backend activity", () => {
  resetInitProgressViewState();
  const t0 = 5_000_000;
  const status = (activity: string, sequence: number) =>
    runningStage2Status({ run_id: "run-stale", last_activity: activity, sequence });
  const fresh = stalenessView(status("2026-07-10 08:00:00", 7), t0);
  assert.equal(fresh.fresh, true);
  assert.ok(fresh.text.includes("进行中"));
  // Same activity marker 91s later → stalled, amber copy.
  const stalled = stalenessView(status("2026-07-10 08:00:00", 7), t0 + 91_000);
  assert.equal(stalled.fresh, false);
  assert.ok(stalled.staleSeconds > INIT_STALL_THRESHOLD_SECONDS);
  assert.ok(stalled.text.includes("没有新进展"));
  assert.ok(stalled.text.includes("取消"));
  // Backend writes again (heartbeat) → fresh again.
  const revived = stalenessView(status("2026-07-10 08:01:35", 8), t0 + 95_000);
  assert.equal(revived.fresh, true);
});

test("stalenessView stays fresh on old backends without last_activity", () => {
  resetInitProgressViewState();
  const status = runningStage2Status({ run_id: "run-old" });
  delete (status as Record<string, unknown>).last_activity;
  const t0 = 6_000_000;
  stalenessView(status, t0);
  const later = stalenessView(status, t0 + 600_000);
  assert.equal(later.fresh, true); // no heartbeat signal → never claim a stall
});

test("stalenessView is inert for non-running statuses", () => {
  resetInitProgressViewState();
  const idle = stalenessView(statusWith(), 1_000);
  assert.equal(idle.fresh, true);
  assert.equal(idle.text, "");
});

test("stageEtaText rounds up to half minutes and expectation copy exists", () => {
  assert.equal(stageEtaText({ eta_seconds: 180 }), "本阶段通常约 3 分钟");
  assert.equal(stageEtaText({ eta_seconds: 70 }), "本阶段通常约 1.5 分钟");
  assert.equal(stageEtaText({ eta_seconds: 120 }), "本阶段通常约 2 分钟");
  assert.equal(stageEtaText({}), "");
  assert.equal(stageEtaText(null), "");
  assert.ok(INIT_EXPECTATION_HINT.includes("2–5 分钟"));
  assert.ok(INIT_EXPECTATION_HINT.includes("进度会保留"));
});

test("shouldAttachRunningInitProgress restores live, failed, and CLI-only recovery", () => {
  // A run in flight (popup opened / refreshed mid-init, started elsewhere so no
  // click or SSE kicked the poll here) → re-attach the progress poll.
  assert.equal(shouldAttachRunningInitProgress(statusWith({ running: true })), true);
  assert.equal(
    shouldAttachRunningInitProgress(
      statusWith({ last_failure_reason: "analyze_failed", last_failure_detail: "超时" }),
    ),
    true,
  );
  assert.equal(shouldAttachRunningInitProgress(statusWith({ start_mode: "cli_only" })), true);
  assert.equal(initStartMode(statusWith({ reason: "unsupported_runtime" })), "cli_only");
  assert.equal(shouldAttachRunningInitProgress(statusWith({ reason: "unsupported_runtime" })), true);
  // Fresh web-capable idle state leaves the idle panel untouched.
  assert.equal(shouldAttachRunningInitProgress(statusWith({ running: false })), false);
  // Missing/legacy status must never throw or falsely attach.
  assert.equal(shouldAttachRunningInitProgress(null), false);
  assert.equal(shouldAttachRunningInitProgress(undefined), false);
  assert.equal(shouldAttachRunningInitProgress({}), false);
});
