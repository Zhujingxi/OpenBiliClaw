import {
  captureSavedFocus as coreCaptureSavedFocus,
  createDialogFocusController as coreCreateDialogFocusController,
  createDurableTaskTracker as coreCreateDurableTaskTracker,
  createRetainedSavedListState,
  createSavedMutationRegistry,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  isSavedTaskTerminal,
  restoreSavedFocus
} from "../shared/saved-sync-core.js";
export {
  createRetainedSavedListState,
  createSavedMutationRegistry,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  isSavedTaskTerminal,
  restoreSavedFocus
};
export const captureSavedFocus = (root, activeElement = globalThis.document?.activeElement) => coreCaptureSavedFocus(root, activeElement);
export const createDialogFocusController = (options = {}) => coreCreateDialogFocusController({ document: globalThis.document, ...options });
export const createDurableTaskTracker = (options = {}) => coreCreateDurableTaskTracker({
  now: Date.now,
  isVisible: () => typeof document === "undefined" || !document.hidden,
  schedule: (run, delay) => setTimeout(run, delay),
  cancel: (handle) => clearTimeout(handle),
  ...options
});
//# sourceMappingURL=saved-sync-runtime.js.map
