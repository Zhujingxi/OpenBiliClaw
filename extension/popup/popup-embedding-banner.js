/**
 * OpenBiliClaw popup — embedding ("semantic dedup") banner helpers.
 *
 * Loaded straight from popup/ as native JS (not via the esbuild bundle),
 * mirroring the other popup-*.js modules. Kept dependency-light so the
 * show/hide decision and the auto-refresh wiring are unit-testable without
 * a real DOM.
 */

/**
 * Whether the "semantic dedup off" banner should be shown for a given
 * /api/health payload.
 *
 * Only nag when the backend *explicitly* reports embedding off
 * (`embedding_ready === false`). A null payload (backend unreachable) or an
 * older backend without the field stays silent — the connection banner
 * already covers "backend down", and a false alarm is worse than no banner.
 *
 * @param {{ embedding_ready?: boolean } | null | undefined} health
 * @returns {boolean}
 */
export function shouldShowEmbeddingBanner(health) {
  return Boolean(health) && health.embedding_ready === false;
}

/**
 * Re-run `recheck` whenever the panel becomes visible again or the window
 * regains focus.
 *
 * The backend probes /api/health live, so once the user fixes embedding
 * (e.g. `ollama pull bge-m3`) and returns to the panel, a stale "off" banner
 * clears itself instead of lingering until the panel is fully reopened —
 * `maybeShowEmbeddingBanner` otherwise only runs once, at panel open.
 *
 * @param {() => unknown} recheck
 * @param {{ doc?: typeof document, win?: typeof window }} [deps]
 * @returns {() => void} teardown that removes the listeners
 */
export function installEmbeddingBannerAutoRefresh(recheck, { doc = document, win = window } = {}) {
  const onMaybeVisible = () => {
    // Skip work while the panel is hidden; "visible"/undefined both count
    // as on-screen so a plain `focus` (no visibilityState change) still fires.
    if (doc.visibilityState !== "hidden") void recheck();
  };
  doc.addEventListener("visibilitychange", onMaybeVisible);
  win.addEventListener("focus", onMaybeVisible);
  return () => {
    doc.removeEventListener("visibilitychange", onMaybeVisible);
    win.removeEventListener("focus", onMaybeVisible);
  };
}
