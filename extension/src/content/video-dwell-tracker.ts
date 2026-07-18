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

interface DwellSession {
  url: string;
  startedAt: number;
  videoDurationSeconds: number | null;
}

export class VideoDwellTracker {
  private session: DwellSession | null = null;
  private readonly options: VideoDwellTrackerOptions;

  constructor(options: VideoDwellTrackerOptions) {
    this.options = options;
  }

  /**
   * Mark that the user entered a video page. If a prior session was
   * still open (no flush happened between two consecutive enters), it
   * is flushed first so we never silently drop dwell.
   */
  enter(url: string, videoDurationSeconds: number | null = null): void {
    if (this.session !== null && this.session.url !== url) {
      this.flush("interrupted");
    }
    this.session = {
      url,
      startedAt: this.options.now(),
      videoDurationSeconds,
    };
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
   * or a fresh `enter()` on a different URL. Returns the emitted event
   * (or null when there was no session to flush, or the buildEvent
   * adapter rejected it).
   */
  flush(reason: string): BehaviorEvent | null {
    if (this.session === null) return null;
    const elapsed = (this.options.now() - this.session.startedAt) / 1000;
    const watchSeconds = Math.max(0, Number(elapsed.toFixed(2)));

    const metadata: Record<string, unknown> = {
      watch_seconds: watchSeconds,
      dwell_source: "video_page_exit",
      dwell_reason: reason,
    };
    if (this.session.videoDurationSeconds !== null) {
      metadata.video_duration_seconds = this.session.videoDurationSeconds;
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
