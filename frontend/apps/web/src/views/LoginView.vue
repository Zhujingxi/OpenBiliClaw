<script setup lang="ts">
import { inject, ref } from "vue";
import type { WebApi } from "../services/api";
import { useAuthStore } from "../stores/auth";
import { useI18n } from "vue-i18n";
import LocalizedError from "../components/LocalizedError.vue";

const api = inject<WebApi>("api");
if (!api) throw new Error("api dependency missing");
const { t } = useI18n();
const auth = useAuthStore();
const password = ref("");

const submit = async (): Promise<void> => {
  await auth.login(api, password.value);
};
</script>

<template>
  <section aria-labelledby="login-title">
    <h1 id="login-title" tabindex="-1">{{ t("login.title") }}</h1>
    <p>{{ t("login.intro") }}</p>
    <form @submit.prevent="submit">
      <label for="password">{{ t("login.password") }}</label>
      <input
        id="password"
        v-model="password"
        type="password"
        required
        autocomplete="current-password"
        :disabled="auth.loading"
        aria-describedby="password-help"
      />
      <p id="password-help" class="field-hint">{{ t("login.passwordHelp") }}</p>
      <button type="submit" :disabled="auth.loading">
        {{ auth.loading ? t("login.signingIn") : t("login.submit") }}
      </button>
      <p v-if="auth.error" role="alert">
        <LocalizedError :error="auth.error" />
      </p>
    </form>
  </section>
</template>
