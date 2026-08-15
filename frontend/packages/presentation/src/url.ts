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

/** Route a safe HTTPS image through the backend CDN allowlist. */
export function proxyImageUrl(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  try {
    const parsed = new URL(value);
    // Provider CDNs serve the same objects over https; upgrade instead of dropping.
    if (parsed.protocol === "http:") parsed.protocol = "https:";
    return parsed.protocol === "https:"
      ? `/v1/media?url=${encodeURIComponent(parsed.href)}`
      : undefined;
  } catch {
    return undefined;
  }
}
