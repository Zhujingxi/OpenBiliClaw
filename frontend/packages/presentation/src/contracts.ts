import type { components } from "@openbiliclaw/api-client/generated";

/** Canonical backend DTO aliases; never duplicate these shapes by hand. */
export type ContentPreview = components["schemas"]["ContentPreview"];
export type ContentRef = components["schemas"]["ContentRef"];
export type ActionResult = components["schemas"]["ActionResult"];
export type CardData = components["schemas"]["CardData"];

export type CardKind =
  | "video"
  | "image"
  | "article"
  | "discussion"
  | "fallback";
export type Availability = "available" | "deleted" | "provider-unavailable";

/** UI-local shell metadata around canonical CardData. */
export interface CardView {
  readonly data: CardData;
  readonly version: number;
  readonly kind: CardKind;
  readonly providerLabel: string;
  readonly availability: Availability;
}

export interface SafeCardDescriptor {
  readonly version: number;
  readonly kind: CardKind;
  readonly renderer?: "generic";
}

/** Construct a clean descriptor; reject extras such as html/css/componentName. */
export function parseTrustedDescriptor(
  value: unknown,
): SafeCardDescriptor | undefined {
  if (!isRecord(value)) return undefined;
  const keys = Object.keys(value);
  if (keys.some((key) => !["version", "kind", "renderer"].includes(key)))
    return undefined;
  if (
    typeof value.version !== "number" ||
    !Number.isInteger(value.version) ||
    !isCardKind(value.kind) ||
    (value.renderer !== undefined && value.renderer !== "generic")
  ) {
    return undefined;
  }
  return value.renderer === undefined
    ? { version: value.version, kind: value.kind }
    : { version: value.version, kind: value.kind, renderer: "generic" };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCardKind(value: unknown): value is CardKind {
  return ["video", "image", "article", "discussion", "fallback"].includes(
    String(value),
  );
}
