/**
 * Toolbar action badge state — pure decision table, kept out of the service
 * worker so it is unit-testable.
 *
 * Two independent signals feed one badge:
 *  - `reachable`: backend HTTP/WS reachability (`null` = not probed yet)
 *  - `uninitialized`: backend reachable but reporting `initialized === false`
 *    (guided init never completed)
 *
 * Unreachable wins over uninitialized: a down backend's init state is
 * unknown, and "start the daemon" is the actionable hint there. A reachable
 * but uninitialized backend must NOT render the same empty badge as a healthy
 * one — that silence is exactly what kept fresh installs from ever noticing
 * the guided-init entry.
 */

export type ActionBadgeView = {
  text: string;
  color?: string;
  title: string;
};

export const BADGE_TITLE_DEFAULT = "OpenBiliClaw";
export const BADGE_TITLE_UNREACHABLE =
  "OpenBiliClaw：后端未启动，请启动 Docker Compose，或同时启动 API 与 worker";
export const BADGE_TITLE_UNINITIALIZED = "OpenBiliClaw：后端还没初始化，点击图标开始引导初始化";
export const BADGE_COLOR_UNREACHABLE = "#9CA3AF";
export const BADGE_COLOR_UNINITIALIZED = "#F97316";

/**
 * /api/events answers HTTP 200 with `accepted=0` and per-event
 * `reason:"not_initialized"` rejections when guided init never ran — the
 * events are consumed and dropped, not retried. Detect that shape so the
 * service worker can surface the uninitialized badge instead of dropping
 * the user's behavior signals in complete silence.
 */
export function flushResponseReportsUninitialized(payload: unknown): boolean {
  if (!payload || typeof payload !== "object") return false;
  const data = payload as { accepted?: unknown; rejected?: unknown };
  if (Number(data.accepted ?? 0) !== 0) return false;
  const rejected = Array.isArray(data.rejected) ? data.rejected : [];
  return rejected.some(
    (item) =>
      Boolean(item) &&
      typeof item === "object" &&
      (item as { reason?: unknown }).reason === "not_initialized",
  );
}

export function computeActionBadge(
  reachable: boolean | null,
  uninitialized: boolean,
): ActionBadgeView {
  if (reachable === false) {
    return { text: "!", color: BADGE_COLOR_UNREACHABLE, title: BADGE_TITLE_UNREACHABLE };
  }
  if (uninitialized) {
    return { text: "!", color: BADGE_COLOR_UNINITIALIZED, title: BADGE_TITLE_UNINITIALIZED };
  }
  return { text: "", title: BADGE_TITLE_DEFAULT };
}
