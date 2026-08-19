export type LoadPhase = "idle" | "loading" | "success" | "empty" | "error";

export function errorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message : "Unexpected failure";
  const normalized = detail.toLowerCase();
  if (normalized.includes("request validation failed"))
    return `Check the submitted fields and try again. (${detail})`;
  if (normalized.includes("capability is not configured"))
    return `The AI assistant is not configured. (${detail})`;
  if (normalized.includes("source is not connected"))
    return `Connect this source before using it. (${detail})`;
  if (normalized.includes("temporary failure"))
    return `The service could not complete the request. Try again. (${detail})`;
  return detail;
}

export function isCancellation(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export class RequestOwner {
  #controller: AbortController | undefined;

  next(): AbortSignal {
    this.cancel();
    this.#controller = new AbortController();
    return this.#controller.signal;
  }

  owns(signal: AbortSignal): boolean {
    return this.#controller?.signal === signal && !signal.aborted;
  }

  cancel(): void {
    this.#controller?.abort();
    this.#controller = undefined;
  }
}
