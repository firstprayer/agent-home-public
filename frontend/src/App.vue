<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "./api.js";
import IdentityTable from "./components/IdentityTable.vue";
import IdentityDialog from "./components/IdentityDialog.vue";
import SleeveDialog from "./components/SleeveDialog.vue";
import ExternalIdentities from "./components/ExternalIdentities.vue";
import ExternalIdentityDialog from "./components/ExternalIdentityDialog.vue";
import Settings from "./components/Settings.vue";
import BrandMark from "./components/BrandMark.vue";
import GettingStarted from "./components/GettingStarted.vue";
import { pages } from "./navigation.js";

const currentPage = () => pages[location.hash] || "agents";
const page = ref(currentPage());
const syncPage = () => {
  page.value = currentPage();
};
const state = ref(null);
const error = ref("");
const dialog = ref(null);
const search = ref("");
const filter = ref("all");
const working = computed(
  () =>
    state.value?.identities.filter(
      (identity) => identity.runtime.status === "active",
    ).length || 0,
);
const withoutSleeve = computed(
  () =>
    state.value?.identities.filter((identity) => !identity.sleeve).length || 0,
);
const listening = computed(
  () =>
    state.value?.identities.filter((identity) => identity.polling).length || 0,
);
const visibleIdentities = computed(() =>
  (state.value?.identities || []).filter((identity) => {
    const query = search.value.trim().toLowerCase();
    const match = [
      identity.name,
      identity.telegram?.username,
      identity.sleeve?.cwd,
    ].some((value) => value?.toLowerCase().includes(query));
    return (
      match &&
      (filter.value === "all" ||
        (filter.value === "working" && identity.runtime.status === "active") ||
        (filter.value === "unassigned" && !identity.sleeve))
    );
  }),
);
let timer, request;

async function refresh() {
  request?.abort();
  const controller = new AbortController();
  request = controller;
  try {
    const result = await api("/api/state", undefined, controller.signal);
    if (controller.signal.aborted) return;
    state.value = result;
    error.value = "";
  } catch (failure) {
    if (!controller.signal.aborted) error.value = failure.message;
  }
}

function saved() {
  dialog.value = null;
  refresh();
}

function navigate(destination) {
  location.hash = destination;
  page.value = pages[`#${destination}`] || "agents";
}
function communication(identity) {
  dialog.value = { kind: "identity", identity, connectTelegram: true };
}

onMounted(() => {
  window.addEventListener("hashchange", syncPage);
  refresh();
  timer = setInterval(() => {
    if (!dialog.value && !document.hidden) refresh();
  }, 4000);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  request?.abort();
  window.removeEventListener("hashchange", syncPage);
});
</script>

<template>
  <a class="skip-link" href="#main">Skip to content</a>
  <header class="page-header">
    <a class="brand" href="#agents" @click="page = 'agents'"
      ><BrandMark /><span
        >Agent Home<span class="brand-subtitle"
          >YOUR CODEX. WITHIN REACH.</span
        ></span
      ></a
    >
    <nav class="top-nav" aria-label="Main navigation">
      <a
        href="#agents"
        :aria-current="page === 'agents' ? 'page' : undefined"
        @click="page = 'agents'"
        >Agents</a
      ><a
        href="#external-identities"
        :aria-current="page === 'external' ? 'page' : undefined"
        @click="page = 'external'"
        >External Identities</a
      ><a
        href="#settings"
        :aria-current="page === 'settings' ? 'page' : undefined"
        @click="page = 'settings'"
        >Settings</a
      ><a
        href="#guide"
        :aria-current="page === 'guide' ? 'page' : undefined"
        @click="page = 'guide'"
        >Get started <span aria-hidden="true">↗</span></a
      >
    </nav>
    <span class="service-status" :class="{ offline: error }" role="status"
      ><span class="connection-dot" aria-hidden="true"></span
      >{{
        error
          ? "Service disconnected"
          : state
            ? "Desktop connected"
            : "Connecting…"
      }}</span
    >
  </header>
  <main id="main">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="page === 'agents'">
      <div class="workspace-intro">
        <div>
          <span class="eyebrow">HOME BASE</span>
          <h1>Talk to your Codex agent,<br /><em>anywhere.</em></h1>
          <p>Your computer does the work. Your conversation goes with you.</p>
        </div>
        <div class="intro-note">
          <span class="orbit-symbol" aria-hidden="true">↗</span
          ><span>A familiar chat.<br />A powerful workspace.</span>
        </div>
      </div>
      <div
        class="workspace-metrics"
        v-if="state?.identities.length"
        aria-label="Workspace overview"
      >
        <div>
          <span>Agent identities</span
          ><strong>{{
            state.identities.length.toString().padStart(2, "0")
          }}</strong
          ><small>One home for every project</small>
        </div>
        <div>
          <span>Listening on Telegram</span
          ><strong
            >{{ listening.toString().padStart(2, "0")
            }}<span class="metric-dot" aria-hidden="true"></span></strong
          ><small>Connected bot listeners</small>
        </div>
        <div>
          <span>Working now</span
          ><strong>{{ working.toString().padStart(2, "0") }}</strong
          ><small
            >{{ withoutSleeve }}
            {{ withoutSleeve === 1 ? "identity awaits" : "identities await" }} a
            sleeve</small
          >
        </div>
      </div>
      <section class="agents" aria-labelledby="agents-title">
        <div class="section-header">
          <div>
            <h2 id="agents-title">
              Your agents<span class="section-count" v-if="state">{{
                state.identities.length
              }}</span>
            </h2>
            <p>Identity stays. Sleeves can change.</p>
          </div>
          <button @click="dialog = { kind: 'identity', identity: null }">
            + New identity
          </button>
        </div>
        <div v-if="state?.identities.length" class="table-toolbar">
          <div class="table-filters" aria-label="Filter agents">
            <button
              v-for="item in [
                { id: 'all', label: 'All agents' },
                { id: 'working', label: 'Working' },
                { id: 'unassigned', label: 'Without sleeve' },
              ]"
              :key="item.id"
              class="filter-button"
              :aria-pressed="filter === item.id"
              @click="filter = item.id"
            >
              {{ item.label }}
            </button>
          </div>
          <label class="agent-search"
            ><span class="sr-only">Search agents</span
            ><span aria-hidden="true">⌕</span
            ><input v-model="search" type="search" placeholder="Search agents…"
          /></label>
        </div>
        <IdentityTable
          v-if="state?.identities.length && visibleIdentities.length"
          :identities="visibleIdentities"
          @sleeve="(identity) => (dialog = { kind: 'sleeve', identity })"
          @communication="communication"
        />
        <div v-else-if="state?.identities.length" class="empty">
          <h3>No matching agents.</h3>
          <p>Try another name or show all your agents.</p>
          <button
            class="secondary"
            @click="
              search = '';
              filter = 'all';
            "
          >
            Clear filters
          </button>
        </div>
        <GettingStarted
          :connected="Boolean(state) && !error"
          v-else-if="state"
          :identities="state.identities"
          :external-identities="state.external_identities"
          @create="dialog = { kind: 'identity', identity: null }"
          @sleeve="(identity) => (dialog = { kind: 'sleeve', identity })"
          @communication="communication"
          @access="navigate('external-identities')"
          @settings="navigate('settings')"
        />
        <div v-else class="loading-state" role="status">
          <span class="loading-line"></span><span class="loading-line"></span
          ><span class="loading-line"></span>
          <p>Bringing your workspace home…</p>
        </div>
        <div class="table-footer">
          <span
            ><span class="mini-dot" :class="{ warning: error }"></span>
            {{
              error
                ? "Status may be out of date"
                : "Status refreshes every 4 seconds"
            }}</span
          ><button class="quiet" @click="refresh">
            Refresh status <span aria-hidden="true">↻</span>
          </button>
        </div>
      </section>
      <section class="activity" aria-labelledby="activity-title">
        <div class="section-header">
          <h2 id="activity-title">Recent activity</h2>
          <span v-if="state" class="muted"
            >{{ state.pending_deliveries }} replies pending ·
            {{ state.uncertain_instructions.length }} instructions to
            review</span
          >
        </div>
        <ul>
          <li
            v-for="(event, index) in state?.events.slice(-6).reverse() || []"
            :key="`${event.time}:${index}`"
          >
            <time :datetime="new Date(event.time * 1000).toISOString()">{{
              new Date(event.time * 1000).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })
            }}</time
            ><span
              ><strong>{{ event.identity }}</strong> · {{ event.message }}</span
            >
          </li>
          <li v-if="state && !state.events.length">
            Activity will appear here as your agents connect and work.
          </li>
        </ul>
      </section>
    </template>
    <template v-else-if="page === 'guide'"
      ><div class="page-intro">
        <span class="eyebrow">MAKE YOURSELF AT HOME</span>
        <h1>Your first conversation.</h1>
        <p>A few connections now. Your Codex, wherever you go next.</p>
      </div>
      <section class="guide-section">
        <GettingStarted
          :connected="Boolean(state) && !error"
          :identities="state?.identities || []"
          :external-identities="state?.external_identities || []"
          @create="dialog = { kind: 'identity', identity: null }"
          @sleeve="(identity) => (dialog = { kind: 'sleeve', identity })"
          @communication="communication"
          @access="navigate('external-identities')"
          @settings="navigate('settings')"
        /></section
    ></template>
    <Settings v-else-if="page === 'settings'" @saved="refresh" />
    <ExternalIdentities
      v-else
      :identities="state?.external_identities || []"
      :agents="state?.identities || []"
      :senders="state?.unregistered_senders || []"
      @create="dialog = { kind: 'external', identity: null }"
      @edit="(identity) => (dialog = { kind: 'external', identity })"
      @register="
        (sender) => (dialog = { kind: 'external', identity: null, sender })
      "
    />
    <footer>
      <span>Built for your computer. Ready for your next idea.</span
      ><span
        >Local workspace <span aria-hidden="true">·</span> Codex ↔
        Telegram</span
      >
    </footer>
  </main>
  <IdentityDialog
    v-if="dialog?.kind === 'identity'"
    :identity="dialog.identity"
    :connect-telegram="dialog.connectTelegram"
    @close="dialog = null"
    @saved="saved"
  />
  <ExternalIdentityDialog
    v-if="dialog?.kind === 'external'"
    :identity="dialog.identity"
    :sender="dialog.sender"
    :agents="state?.identities || []"
    @close="dialog = null"
    @saved="saved"
  />
  <SleeveDialog
    v-if="dialog?.kind === 'sleeve'"
    :identity="dialog.identity"
    :identities="state.identities"
    :default-socket="state.default_socket"
    :allowed-folders="state.sleeve_allowed_folders || []"
    @close="dialog = null"
    @saved="saved"
  />
</template>
