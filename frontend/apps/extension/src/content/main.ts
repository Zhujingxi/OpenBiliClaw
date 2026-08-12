import { parseExtensionMessage } from "../shared/messages";

// The target content boundary carries presentation/connection messages only.
// Provider sessions, cookie extraction, page taps and task dispatch are excluded.
globalThis.addEventListener("message", (event: MessageEvent<unknown>) => {
  if (event.origin !== globalThis.location.origin) return;
  const parsed = parseExtensionMessage(event.data);
  if (parsed?.kind === "connection.status") {
    globalThis.dispatchEvent(
      new CustomEvent("openbiliclaw:connection-status", { detail: parsed }),
    );
  }
});
