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
import type {
  DialogFocusControllerOptions,
  DurableTaskTrackerOptions,
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
  root: Parameters<typeof coreCaptureSavedFocus>[0],
  activeElement: Parameters<typeof coreCaptureSavedFocus>[1] = globalThis.document?.activeElement,
) => coreCaptureSavedFocus(root, activeElement);

export const createDialogFocusController = (options: DialogFocusControllerOptions = {}) =>
  coreCreateDialogFocusController({ document: globalThis.document, ...options });

export const createDurableTaskTracker = (options: DurableTaskTrackerOptions = {}) =>
  coreCreateDurableTaskTracker({
    now: Date.now,
    isVisible: () => typeof document === "undefined" || !document.hidden,
    schedule: (run: () => void, delay: number) => setTimeout(run, delay),
    cancel: (handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    ...options,
  });
