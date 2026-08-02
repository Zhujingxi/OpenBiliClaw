export type PageType = string;

export interface BehaviorContext {
  pageType: PageType;
  domSnapshot?: string;
  viewport: { width: number; height: number };
  scrollPosition: number;
}

export interface BehaviorEvent {
  /** Producer-owned identity retained across MV3 storage/retry/parking. */
  event_id?: string;
  type: string;
  url: string;
  title: string;
  timestamp: number;
  source_platform: string;
  context: BehaviorContext;
  metadata: Record<string, unknown>;
}

export interface ActionHint {
  text: string | null;
  ariaLabel: string | null;
  className: string;
  /**
   * `aria-pressed` state of the attributed control:
   * `true` / `false` for the two explicit states, `null` when the
   * attribute is absent or carries any other value (fail open).
   * A pressed like/favorite/follow control means the click withdraws
   * the action (a retraction).
   */
  pressed: boolean | null;
}

/**
 * Platform-specific logic injected into the generic collector kernel.
 *
 * One adapter per site (bilibili, xiaohongshu, ...). The kernel handles
 * DOM observation, debouncing, and transport; adapters handle what
 * counts as a "card", how to extract a content id, and how to classify
 * pages/actions for that site.
 */
export interface PlatformAdapter {
  /** Identifier stored on every event, e.g. "bilibili" | "xiaohongshu". */
  readonly sourcePlatform: string;

  /**
   * Actions for which a MAIN-world network tap is the authoritative source,
   * so the generic DOM click path must NOT emit them (it would double-count
   * with the tap and would fire "opened the menu = an event" false actions).
   *
   * Keys are `inferActionType` outputs (`"like"`, `"favorite"`, `"share"`,
   * `"comment"`, …) plus the literal `"retraction"` for a pressed-control
   * withdrawal. Undeclared actions and platforms with no tap keep the DOM
   * path as their source of truth.
   *
   * X declares `{like, favorite, share, comment, retraction}` (its GraphQL
   * tap emits all five); bilibili declares
   * `{comment, like, favorite, coin, retraction}` via its interact tap;
   * xiaohongshu declares `{like, favorite, retraction}` via its action tap.
   */
  readonly tapAuthoritativeActions?: ReadonlySet<string>;

  /** Classify the current URL into a coarse page type for context. */
  detectPageType(url: string): PageType;

  /**
   * Pull the platform's canonical content identifier from a URL
   * (bvid for bilibili, note_id for xiaohongshu, etc.). Null if the
   * URL doesn't point at a single piece of content.
   */
  extractContentId(url: string): string | null;

  /**
   * Pull the search query from a result-page URL, following this
   * adapter's own `detectPageType` search patterns (query param or path
   * segment). Returns null for query-less search pages (e.g. X `/explore`)
   * so the kernel emits nothing. Covers Enter, search-button clicks, and
   * suggestion clicks — the result URL is the ground truth.
   */
  extractSearchQuery?(url: string): string | null;

  /**
   * CSS selector for clickable content cards in the feed. Used by
   * hover observation and click target detection.
   */
  readonly cardSelector: string;

  /**
   * CSS selector for search input fields on this platform. Enter
   * keypresses inside matching inputs emit `search` events.
   */
  readonly searchInputSelector: string;

  /**
   * CSS selector for the main video element (if any). When null the
   * kernel skips video observation — xhs and most web sources don't
   * have a single play/pause-able player worth tracking.
   */
  readonly videoSelector: string | null;

  /**
   * PageTypes whose dwell is worth measuring. Video platforms use
   * `["video"]` (play-state gated); content platforms opt their reading
   * pages in (visibility gated). Defaults to `["video"]` when omitted.
   */
  readonly dwellPageTypes?: string[];

  /** Map a clicked element's text/aria/className hint to a strong-signal action type. */
  inferActionType(hint: ActionHint): string | null;

  /**
   * Build platform-specific metadata to attach to every event
   * (e.g. `{bvid}` for bilibili, `{note_id}` for xhs). The kernel
   * always sets `source_platform` + `content_id` separately.
   */
  buildEventMetadata(url: string): Record<string, unknown>;

  /**
   * Optional target-specific metadata for click/action events. This is
   * needed on feed pages where the current URL is a list page but the user
   * clicked a specific card's action button.
   */
  buildTargetMetadata?(target: Element, currentUrl: string): Record<string, unknown>;
}
