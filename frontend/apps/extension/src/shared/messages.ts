export type ConnectionState =
  | "disconnected"
  | "checking"
  | "connected"
  | "unavailable";

export type ExtensionMessage =
  | { readonly kind: "connection.get" }
  | {
      readonly kind: "connection.set";
      readonly backendUrl: string;
      readonly deviceToken: string;
    }
  | { readonly kind: "connection.check" }
  | {
      readonly kind: "connection.status";
      readonly state: ConnectionState;
      readonly backendUrl: string;
    };

const messageKeys: Readonly<
  Record<ExtensionMessage["kind"], readonly string[]>
> = {
  "connection.get": ["kind"],
  "connection.set": ["kind", "backendUrl", "deviceToken"],
  "connection.check": ["kind"],
  "connection.status": ["kind", "state", "backendUrl"],
};

export function parseExtensionMessage(
  value: unknown,
): ExtensionMessage | undefined {
  if (
    !isRecord(value) ||
    typeof value.kind !== "string" ||
    !(value.kind in messageKeys)
  )
    return undefined;
  const kind = value.kind as ExtensionMessage["kind"];
  if (!hasExactKeys(value, messageKeys[kind])) return undefined;
  switch (kind) {
    case "connection.get":
      return { kind };
    case "connection.check":
      return { kind };
    case "connection.set":
      return isLoopbackUrl(value.backendUrl) &&
        isBoundedText(value.deviceToken, 1, 512)
        ? { kind, backendUrl: value.backendUrl, deviceToken: value.deviceToken }
        : undefined;
    case "connection.status":
      return isConnectionState(value.state) && isLoopbackUrl(value.backendUrl)
        ? { kind, state: value.state, backendUrl: value.backendUrl }
        : undefined;
  }
}

export function isLoopbackUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 2048) return false;
  try {
    const url = new URL(value);
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
    );
  } catch {
    return false;
  }
}

function hasExactKeys(
  value: Record<string, unknown>,
  allowed: readonly string[],
): boolean {
  return (
    Object.keys(value).every((key) => allowed.includes(key)) &&
    allowed.every((key) => key in value)
  );
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function isBoundedText(
  value: unknown,
  minimum: number,
  maximum: number,
): value is string {
  return (
    typeof value === "string" &&
    value.length >= minimum &&
    value.length <= maximum
  );
}
function isConnectionState(value: unknown): value is ConnectionState {
  return ["disconnected", "checking", "connected", "unavailable"].includes(
    String(value),
  );
}
