import type { BehaviorEvent } from "../shared/types.ts";

type HandoffResponse = { readonly accepted?: boolean } | undefined;
type SendMessage = (message: Record<string, unknown>) => Promise<HandoffResponse>;
type Delay = (milliseconds: number) => Promise<void>;

const RETRY_DELAYS_MS = [0, 100, 500] as const;

/** Keep the content-side event alive until the service worker confirms persistence. */
export async function handoffBehaviorEvent(
  event: BehaviorEvent,
  send: SendMessage = (message) => chrome.runtime.sendMessage(message),
  delay: Delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
): Promise<boolean> {
  const message = { action: "BEHAVIOR_EVENT", data: event };
  for (const milliseconds of RETRY_DELAYS_MS) {
    if (milliseconds > 0) await delay(milliseconds);
    try {
      if ((await send(message))?.accepted === true) return true;
    } catch {
      // The exact same event is retried below; never disturb the host page.
    }
  }
  return false;
}
