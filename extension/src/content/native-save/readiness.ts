/** Wait for a login-dependent SPA to expose its exact native-save surface. */
const INITIAL_READY_ATTEMPTS = 40;
const INITIAL_READY_INTERVAL_MS = 250;

export async function waitForNativeSaveReadiness(
  ready: () => boolean | Promise<boolean>,
  sleep: (ms: number) => Promise<void>,
): Promise<boolean> {
  // Real cold-page E2E showed `tab.status=complete` up to 3.3 seconds before
  // the account/content controls existed. A bounded 10-second window absorbs
  // that SPA render gap while staying far below the runner's 240-second cap.
  for (let attempt = 0; attempt < INITIAL_READY_ATTEMPTS; attempt += 1) {
    if (await ready()) return true;
    if (attempt + 1 < INITIAL_READY_ATTEMPTS) await sleep(INITIAL_READY_INTERVAL_MS);
  }
  return false;
}
