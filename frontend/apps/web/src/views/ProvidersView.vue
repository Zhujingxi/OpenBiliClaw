<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted } from "vue";
import AsyncState from "../components/AsyncState.vue";
import type { WebApi } from "../services/api";
import { useSourcesStore } from "../stores/sources";
import { useI18n } from "vue-i18n";

const api = inject<WebApi>("api");
if (api === undefined) throw new Error("WebApi not provided");
const store = useSourcesStore();
const { t, locale } = useI18n();
const connectedCount = computed(
  () => store.items.filter((item) => item.state === "connected").length,
);
const hasConnectedSource = computed(() => connectedCount.value > 0);
const providerInventory = computed(
  () => new Map(store.inventory.by_provider.map((item) => [item.key, item])),
);
const contentKinds = computed(() =>
  [...store.inventory.by_content_kind].sort(
    (left, right) =>
      right.pool_count - left.pool_count || left.key.localeCompare(right.key),
  ),
);
const queuedProviders = computed(() =>
  [...store.inventory.by_provider]
    .filter((item) => item.queue_count > 0)
    .sort(
      (left, right) =>
        right.queue_count - left.queue_count ||
        left.key.localeCompare(right.key),
    ),
);
const videoQueueCount = computed(
  () =>
    store.inventory.by_content_kind.find((item) => item.key === "video")
      ?.queue_count ?? 0,
);

function providerInitial(id: string): string {
  return id.slice(0, 1).toUpperCase();
}
function providerStats(id: string) {
  return providerInventory.value.get(id);
}
function label(value: string): string {
  return value
    .replace(/^builtin\./, "")
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
function formatCount(value: number): string {
  return new Intl.NumberFormat(locale.value).format(value);
}
function displayCount(value: number): string {
  return store.phase === "success" || store.phase === "empty"
    ? formatCount(value)
    : "—";
}
function poolShare(value: number): string {
  return store.inventory.pool_count === 0
    ? "0%"
    : `${Math.round((value / store.inventory.pool_count) * 100)}%`;
}
function verifiedAt(value: string | null | undefined): string {
  if (!value) return t("providers.notVerified");
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? t("providers.verificationUnavailable")
    : t("providers.verified", {
        date: new Intl.DateTimeFormat(locale.value, {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(date),
      });
}

onMounted(() => store.load(api));
onBeforeUnmount(store.cancel);
</script>

<template>
  <section class="sources-page">
    <div class="page-heading">
      <div class="page-heading-copy">
        <p class="eyebrow">{{ t("providers.eyebrow") }}</p>
        <h1 tabindex="-1">{{ t("providers.title") }}</h1>
        <p>{{ t("providers.intro") }}</p>
      </div>
      <a class="connect-action" href="#/connect">{{ t("providers.add") }}</a>
    </div>

    <dl
      class="source-metrics surface-card"
      :aria-label="t('providers.overview')"
    >
      <div>
        <dt>{{ t("providers.connected") }}</dt>
        <dd>{{ displayCount(connectedCount) }}</dd>
        <small>{{
          t("providers.available", { count: displayCount(store.items.length) })
        }}</small>
      </div>
      <div>
        <dt>{{ t("providers.feedQueue") }}</dt>
        <dd>{{ displayCount(store.inventory.queue_count) }}</dd>
        <small>{{ t("providers.feedHelp") }}</small>
      </div>
      <div>
        <dt>{{ t("providers.videos") }}</dt>
        <dd>{{ displayCount(videoQueueCount) }}</dd>
        <small>{{ t("providers.videosHelp") }}</small>
      </div>
      <div>
        <dt>{{ t("providers.pool") }}</dt>
        <dd>{{ displayCount(store.inventory.pool_count) }}</dd>
        <small>{{ t("providers.poolHelp") }}</small>
      </div>
    </dl>

    <AsyncState :phase="store.phase" :error="store.error">
      <template #empty>
        {{ t("providers.emptyProviders") }}
        <a href="#/connect">{{ t("providers.review") }}</a
        >.
      </template>

      <p v-if="!hasConnectedSource" class="empty-guidance" role="status">
        {{ t("providers.noConnected") }}
        <a href="#/connect">{{ t("providers.connect") }}</a>
        {{ t("providers.noConnectedTail") }}
      </p>

      <div class="inventory-grid" :aria-label="t('providers.inventory')">
        <section class="inventory-card surface-card">
          <div class="inventory-heading">
            <div>
              <p class="eyebrow">{{ t("providers.resourcePool") }}</p>
              <h2>{{ t("providers.discovered") }}</h2>
            </div>
            <span class="inventory-total">{{
              t("providers.active", {
                count: formatCount(store.inventory.pool_count),
              })
            }}</span>
          </div>
          <p class="inventory-description">
            {{ t("providers.poolDescription") }}
          </p>
          <ul v-if="contentKinds.length" class="kind-list">
            <li v-for="item in contentKinds" :key="item.key">
              <div class="kind-row-heading">
                <strong>{{ label(item.key) }}</strong>
                <span>
                  {{
                    t("providers.poolRow", {
                      pool: formatCount(item.pool_count),
                      queue: formatCount(item.queue_count),
                    })
                  }}
                </span>
              </div>
              <div
                class="kind-track"
                role="img"
                :aria-label="
                  t('providers.candidatesAria', {
                    label: label(item.key),
                    count: formatCount(item.pool_count),
                    total: formatCount(store.inventory.pool_count),
                  })
                "
              >
                <span :style="{ width: poolShare(item.pool_count) }"></span>
              </div>
            </li>
          </ul>
          <p v-else class="inventory-empty">
            {{ t("providers.poolEmpty") }}
          </p>
        </section>

        <section class="inventory-card queue-card surface-card">
          <div class="inventory-heading">
            <div>
              <p class="eyebrow">{{ t("providers.feedQueue") }}</p>
              <h2>{{ t("providers.ready") }}</h2>
            </div>
            <span class="inventory-total queue-total">{{
              t("providers.items", {
                count: formatCount(store.inventory.queue_count),
              })
            }}</span>
          </div>
          <p class="inventory-description">
            {{ t("providers.queueDescription") }}
          </p>
          <div class="queue-highlight">
            <span aria-hidden="true">▶</span>
            <div>
              <strong>{{ formatCount(videoQueueCount) }}</strong>
              <small>{{ t("providers.videosInQueue") }}</small>
            </div>
          </div>
          <ul v-if="queuedProviders.length" class="queue-provider-list">
            <li v-for="item in queuedProviders" :key="item.key">
              <span>{{ item.key }}</span>
              <strong>{{ formatCount(item.queue_count) }}</strong>
            </li>
          </ul>
          <p v-else class="inventory-empty">
            {{ t("providers.queueEmpty") }}
          </p>
          <p class="archive-note">
            {{
              t("providers.archive", {
                count: formatCount(store.inventory.archived_count),
              })
            }}
          </p>
        </section>
      </div>

      <div class="provider-section-heading">
        <div>
          <p class="eyebrow">{{ t("providers.sourceHealth") }}</p>
          <h2>{{ t("providers.providersAccess") }}</h2>
        </div>
        <p>{{ t("providers.healthIntro") }}</p>
      </div>

      <ul class="provider-grid" :aria-label="t('providers.statuses')">
        <li
          v-for="item in store.items"
          :key="`${item.provider_id}:${item.account_id ?? 'default'}`"
          class="provider-card"
          :data-provider="item.provider_id"
        >
          <div class="provider-card-head">
            <span class="provider-logo" aria-hidden="true">
              {{ providerInitial(item.provider_id) }}
            </span>
            <div>
              <div class="provider-status">
                <strong>{{ item.provider_id }}</strong>
                <span class="status-badge" :class="`status-${item.state}`">
                  {{ t(`common.states.${item.state}`, item.state) }}
                </span>
              </div>
              <small>
                {{
                  item.verification?.safe_account_identity ||
                  item.account_id ||
                  t("providers.defaultAccount")
                }}
              </small>
            </div>
            <span
              class="status-dot"
              :class="`status-${item.state}`"
              aria-hidden="true"
            ></span>
          </div>

          <dl class="provider-facts">
            <div>
              <dt>{{ t("providers.inPool") }}</dt>
              <dd>
                {{
                  formatCount(providerStats(item.provider_id)?.pool_count ?? 0)
                }}
              </dd>
            </div>
            <div>
              <dt>{{ t("providers.inQueue") }}</dt>
              <dd>
                {{
                  formatCount(providerStats(item.provider_id)?.queue_count ?? 0)
                }}
              </dd>
            </div>
            <div>
              <dt>{{ t("providers.verification") }}</dt>
              <dd>{{ label(item.verification?.strength ?? "none") }}</dd>
            </div>
          </dl>

          <div class="capability-block">
            <div class="card-label-row">
              <span>{{ t("providers.capabilities") }}</span>
              <small>{{ item.capabilities?.length ?? 0 }}</small>
            </div>
            <div class="capability-list" :aria-label="t('providers.supported')">
              <span
                v-for="capability in item.capabilities ?? []"
                :key="capability"
              >
                {{ label(capability) }}
              </span>
              <span v-if="!item.capabilities?.length">{{
                t("providers.capabilitiesUnavailable")
              }}</span>
            </div>
          </div>

          <div class="verification-line">
            <span
              :class="{ 'is-verified': item.verification }"
              aria-hidden="true"
              >{{ item.verification ? "✓" : "○" }}</span
            >
            <span>{{ verifiedAt(item.verification?.verified_at) }}</span>
          </div>

          <div class="provider-card-footer">
            <span>{{
              item.method_id ? label(item.method_id) : t("providers.noMethod")
            }}</span>
            <a :href="`#/connect/${encodeURIComponent(item.provider_id)}`">
              {{
                t(
                  item.state === "connected"
                    ? "providers.manage"
                    : "providers.configure",
                )
              }}
            </a>
          </div>
        </li>
      </ul>
    </AsyncState>
  </section>
</template>

<style scoped>
.connect-action {
  display: inline-flex;
  align-items: center;
  min-height: 2.6rem;
  border-radius: var(--radius-sm);
  padding: 0.6rem 1rem;
  background: var(--brand);
  color: #fff;
  font-size: 0.8rem;
  font-weight: 720;
  text-decoration: none;
  box-shadow: 0 7px 18px rgb(37 99 166 / 0.18);
}
.connect-action:hover {
  background: var(--brand-strong);
}
.source-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  padding-block: 1rem;
  background:
    radial-gradient(circle at 100% 0, rgb(37 99 166 / 0.08), transparent 32%),
    var(--card);
}
.source-metrics > div {
  display: grid;
  align-content: center;
  min-width: 0;
  min-height: 6rem;
  border-left: 1px solid var(--border);
  padding: 0.15rem clamp(0.8rem, 2vw, 1.35rem);
}
.source-metrics > div:first-child {
  border-left: 0;
}
.source-metrics dt {
  color: var(--muted-foreground);
  font-size: 0.68rem;
  font-weight: 760;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.source-metrics dd {
  margin: 0.2rem 0 0.25rem;
  border: 0;
  padding: 0;
  color: var(--foreground);
  font-size: clamp(1.8rem, 4vw, 2.5rem);
  font-weight: 780;
  letter-spacing: -0.06em;
  line-height: 1;
}
.source-metrics small {
  color: var(--muted-foreground);
  font-size: 0.68rem;
  line-height: 1.35;
}
.empty-guidance {
  margin: 0;
  border: 1px dashed color-mix(in srgb, var(--warning) 45%, var(--border));
  border-radius: var(--radius);
  padding: 0.85rem 1rem;
  background: color-mix(in srgb, var(--warning) 5%, var(--card));
}
.inventory-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(18rem, 0.8fr);
  gap: 0.9rem;
}
.inventory-card {
  display: grid;
  align-content: start;
  gap: 1rem;
  overflow: hidden;
}
.inventory-heading,
.provider-section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.inventory-heading h2,
.provider-section-heading h2 {
  margin: 0;
  font-size: 1.05rem;
  letter-spacing: -0.02em;
}
.inventory-total {
  flex: 0 0 auto;
  border: 1px solid color-mix(in srgb, var(--brand) 25%, var(--border));
  border-radius: 999px;
  padding: 0.32rem 0.62rem;
  background: var(--brand-soft);
  color: var(--brand-strong);
  font-size: 0.68rem;
  font-weight: 750;
}
.queue-total {
  border-color: color-mix(in srgb, var(--success) 25%, var(--border));
  background: color-mix(in srgb, var(--success) 8%, var(--card));
  color: var(--success);
}
.inventory-description {
  margin: -0.45rem 0 0;
  color: var(--muted-foreground);
  font-size: 0.78rem;
  line-height: 1.5;
}
.kind-list,
.queue-provider-list {
  display: grid;
  gap: 0.85rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.kind-row-heading,
.queue-provider-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.kind-row-heading strong {
  font-size: 0.78rem;
}
.kind-row-heading span {
  color: var(--muted-foreground);
  font-size: 0.68rem;
  text-align: right;
}
.kind-track {
  height: 0.42rem;
  margin-top: 0.38rem;
  overflow: hidden;
  border-radius: 999px;
  background: var(--muted);
}
.kind-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--brand), #64a3df);
}
.queue-card {
  background:
    linear-gradient(150deg, rgb(35 122 82 / 0.04), transparent 55%), var(--card);
}
.queue-highlight {
  display: flex;
  align-items: center;
  gap: 0.8rem;
  border: 1px solid color-mix(in srgb, var(--success) 18%, var(--border));
  border-radius: var(--radius);
  padding: 0.8rem;
  background: color-mix(in srgb, var(--success) 6%, var(--card));
}
.queue-highlight > span {
  display: grid;
  place-items: center;
  width: 2rem;
  height: 2rem;
  border-radius: 50%;
  background: var(--success);
  color: #fff;
  font-size: 0.66rem;
}
.queue-highlight div {
  display: grid;
}
.queue-highlight strong {
  font-size: 1.35rem;
  line-height: 1;
}
.queue-highlight small {
  margin-top: 0.2rem;
  color: var(--muted-foreground);
  font-size: 0.68rem;
}
.queue-provider-list {
  gap: 0;
}
.queue-provider-list li {
  border-top: 1px solid var(--border);
  padding: 0.52rem 0;
  font-size: 0.75rem;
}
.queue-provider-list span {
  text-transform: capitalize;
}
.queue-provider-list strong {
  font-variant-numeric: tabular-nums;
}
.inventory-empty,
.archive-note {
  margin: 0;
  color: var(--muted-foreground);
  font-size: 0.73rem;
  line-height: 1.5;
}
.inventory-empty {
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  padding: 0.8rem;
  background: color-mix(in srgb, var(--muted) 55%, transparent);
}
.archive-note {
  margin-top: auto;
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
}
.provider-section-heading {
  align-items: flex-end;
  margin-top: 0.25rem;
}
.provider-section-heading > p {
  max-width: 24rem;
  margin: 0;
  color: var(--muted-foreground);
  font-size: 0.75rem;
  text-align: right;
}
.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
  gap: 0.9rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.provider-card {
  display: grid;
  align-content: start;
  gap: 1rem;
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.05rem;
  background: var(--card);
  box-shadow: var(--shadow-sm);
  transition:
    border-color 120ms ease,
    box-shadow 120ms ease,
    transform 120ms ease;
}
.provider-card:hover {
  transform: translateY(-2px);
  border-color: #beb9ad;
  box-shadow: 0 12px 26px rgb(31 30 27 / 0.08);
}
.provider-card-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 0.75rem;
  align-items: center;
}
.provider-logo {
  display: grid;
  place-items: center;
  width: 2.65rem;
  height: 2.65rem;
  border-radius: 0.78rem;
  background: linear-gradient(145deg, var(--brand), var(--brand-strong));
  color: #fff;
  font-size: 0.86rem;
  font-weight: 820;
  box-shadow: 0 6px 14px rgb(37 99 166 / 0.16);
}
.provider-card[data-provider="youtube"] .provider-logo {
  background: #fff0f0;
  color: #c62828;
  box-shadow: none;
}
.provider-card[data-provider="bilibili"] .provider-logo {
  background: #e8f6fb;
  color: #087ea4;
  box-shadow: none;
}
.provider-card[data-provider="douyin"] .provider-logo {
  background: #242226;
  color: #fff;
}
.provider-card[data-provider="rednote"] .provider-logo {
  background: #fff0ef;
  color: #d62f35;
  box-shadow: none;
}
.provider-card-head > div {
  min-width: 0;
}
.provider-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
}
.provider-status strong {
  overflow: hidden;
  text-overflow: ellipsis;
  text-transform: capitalize;
  white-space: nowrap;
}
.provider-status .status-badge {
  flex: 0 0 auto;
}
.provider-card-head small {
  display: block;
  margin-top: 0.18rem;
  overflow: hidden;
  color: var(--muted-foreground);
  font-size: 0.68rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status-dot {
  width: 0.52rem;
  height: 0.52rem;
  border-radius: 50%;
  background: #aaa59a;
}
.status-dot.status-connected {
  background: var(--success);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 12%, transparent);
}
.status-dot.status-disconnected {
  background: var(--warning);
}
.status-dot.status-error {
  background: var(--error);
}
.provider-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: color-mix(in srgb, var(--muted) 42%, var(--card));
}
.provider-facts > div {
  display: grid;
  gap: 0.2rem;
  min-width: 0;
  border-left: 1px solid var(--border);
  padding: 0.62rem;
}
.provider-facts > div:first-child {
  border-left: 0;
}
.provider-facts dt,
.provider-facts dd {
  border: 0;
  padding: 0;
}
.provider-facts dt {
  color: var(--muted-foreground);
  font-size: 0.6rem;
  font-weight: 720;
  text-transform: uppercase;
}
.provider-facts dd {
  overflow: hidden;
  font-size: 0.78rem;
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.capability-block {
  display: grid;
  gap: 0.5rem;
}
.card-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--muted-foreground);
  font-size: 0.62rem;
  font-weight: 760;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.card-label-row small {
  display: grid;
  place-items: center;
  min-width: 1.35rem;
  min-height: 1.35rem;
  border-radius: 999px;
  background: var(--muted);
  font-size: 0.6rem;
}
.capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  min-height: 1.6rem;
}
.capability-list span {
  border: 1px solid color-mix(in srgb, var(--brand) 12%, var(--border));
  border-radius: 999px;
  padding: 0.26rem 0.52rem;
  background: color-mix(in srgb, var(--brand-soft) 55%, var(--card));
  color: var(--brand-strong);
  font-size: 0.64rem;
  font-weight: 680;
}
.verification-line {
  display: flex;
  align-items: center;
  gap: 0.42rem;
  min-width: 0;
  color: var(--muted-foreground);
  font-size: 0.68rem;
}
.verification-line > span:first-child {
  display: grid;
  place-items: center;
  width: 1.15rem;
  height: 1.15rem;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--muted);
  color: var(--muted-foreground);
  font-size: 0.62rem;
  font-weight: 800;
}
.verification-line > span.is-verified {
  background: color-mix(in srgb, var(--success) 10%, var(--card));
  color: var(--success);
}
.provider-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  margin-top: auto;
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
  font-size: 0.72rem;
}
.provider-card-footer span {
  min-width: 0;
  overflow: hidden;
  color: var(--muted-foreground);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.provider-card-footer a {
  flex: 0 0 auto;
  font-weight: 740;
  text-decoration: none;
}
@media (max-width: 64rem) {
  .source-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .source-metrics > div:nth-child(3) {
    border-left: 0;
  }
  .source-metrics > div:nth-child(n + 3) {
    border-top: 1px solid var(--border);
  }
  .inventory-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 36rem) {
  .source-metrics {
    grid-template-columns: minmax(0, 1fr);
  }
  .source-metrics > div {
    min-height: 5.1rem;
    border-top: 1px solid var(--border);
    border-left: 0;
  }
  .source-metrics > div:first-child {
    border-top: 0;
  }
  .source-metrics > div:nth-child(2) {
    border-top: 1px solid var(--border);
  }
  .inventory-heading,
  .provider-section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
  .provider-section-heading > p {
    text-align: left;
  }
  .provider-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .provider-facts > div:nth-child(3) {
    grid-column: 1 / -1;
    border-top: 1px solid var(--border);
    border-left: 0;
  }
}
</style>
