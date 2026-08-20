export type ExtensionErrorCode =
  | "accessUnavailable"
  | "backendConnectionFailed"
  | "backendUnavailable"
  | "connectionFailed"
  | "credentialVerificationFailed"
  | "extensionApiUnavailable"
  | "invalidRecipeResponse"
  | "invalidSourceResponse"
  | "invalidToken"
  | "invalidUrl"
  | "recipeLookupFailed"
  | "requiredArtifactUnavailable"
  | "sitePermissionDenied";

export interface ExtensionIssue {
  readonly code: ExtensionErrorCode;
  readonly status?: number;
}

export class ExtensionFailure extends Error {
  readonly code: ExtensionErrorCode;
  readonly status: number | undefined;

  constructor(code: ExtensionErrorCode, status?: number) {
    super(code);
    this.name = "ExtensionFailure";
    this.code = code;
    this.status = status;
  }
}

export function extensionIssue(error: unknown): ExtensionIssue {
  return error instanceof ExtensionFailure
    ? {
        code: error.code,
        ...(error.status === undefined ? {} : { status: error.status }),
      }
    : { code: "accessUnavailable" };
}
