export function normalizeSettingsPatch(patch) {
  const network = patch?.network;
  if (network && network.mode !== "custom") network.proxy_url = "";
  return patch;
}
