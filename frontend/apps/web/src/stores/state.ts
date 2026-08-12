export type LoadPhase = "idle" | "loading" | "success" | "empty" | "error";

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected failure";
}

export class RequestOwner {
  #controller: AbortController | undefined;

  next(): AbortSignal {
    this.cancel();
    this.#controller = new AbortController();
    return this.#controller.signal;
  }

  cancel(): void {
    this.#controller?.abort();
    this.#controller = undefined;
  }
}
