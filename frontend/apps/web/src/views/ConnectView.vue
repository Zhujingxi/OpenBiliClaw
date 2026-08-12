<script setup lang="ts">
import { ref } from "vue";
const provider = ref("");
</script>
<template>
  <section>
    <h1 tabindex="-1">Connect source</h1>
    <form @submit.prevent>
      <label for="provider-id">Provider</label
      ><input id="provider-id" v-model="provider" autocomplete="off" /><button
        type="submit"
      >
        Review connection
      </button>
      <p>Credentials are write-only and are never displayed here.</p>
    </form>
  </section>
</template>
