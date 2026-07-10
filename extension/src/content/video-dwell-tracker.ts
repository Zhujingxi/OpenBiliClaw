/**
 * Pure video-page dwell tracker for the satisfaction signal.
 *
 * The kernel calls `enter(url, durationSeconds)` when the user lands on a
 * video page and `flush(reason)` on SPA navigation away (`pushState` /
 * `replaceState` / `popstate`) or `pagehide`. The tracker emits one
 * synthesised `click` BehaviorEvent for the previous page with
 * `metadata.watch_seconds` (and `metadata.video_duration_seconds` if
 * known) so the storage classifier can mark the visit as a quick-exit,
 * meaningful_dwell, or unknown.
 *
 * Pure by construction: takes the clock and emitter as injected
 * dependencies so node:test can drive the lifecycle without a browser.
 */

import type { BehaviorEvent } from "../shared/types.js";

/** Anything the kernel needs to assemble a final event for the previous page. */
export interface DwellEventBuilder {
  (
    previousUrl: string,
    metadata: Record<string, unknown>,
  ): BehaviorEvent | null;
}

export interface VideoDwellTrackerOptions {
  /** Wall-clock source. Inject `() => performance.now()` from the kernel. */
  now: () => number;
  /** Called when the tracker is ready to emit a finalised dwell event. */
  emit: (event: BehaviorEvent) => void;
  /** Builds the BehaviorEvent for the previous page when dwell is flushed. */
  buildEvent: DwellEventBuilder;
}

/**
 * `playback` — video pages, segments driven by play/pause/ended.
 * `visible` — content pages, segments driven by tab visibility.
 */
export type DwellMode = "playback" | "visible";

interface DwellSession {
  url: string;
  mode: DwellMode;
  /** Wall-clock entry time — the basis for `page_dwell_seconds`. */
  enteredAt: number;
  /** Playing/visible time accumulated from closed segments (ms). */
  accumulatedMs: number;
  /** Start of the currently-open segment, or null when paused/hidden. */
  segmentStartedAt: number | null;
  videoDurationSeconds: number | null;
}

// When the video duration is unknown, cap reported watch_seconds so a lost
// pause/ended event can't produce an absurd value. Only applied when the raw
// playing time exceeds it — smaller raw values pass through untouched.
const _WATCH_SECONDS_FALLBACK_CAP = 600;

export class VideoDwellTracker {
  private session: DwellSession | null = null;
  private readonly options: VideoDwellTrackerOptions;

  constructor(options: VideoDwellTrackerOptions) {
    this.options = options;
  }

  /**
   * Mark that the user entered a video page. If a prior session for a
   * *different* URL was still open, it is flushed first so we never
   * silently drop dwell. A redundant re-enter on the same URL (e.g. a
   * duplicate replaceState) is a no-op so accumulated playing time
   * survives.
   */
  enter(
    url: string,
    videoDurationSeconds: number | null = null,
    mode: DwellMode = "playback",
  ): void {
    if (this.session !== null) {
      if (this.session.url === url) return;
      this.flush("interrupted");
    }
    this.session = {
      url,
      mode,
      enteredAt: this.options.now(),
      accumulatedMs: 0,
      segmentStartedAt: null,
      videoDurationSeconds,
    };
  }

  /** The current session's dwell mode, or null when idle. */
  currentMode(): DwellMode | null {
    return this.session?.mode ?? null;
  }

  /**
   * Apply a tab visibility transition. Only `visible`-mode sessions
   * respond — `playback` sessions are gated purely by play state, so
   * background audio keeps counting. `hidden` closes the open segment;
   * becoming visible opens a new one.
   */
  handleVisibilityChange(hidden: boolean): void {
    if (this.session === null || this.session.mode !== "visible") return;
    if (hidden) {
      this.endSegment();
    } else {
      this.beginSegment();
    }
  }

  /**
   * Begin a playing segment (video `play`, or bind-time when already
   * playing). Idempotent — a second call while a segment is open does
   * nothing (never resets the start).
   */
  beginSegment(): void {
    if (this.session === null) return;
    if (this.session.segmentStartedAt !== null) return;
    this.session.segmentStartedAt = this.options.now();
  }

  /**
   * End the open playing segment (video `pause` / `ended`), folding its
   * duration into the accumulator. Idempotent when no segment is open.
   */
  endSegment(): void {
    if (this.session === null) return;
    if (this.session.segmentStartedAt === null) return;
    this.session.accumulatedMs += this.options.now() - this.session.segmentStartedAt;
    this.session.segmentStartedAt = null;
  }

  /**
   * Update the known video duration mid-session. Useful when the
   * <video> element finishes loading metadata after the user arrived.
   */
  updateDuration(videoDurationSeconds: number | null): void {
    if (this.session === null) return;
    if (videoDurationSeconds === null) return;
    if (!Number.isFinite(videoDurationSeconds)) return;
    this.session.videoDurationSeconds = videoDurationSeconds;
  }

  /**
   * Flush the in-flight dwell. Called on SPA route change, `pagehide`,
   * or a fresh `enter()` on a different URL. Reports `watch_seconds`
   * (playing time only) and `page_dwell_seconds` (wall-clock). Returns
   * the emitted event (or null when there was no session to flush, or
   * the buildEvent adapter rejected it).
   */
  flush(reason: string): BehaviorEvent | null {
    if (this.session === null) return null;
    const now = this.options.now();

    let playingMs = this.session.accumulatedMs;
    if (this.session.segmentStartedAt !== null) {
      playingMs += now - this.session.segmentStartedAt;
    }
    let watchSeconds = Math.max(0, Number((playingMs / 1000).toFixed(2)));
    const isPlayback = this.session.mode === "playback";
    const duration = this.session.videoDurationSeconds;
    // Only the playback path clamps against a known duration; content pages
    // fall back to the constant cap.
    const cap = isPlayback && duration !== null ? duration * 1.5 : _WATCH_SECONDS_FALLBACK_CAP;
    if (watchSeconds > cap) {
      watchSeconds = Number(cap.toFixed(2));
    }

    const pageDwellSeconds = Math.max(0, Number(((now - this.session.enteredAt) / 1000).toFixed(2)));

    const metadata: Record<string, unknown> = {
      watch_seconds: watchSeconds,
      page_dwell_seconds: pageDwellSeconds,
      dwell_source: isPlayback ? "video_page_exit" : "content_page_exit",
      dwell_reason: reason,
    };
    // Content pages carry no video duration — only the playback path
    // reports it (and only the playback path clamps against it).
    if (isPlayback && duration !== null) {
      metadata.video_duration_seconds = duration;
    }

    const event = this.options.buildEvent(this.session.url, metadata);
    this.session = null;
    if (event === null) return null;
    this.options.emit(event);
    return event;
  }

  /** True iff a dwell session is currently in flight. */
  hasActiveSession(): boolean {
    return this.session !== null;
  }
}
