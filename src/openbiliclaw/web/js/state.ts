/**
 * Centralized mobile UI state with subscription support.
 *
 * Views read from `state` and call `patchState(partial)` to update.
 * Shell (app.js) subscribes to re-render status bar, badge, tab bar.
 * Active view subscribes to re-render its own content.
 */

export const state = {
  authEnabled: false,
  authenticated: true,
  needsLogin: false,
  activeTab: "recommend",
  online: false,
  degraded: false,
  degradedReason: "",
  runtimeStatus: null,
  runtimeEvent: null,
  activityFeed: null,
  activityExpanded: false,
  recommendations: [],
  activeDelights: [],
  delightCurrentIndex: 0,
  messages: { notifications: [], delights: [] },
  profile: null,
  chatTurns: [],
  pendingChatPolls: new Set(),
  pendingChatContext: null,
};

type StateListener = (current: typeof state, changed: object) => void;

const listeners = new Set<StateListener>();

/**
 * Shallow-merge partial into state and notify listeners.
 * For Set/Array fields, callers must pass a new collection (no in-place mutation).
 */
export function patchState(partial: object | null | undefined): void {
  if (!partial || typeof partial !== "object") return;
  Object.assign(state, partial);
  for (const fn of listeners) {
    try { fn(state, partial); } catch { /* listener errors don't block others */ }
  }
}

/**
 * Subscribe to state changes. Returns an unsubscribe function.
 * @param {(state: object, changed: object) => void} listener
 */
export function subscribe(listener: StateListener): () => boolean {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
