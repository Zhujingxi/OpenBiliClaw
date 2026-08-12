const ALLOWED_PROTOCOLS = new Set(["http:", "https:"]);

/** Return a safe absolute URL, or undefined for executable/unsupported protocols. */
export function sanitizeUrl(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  try {
    const parsed = new URL(value);
    return ALLOWED_PROTOCOLS.has(parsed.protocol) ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}
