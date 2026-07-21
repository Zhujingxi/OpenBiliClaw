const SECRET_QUERY_NAMES = new Set([
  "accesskey",
  "accesstoken",
  "auth",
  "apikey",
  "authorization",
  "cookie",
  "password",
  "refreshtoken",
  "session",
  "sessionid",
  "signature",
  "token",
  "xsecsource",
  "xsectoken",
]);
const SECRET_FIELD_SUFFIXES = [
  "apikey",
  "apikeys",
  "authorization",
  "cookie",
  "cookies",
  "credential",
  "credentials",
  "password",
  "passwords",
  "secret",
  "secrets",
  "session",
  "sessionid",
  "sessions",
  "signature",
  "signatures",
  "token",
  "tokens",
] as const;

/** Remove credential-bearing query parameters while preserving ordinary navigation state. */
export function sanitizeOutboundUrl(value: string): string {
  const candidate = value.trim();
  if (!/^https?:\/\//i.test(candidate)) return value;
  try {
    const url = new URL(candidate);
    url.username = "";
    url.password = "";
    const names: string[] = [];
    url.searchParams.forEach((_value, name) => names.push(name));
    for (const name of names) {
      const normalized = name.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (isSecretFieldName(normalized)) {
        url.searchParams.delete(name);
      }
    }
    const fragment = url.hash.slice(1);
    if (fragment.includes("=")) {
      const fragmentParams = new URLSearchParams(fragment.startsWith("?") ? fragment.slice(1) : fragment);
      const fragmentNames: string[] = [];
      fragmentParams.forEach((_value, name) => fragmentNames.push(name));
      for (const name of fragmentNames) {
        if (isSecretFieldName(name)) fragmentParams.delete(name);
      }
      url.hash = fragmentParams.toString();
    }
    return url.href;
  } catch {
    return "";
  }
}

export function isSecretFieldName(value: string): boolean {
  const normalized = value.toLowerCase().replace(/[^a-z0-9]/g, "");
  return SECRET_QUERY_NAMES.has(normalized)
    || SECRET_FIELD_SUFFIXES.some((suffix) => normalized.endsWith(suffix));
}
