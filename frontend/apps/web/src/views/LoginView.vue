<script setup lang="ts">
import { inject, ref } from "vue";
import type { WebApi } from "../services/api";
import { useAuthStore } from "../stores/auth";

const api = inject<WebApi>("api");
if (!api) throw new Error("api dependency missing");
const auth = useAuthStore();
const password = ref("");

const submit = async (): Promise<void> => {
  await auth.login(api, password.value);
};
</script>

<template>
  <section aria-labelledby="login-title">
    <h1 id="login-title" tabindex="-1">Sign in</h1>
    <p>Enter the password configured for this local OpenBiliClaw instance.</p>
    <form @submit.prevent="submit">
      <label for="password">Password</label>
      <input
        id="password"
        v-model="password"
        type="password"
        required
        autocomplete="current-password"
        :disabled="auth.loading"
      />
      <button type="submit" :disabled="auth.loading">
        {{ auth.loading ? "Signing in…" : "Sign in" }}
      </button>
      <p v-if="auth.error" role="alert">{{ auth.error }}</p>
    </form>
  </section>
</template>
