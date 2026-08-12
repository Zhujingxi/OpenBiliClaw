import type { ProfileResponse, WebApi } from "../services/api";
import type { components } from "@openbiliclaw/api-client";
import { defineStore } from "pinia";
import { ref } from "vue";
import {
  errorMessage,
  isCancellation,
  RequestOwner,
  type LoadPhase,
} from "./state";

export const useProfileStore = defineStore("profile", () => {
  const phase = ref<LoadPhase>("idle");
  const result = ref<ProfileResponse>();
  const error = ref<string>();
  const owner = new RequestOwner();

  async function load(api: WebApi, profileId = "default"): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      const next = await api.profile(profileId, signal);
      if (!owner.owns(signal)) return;
      result.value = next;
      phase.value =
        next.profile.preference_summary.length === 0 &&
        next.profile.insights.length === 0
          ? "empty"
          : "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  async function edit(
    api: WebApi,
    command: components["schemas"]["ProfileEditRequest"],
  ): Promise<void> {
    const signal = owner.next();
    phase.value = "loading";
    error.value = undefined;
    try {
      // Server response is authoritative; reload the bounded projection after mutation.
      await api.editProfile(command, signal);
      const next = await api.profile(command.profile_id, signal);
      if (!owner.owns(signal)) return;
      result.value = next;
      phase.value = "success";
    } catch (caught) {
      if (isCancellation(caught) || !owner.owns(signal)) return;
      error.value = errorMessage(caught);
      phase.value = "error";
    }
  }

  return { phase, result, error, load, edit, cancel: () => owner.cancel() };
});
