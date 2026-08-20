<script setup lang="ts">
import AsyncState from "../components/AsyncState.vue";
import { inject, onBeforeUnmount, onMounted } from "vue";
import { useRuntimeStore } from "../stores/runtime";
import type { WebApi } from "../services/api";
import { useI18n } from "vue-i18n";
const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const { t, locale } = useI18n();
const store = useRuntimeStore();
onMounted(() => {
  void store.load(api);
  void store.connect(api);
});
onBeforeUnmount(() => {
  store.cancel();
  store.disconnect();
});
</script>
<template>
  <section>
    <h1 tabindex="-1">{{ t("runtime.title") }}</h1>
    <div class="runtime-actions">
      <p role="status" aria-live="polite">
        {{
          t(
            store.streamConnected
              ? "runtime.eventsConnected"
              : "runtime.eventsDisconnected",
          )
        }}
      </p>
      <p v-if="store.error && store.phase !== 'error'" role="alert">
        {{ t("runtime.events") }}: {{ store.error }}
      </p>
      <button
        type="button"
        :disabled="store.phase === 'loading'"
        @click="store.load(api)"
      >
        {{ t("runtime.refresh") }}
      </button>
    </div>
    <AsyncState :phase="store.phase" :error="store.error">
      <section v-if="store.health" aria-labelledby="health-summary-heading">
        <h2 id="health-summary-heading">{{ t("runtime.summary") }}</h2>
        <p
          class="health-badge"
          :class="`health-${store.health.health.status}`"
          role="status"
        >
          {{
            t(
              `common.states.${store.health.health.status}`,
              store.health.health.status,
            )
          }}
        </p>
        <dl>
          <dt>{{ t("runtime.component") }}</dt>
          <dd>{{ store.health.health.component_id }}</dd>
          <dt>{{ t("runtime.checked") }}</dt>
          <dd>
            <time :datetime="store.health.health.checked_at">
              {{
                new Date(store.health.health.checked_at).toLocaleString(locale)
              }}
            </time>
          </dd>
          <template v-if="store.health.health.issue">
            <dt>{{ t("runtime.issue") }}</dt>
            <dd>{{ store.health.health.issue }}</dd>
          </template>
        </dl>
        <section aria-labelledby="runtime-jobs-heading">
          <h3 id="runtime-jobs-heading">{{ t("runtime.jobs") }}</h3>
          <p v-if="store.health.health.jobs.length === 0" role="status">
            {{ t("runtime.noJobs") }}
          </p>
          <div v-else class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th scope="col">{{ t("runtime.job") }}</th>
                  <th scope="col">{{ t("runtime.lastResult") }}</th>
                  <th scope="col">{{ t("runtime.runs") }}</th>
                  <th scope="col">{{ t("runtime.active") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="job in store.health.health.jobs" :key="job.job_id">
                  <th scope="row">{{ job.job_id }}</th>
                  <td>{{ job.last_result ?? t("runtime.notRun") }}</td>
                  <td>{{ job.runs_completed }} / {{ job.runs_started }}</td>
                  <td>{{ job.active_runs }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </section>
    </AsyncState>
    <details>
      <summary>
        {{ t("runtime.recentEvents", { count: store.events.length }) }}
      </summary>
      <p v-if="store.events.length === 0" role="status">
        {{ t("runtime.noEvents") }}
      </p>
      <ol v-else>
        <li v-for="event in store.events" :key="event.event_id">
          #{{ event.event_id }} {{ event.kind }} — {{ event.status }}
        </li>
      </ol>
    </details>
  </section>
</template>
<style scoped>
.runtime-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1rem;
}
.runtime-actions button {
  width: auto;
}
.health-badge {
  justify-self: start;
  margin: 0;
  padding: 0.35rem 0.75rem;
  border-radius: 999px;
  font-weight: 700;
  text-transform: capitalize;
}
.health-healthy {
  color: var(--success);
  border: 1px solid var(--success);
  background: color-mix(in srgb, var(--success) 14%, transparent);
}
.health-degraded {
  color: var(--warning);
  border: 1px solid var(--warning);
  background: color-mix(in srgb, var(--warning) 14%, transparent);
}
.health-unhealthy {
  color: var(--error);
  border: 1px solid var(--error);
  background: color-mix(in srgb, var(--error) 14%, transparent);
}
dl {
  display: grid;
  grid-template-columns: minmax(8rem, auto) minmax(0, 1fr);
  margin: 0;
}
dt {
  padding: 0.5rem 1rem 0.5rem 0;
  font-weight: 700;
  color: var(--text-main);
}
dd {
  margin: 0;
  padding: 0.5rem 0;
  color: var(--text-secondary);
}
dt:not(:first-of-type),
dd:not(:first-of-type) {
  border-top: 1px solid var(--line);
}
.table-scroll {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 0.5rem;
  border-bottom: 1px solid var(--line);
  text-align: start;
}
</style>
