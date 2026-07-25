/**
 * Pure helpers that turn a MAIN-world `XhsAction` (from `xhs-action-tap.ts`)
 * into the unified `BehaviorEvent` the isolated content script forwards to
 * `/api/events`.
 *
 * Kept in its own module (not in `content/xiaohongshu.ts`) because that entry
 * runs DOM side effects at import time; node:test needs to exercise these
 * builders without booting the collector.
 */

import type { XhsAction } from "../../main/xhs-action-tap.js";
import type { BehaviorEvent } from "../../shared/types.js";
import { xiaohongshuAdapter } from "../../shared/platforms/xiaohongshu.ts";

// Strength recorded for a retraction — matches the backend's 0.2 default and
// the X/bilibili retraction paths. A retraction is a neutralization, not a
// positive vote.
const RETRACTION_SIGNAL_STRENGTH = 0.2;

/** Canonical note URL from a 24-hex note id. Interoperates with the backend's
 * `note_id_from_url` (sources/identity_keys.py), so retraction discounting
 * lines the note up with the positive event it undoes. */
export function xhsNoteUrl(noteId: string): string {
  return `https://www.xiaohongshu.com/explore/${noteId}`;
}

/** Best-effort current page href (safe under node --test where window is absent). */
function currentHref(): string {
  return typeof window !== "undefined" ? window.location.href : "";
}

/** Structural guard for a message payload claiming to be an `XhsAction`. */
export function isXhsAction(value: unknown): value is XhsAction {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  if (v.type !== "like" && v.type !== "favorite" && v.type !== "retraction") return false;
  if (typeof v.note_id !== "string" || !/^[0-9a-f]{24}$/i.test(v.note_id)) return false;
  return true;
}

/**
 * Normalize a captured `XhsAction` into the unified BehaviorEvent. A
 * `retraction` (unlike / uncollect) becomes a neutral `feedback` event
 * carrying `feedback_type="retraction"`, the withdrawn action, and an explicit
 * 0.2 signal strength; a like / favorite passes through as that event type.
 * Pure — no side effects.
 */
export function buildEventFromXhsAction(action: XhsAction): BehaviorEvent {
  const url = xhsNoteUrl(action.note_id);
  const hasWindow = typeof window !== "undefined";
  const hasDocument = typeof document !== "undefined";

  const metadata: Record<string, unknown> = { note_id: action.note_id };

  let type: string = action.type;
  if (action.type === "retraction") {
    type = "feedback";
    metadata.feedback_type = "retraction";
    if (action.retracted_action) metadata.retracted_action = action.retracted_action;
    metadata.signal_strength = RETRACTION_SIGNAL_STRENGTH;
  }

  const href = currentHref();
  return {
    type,
    url,
    title: hasDocument ? document.title || "" : "",
    timestamp: Date.now(),
    source_platform: xiaohongshuAdapter.sourcePlatform,
    context: {
      pageType: xiaohongshuAdapter.detectPageType(href || url),
      viewport: {
        width: hasWindow ? window.innerWidth : 0,
        height: hasWindow ? window.innerHeight : 0,
      },
      scrollPosition: hasWindow ? window.scrollY : 0,
    },
    metadata,
  };
}
