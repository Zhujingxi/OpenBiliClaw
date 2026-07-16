/**
 * OpenBiliClaw — Bilibili content script entry (isolated world).
 *
 * Injected into bilibili.com pages. Two responsibilities:
 *
 *   1. Wire the generic collector kernel to the bilibili adapter
 *      (`startCollector`) for navigation / click / search / scroll context.
 *
 *   2. Listen for the MAIN-world interact tap's `postMessage`
 *      (`source: "obc-bili-interact"`) and forward each captured danmaku /
 *      comment as a `comment` BEHAVIOR_EVENT to the service worker → backend.
 *
 * The MAIN-world tap (`dist/main/bili-interact-tap.js`) runs at
 * document_start in `world: MAIN` (see manifest.json) and observes the user's
 * own `/x/v2/dm/post` and `/x/v2/reply/add` writes. It never mutates the
 * page's requests.
 */

import { bilibiliAdapter } from "../shared/platforms/bilibili.ts";
import type { BehaviorEvent } from "../shared/types.js";
import type { BiliInteraction } from "../main/bili-interact-tap.js";
import { COMMENT_TEXT_MAX_CHARS, sanitizeUserText } from "../shared/text-sanitize.ts";

// Danmaku evidence strength — mirrors the backend's danmaku default (0.6),
// below a written comment (0.75) because bullet chatter is more casual.
const DANMAKU_SIGNAL_STRENGTH = 0.6;

/** Best-effort current page href (safe under node --test where window is absent). */
function currentHref(): string {
  return typeof window !== "undefined" ? window.location.href : "";
}

export function isBiliInteraction(value: unknown): value is BiliInteraction {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (v.kind !== "danmaku" && v.kind !== "comment") return false;
  return typeof v.text === "string" && v.text.length > 0;
}

/**
 * Normalize a captured `BiliInteraction` into the unified `comment`
 * BEHAVIOR_EVENT forwarded to `/api/events`. The video URL is the current page
 * href (the user is on the video page when they danmaku / comment), so the
 * backend's bvid extraction works without the tap resolving aid → bvid.
 * The comment text is sanitized here (first defense; the backend repeats it).
 * Pure — no side effects.
 */
export function buildEventFromBiliInteraction(interaction: BiliInteraction): BehaviorEvent {
  const href = currentHref();
  const hasWindow = typeof window !== "undefined";
  const hasDocument = typeof document !== "undefined";

  const metadata: Record<string, unknown> = {
    comment_kind: interaction.kind === "danmaku" ? "danmaku" : "comment",
  };
  const cleaned = sanitizeUserText(interaction.text, COMMENT_TEXT_MAX_CHARS);
  if (cleaned) metadata.comment_text = cleaned;
  if (interaction.bvid) metadata.bvid = interaction.bvid;
  if (interaction.kind === "danmaku") metadata.signal_strength = DANMAKU_SIGNAL_STRENGTH;

  return {
    type: "comment",
    url: href,
    title: hasDocument ? document.title || "" : "",
    timestamp: Date.now(),
    source_platform: bilibiliAdapter.sourcePlatform,
    context: {
      pageType: bilibiliAdapter.detectPageType(href),
      viewport: {
        width: hasWindow ? window.innerWidth : 0,
        height: hasWindow ? window.innerHeight : 0,
      },
      scrollPosition: hasWindow ? window.scrollY : 0,
    },
    metadata,
  };
}

function sendEvent(event: BehaviorEvent): void {
  try {
    chrome.runtime.sendMessage({ action: "BEHAVIOR_EVENT", data: event });
  } catch {
    // best effort — never break the page
  }
}

// ── Side effects (collector kernel, task executor, interact-tap bridge) run
// only in a real browser; guarded so this module imports cleanly under
// node --test (mirrors content/x.ts). Dynamic imports keep the DOM-heavy
// kernel graph out of node:test's static analysis.
if (typeof window !== "undefined" && typeof chrome !== "undefined") {
  void import("./kernel.js").then(({ startCollector }) => {
    startCollector(bilibiliAdapter);
  });
  void import("./bili/task-executor.js").then(({ installBiliMessageListener }) => {
    installBiliMessageListener();
  });

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data as { source?: string; interaction?: unknown } | null;
    if (!data || data.source !== "obc-bili-interact") return;
    if (!isBiliInteraction(data.interaction)) return;
    sendEvent(buildEventFromBiliInteraction(data.interaction));
  });

  console.log(
    "[OpenBiliClaw] Bilibili behavior collector initialized on",
    bilibiliAdapter.detectPageType(window.location.href),
    "page",
  );
}
