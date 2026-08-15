export const routes = [
  ["recommendations", "Recommendations"],
  ["providers", "Providers"],
  ["search", "Search"],
  ["content", "Content detail"],
  ["profile", "Profile"],
  ["assistant", "Assistant"],
  ["connect", "Connect source"],
  ["settings", "Settings"],
  ["runtime", "Runtime health"],
] as const;
export type RouteName = (typeof routes)[number][0] | "login";

export function routeFromHash(hash: string): RouteName {
  const candidate = hash.replace(/^#\/?/, "").split("/")[0];
  if (candidate === "login") return "login";
  return routes.some(([name]) => name === candidate)
    ? (candidate as RouteName)
    : "recommendations";
}

export function routeParameter(hash: string): string | undefined {
  const [, value] = hash.replace(/^#\/?/, "").split("/", 2);
  if (value === undefined || value === "") return undefined;
  try {
    return decodeURIComponent(value);
  } catch {
    return undefined;
  }
}
