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
  pendingChatPolls: /* @__PURE__ */ new Set(),
  pendingChatContext: null
};
const listeners = /* @__PURE__ */ new Set();
export function patchState(partial) {
  if (!partial || typeof partial !== "object") return;
  Object.assign(state, partial);
  for (const fn of listeners) {
    try {
      fn(state, partial);
    } catch {
    }
  }
}
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
//# sourceMappingURL=state.js.map
