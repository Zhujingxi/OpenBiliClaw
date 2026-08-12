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
export type RouteName = (typeof routes)[number][0];

export function routeFromHash(hash: string): RouteName {
  const candidate = hash.replace(/^#\/?/, "").split("/")[0];
  return routes.some(([name]) => name === candidate)
    ? (candidate as RouteName)
    : "recommendations";
}
