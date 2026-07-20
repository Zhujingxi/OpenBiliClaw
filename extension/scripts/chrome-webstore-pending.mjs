// @ts-check

/**
 * Retry an upload once after cancelling a pending in-review submission, when
 * authorized via `replacePending` and the upload failed with the Chrome Web
 * Store NOT_UPDATEABLE reason.
 *
 * @template T
 * @param {{ replacePending: boolean, upload: () => Promise<T>, cancelSubmission: () => Promise<unknown> }} deps
 * @returns {Promise<T>}
 */
export async function uploadWithPendingReplacement({ replacePending, upload, cancelSubmission }) {
  try {
    return await upload();
  } catch (error) {
    if (
      !replacePending ||
      /** @type {{ chromeWebStoreReason?: string }} */ (error)?.chromeWebStoreReason !==
        "NOT_UPDATEABLE"
    ) {
      throw error;
    }
    console.log("Chrome Web Store item is in review; cancelling the pending submission...");
    await cancelSubmission();
    console.log("Pending submission cancelled; retrying the upload once...");
    return await upload();
  }
}
