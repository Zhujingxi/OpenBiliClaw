/**
 * Mobile saved-sync runtime: thin adapter over the canonical shared core.
 * The shared module owns the implementations; this adapter injects the
 * browser dependencies (activeElement, timers, visibility) the core requires
 * explicitly, preserving the call signatures the views already use.
 */
import {
  captureSavedFocus as coreCaptureSavedFocus,
  createDialogFocusController as coreCreateDialogFocusController,
  createDurableTaskTracker as coreCreateDurableTaskTracker,
  createRetainedSavedListState,
  createSavedMutationRegistry,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  isSavedTaskTerminal,
  restoreSavedFocus,
} from "../shared/saved-sync-core.js";

export {
  createRetainedSavedListState,
  createSavedMutationRegistry,
  createSavedSubmissionFence,
  createSavedTaskCoordinator,
  isSavedTaskTerminal,
  restoreSavedFocus,
};

export const captureSavedFocus = (
  root: unknown,
  activeElement: unknown = globalThis.document?.activeElement,
) =>
  coreCaptureSavedFocus(root, activeElement);

export const createDialogFocusController = (options: ObscDialogFocusControllerOptions = {}) =>
  coreCreateDialogFocusController({ document: globalThis.document, ...options });

export const createDurableTaskTracker = (options: ObscDurableTaskTrackerOptions = {}) =>
  coreCreateDurableTaskTracker({
    now: Date.now,
    isVisible: () => typeof document === "undefined" || !document.hidden,
    schedule: (run: () => void, delay: number) => setTimeout(run, delay),
    cancel: (handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    ...options,
  });
