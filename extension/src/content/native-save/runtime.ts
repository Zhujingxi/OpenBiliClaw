import {
  isAllowedNativeSaveHostname,
  isNativeSaveTask,
  sanitizeNativeSaveResult,
  type NativeSavePlatform,
  type NativeSaveTask,
  type SanitizedNativeSaveOutcome,
} from "../../shared/native-save.ts";

export type NativeSaveExecutor = (
  task: NativeSaveTask,
) => Promise<unknown> | unknown;

const installedPlatforms = new Set<NativeSavePlatform>();
const executedTaskIds = new Set<string>();

/** Install one platform executor. Platform entrypoints are intentionally wired in later tasks. */
export function installNativeSaveExecutor(
  platform: NativeSavePlatform,
  executor: NativeSaveExecutor,
): void {
  if (installedPlatforms.has(platform)) return;
  installedPlatforms.add(platform);

  chrome.runtime.onMessage.addListener(async (message: unknown) => {
    if (typeof message !== "object" || message === null) return false;
    const envelope = message as { type?: unknown; task?: unknown };
    if (envelope.type !== "NATIVE_SAVE_EXECUTE" || !isNativeSaveTask(envelope.task)) return false;
    const task = envelope.task;
    if (task.platform !== platform || !isAllowedNativeSaveHostname(platform, location.hostname)) {
      return false;
    }
    if (executedTaskIds.has(task.id)) return true;
    executedTaskIds.add(task.id);

    let outcome: SanitizedNativeSaveOutcome;
    try {
      outcome = sanitizeNativeSaveResult(await executor(task));
    } catch {
      outcome = sanitizeNativeSaveResult({ status: "failed", error_code: "native_save_failed" });
    }
    await chrome.runtime.sendMessage({
      type: "NATIVE_SAVE_RESULT",
      platform,
      task_id: task.id,
      item_key: task.item_key,
      ...outcome,
    });
    return true;
  });
}
