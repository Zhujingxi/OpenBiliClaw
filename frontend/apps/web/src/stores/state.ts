export type LoadPhase = "idle" | "loading" | "success" | "empty" | "error";

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected failure";
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
