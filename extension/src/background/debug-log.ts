/**
 * TEMP DEBUG: ship extension-side events to the daemon log so we
 * can trace behaviour across XHS + DY dispatchers without three
 * separate DevTools consoles. Will be reverted before release.
 */

import { apiUrl } from "../shared/backend-endpoint.ts";

export function debugLog(source: "xhs" | "dy" | "sw", event: string, data?: unknown): void {
  // Fire-and-forget. If daemon is down, swallow silently — dispatcher
  // operation must not depend on debug logging.
  void (async () => {
    try {
      await fetch(await apiUrl("/sources/_debug/log"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source, event, data: data ?? null }),
      });
    } catch {
      // ignore
    }
  })();
  // Mirror to local SW console for users with the dev tools open.
  // eslint-disable-next-line no-console
  console.debug(`[obc-${source}] ${event}`, data ?? "");
}
