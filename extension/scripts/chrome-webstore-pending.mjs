export async function uploadWithPendingReplacement({
  replacePending,
  upload,
  cancelSubmission,
}) {
  try {
    return await upload();
  } catch (error) {
    if (!replacePending || error?.chromeWebStoreReason !== "NOT_UPDATEABLE") {
      throw error;
    }
    console.log("Chrome Web Store item is in review; cancelling the pending submission...");
    await cancelSubmission();
    console.log("Pending submission cancelled; retrying the upload once...");
    return await upload();
  }
}
