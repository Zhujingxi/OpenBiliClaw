import type { ProfileResponse, WebApi } from "../services/api";
import type { components } from "@openbiliclaw/api-client";
import { defineStore } from "pinia";
import { ref } from "vue";
import { errorMessage, RequestOwner, type LoadPhase } from "./state";

export const useProfileStore = defineStore("profile", () => {
  const phase = ref<LoadPhase>("idle");
  const result = ref<ProfileResponse>();
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(api: WebApi, profileId = "default"): Promise<void> {
    phase.value = "loading";
    error.value = undefined;
    try {
      result.value = await api.profile(profileId, owner.next());
      phase.value =
        result.value.profile.preference_summary.length === 0 &&
        result.value.profile.insights.length === 0
          ? "empty"
          : "success";
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError")
        return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function edit(
    api: WebApi,
    command: components["schemas"]["ProfileEditRequest"],
  ): Promise<void> {
    phase.value = "loading";
    error.value = undefined;
    try {
      // Server response is authoritative; reload the bounded projection after mutation.
      await api.editProfile(command, owner.next());
      result.value = await api.profile(command.profile_id, owner.next());
      phase.value = "success";
    } catch (caught) {
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return { phase, result, error, load, edit, cancel: () => owner.cancel() };
});
