import { ApiError } from "@openbiliclaw/api-client";

export type LoadPhase = "idle" | "loading" | "success" | "empty" | "error";

export type ErrorTranslationKey =
  | "errors.conflict"
  | "errors.forbidden"
  | "errors.invalidResponse"
  | "errors.methodNotAllowed"
  | "errors.network"
  | "errors.notFound"
  | "errors.rateLimit"
  | "errors.requestFailed"
  | "errors.temporaryFailure"
  | "errors.unauthorized"
  | "errors.unavailable"
  | "errors.validation"
  | "recommendations.expired";

export interface UiError {
  readonly key: ErrorTranslationKey;
  readonly code?: string;
  readonly status?: number;
}

const ERROR_KEYS: Readonly<Record<string, ErrorTranslationKey>> = {
  conflict: "errors.conflict",
  forbidden: "errors.forbidden",
  method_not_allowed: "errors.methodNotAllowed",
  not_found: "errors.notFound",
  rate_limit: "errors.rateLimit",
  temporary_failure: "errors.temporaryFailure",
  unauthorized: "errors.unauthorized",
  unavailable_capability: "errors.unavailable",
  validation: "errors.validation",
};

/** Reduce boundary failures to stable, localizable presentation data. */
export function errorMessage(error: unknown): UiError {
  if (!(error instanceof ApiError)) return { key: "errors.requestFailed" };
  if (error.kind === "network") return { key: "errors.network" };
  if (error.kind === "invalid-response")
    return { key: "errors.invalidResponse" };
  return {
    key: error.code
      ? (ERROR_KEYS[error.code] ?? "errors.requestFailed")
      : "errors.requestFailed",
    ...(error.code === undefined ? {} : { code: error.code }),
    ...(error.status === undefined ? {} : { status: error.status }),
  };
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
