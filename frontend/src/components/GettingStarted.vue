<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  identities: { type: Array, default: () => [] },
  externalIdentities: { type: Array, default: () => [] },
  connected: { type: Boolean, default: true },
});
defineEmits(["create", "sleeve", "communication", "access", "settings"]);
const selectedId = ref("");
const agents = computed(() =>
  props.identities.filter((identity) => identity.sleeve?.kind !== "admin_app"),
);
watch(
  agents,
  (list) => {
    if (!list.some((identity) => identity.id === selectedId.value))
      selectedId.value = list[0]?.id || "";
  },
  { immediate: true },
);
const agent = computed(() =>
  agents.value.find((identity) => identity.id === selectedId.value),
);
const hasAccess = computed(
  () =>
    agent.value &&
    props.externalIdentities.some((identity) => {
      const policy = identity.communication_policy;
      return (
        policy.mode === "always_allowed" ||
        (policy.mode === "specific_agents" &&
          policy.agent_ids.includes(agent.value.id))
      );
    }),
);
const runtimeReady = computed(
  () =>
    agent.value?.sleeve &&
    ["idle", "active"].includes(agent.value.runtime.status),
);
const ready = computed(
  () =>
    props.connected &&
    runtimeReady.value &&
    agent.value?.telegram &&
    agent.value.polling &&
    hasAccess.value,
);
</script>

<template>
  <div class="getting-started">
    <div class="guide-heading">
      <div>
        <span class="eyebrow">THE FIRST CONVERSATION</span>
        <h2>From your desktop.<br />To wherever you are.</h2>
        <p>
          Connect a Codex session, add your Telegram bot, and give your own
          account access.
        </p>
      </div>
      <div v-if="agents.length" class="guide-agent">
        <label for="guide-agent">Set up an agent</label
        ><select id="guide-agent" v-model="selectedId">
          <option
            v-for="identity in agents"
            :key="identity.id"
            :value="identity.id"
          >
            {{ identity.name }}
          </option>
        </select>
      </div>
    </div>
    <ol class="setup-steps">
      <li :class="{ complete: agent?.sleeve }">
        <span class="step-number" aria-hidden="true">{{
          agent?.sleeve ? "✓" : "01"
        }}</span>
        <div>
          <span class="step-label">A PLACE TO WORK</span>
          <h3>Choose your Codex session.</h3>
          <p>
            Your agent keeps its identity. Its sleeve is the Codex session that
            does the work.
          </p>
          <p v-if="agent?.sleeve" class="step-detail">
            <span class="mini-dot"></span>
            {{ agent.sleeve.thread_name || "Codex session assigned" }}
          </p>
          <button v-if="!agent" @click="$emit('create')">
            Create your first identity <span aria-hidden="true">↗</span>
          </button>
          <button v-else class="secondary" @click="$emit('sleeve', agent)">
            {{ agent.sleeve ? "Manage session" : "Assign a sleeve" }}
            <span aria-hidden="true">↗</span>
          </button>
          <p class="step-hint">
            <a href="https://developers.openai.com/codex/cli" rel="noreferrer"
              >Install and sign in to Codex CLI</a
            >
            first. Choose a loaded root session, or add allowed folders in
            <button class="inline-button" @click="$emit('settings')">
              Settings
            </button>
            to create one.
          </p>
        </div>
      </li>
      <li :class="{ complete: agent?.telegram && agent.polling }">
        <span class="step-number" aria-hidden="true">{{
          agent?.telegram && agent.polling ? "✓" : "02"
        }}</span>
        <div>
          <span class="step-label">A FAMILIAR CHAT</span>
          <h3>Give it a Telegram bot.</h3>
          <p>
            Open
            <a href="https://t.me/BotFather" rel="noreferrer">BotFather</a>,
            send <code>/newbot</code>, and follow the prompts. Paste the bot
            token into your agent’s connection settings.
          </p>
          <p v-if="agent?.telegram" class="step-detail">
            <span class="mini-dot" :class="{ warning: !agent.polling }"></span>
            @{{ agent.telegram.username }} ·
            {{
              agent.polling
                ? "Listening"
                : "Listener unavailable — check Recent activity"
            }}
          </p>
          <button
            class="secondary"
            :disabled="!agent"
            @click="$emit('communication', agent)"
          >
            {{ agent?.telegram ? "Connection settings" : "Connect Telegram" }}
            <span aria-hidden="true">↗</span>
          </button>
          <p class="step-hint">
            Each bot belongs to one agent. You can create additional identities
            for different projects.
          </p>
        </div>
      </li>
      <li :class="{ complete: hasAccess }">
        <span class="step-number" aria-hidden="true">{{
          hasAccess ? "✓" : "03"
        }}</span>
        <div>
          <span class="step-label">YOUR INVITATION</span>
          <h3>Let your account in.</h3>
          <p>
            Register your own Telegram <strong>@username</strong> or numeric
            user ID in External Identities. Explicitly allow this agent, or
            choose Always allowed.
          </p>
          <p v-if="hasAccess" class="step-detail">
            <span class="mini-dot"></span> An external identity is allowed to
            talk to this agent.
          </p>
          <button class="secondary" @click="$emit('access')">
            Manage who can talk <span aria-hidden="true">↗</span>
          </button>
          <p class="step-hint">
            Use your personal Telegram account here. Unregistered senders are
            rejected. A registered bot is not automatically trusted.
          </p>
        </div>
      </li>
    </ol>
    <div class="first-message" :class="{ ready }">
      <div>
        <span class="eyebrow">{{
          ready ? "READY FOR YOU" : "ONE LAST STEP"
        }}</span>
        <h3>
          {{
            ready
              ? "Say hello from anywhere."
              : "Finish the connections. Then say hello."
          }}
        </h3>
        <p>
          Open your bot and send <code>What are we working on?</code> Your
          message starts work or steers the current turn.
        </p>
      </div>
      <a
        v-if="ready"
        class="button"
        :href="`https://t.me/${agent.telegram.username}`"
        rel="noreferrer"
        >Open in Telegram <span aria-hidden="true">↗</span></a
      >
      <span v-else class="setup-pending">{{
        !connected
          ? "Waiting for the desktop service"
          : !agent?.sleeve
            ? "Assign a Codex sleeve"
            : !runtimeReady
              ? "Waiting for a reachable Codex session"
              : !agent?.telegram
                ? "Connect a Telegram bot"
                : !agent.polling
                  ? "Telegram listener needs attention"
                  : "Register an allowed sender"
      }}</span>
    </div>
    <div class="guide-notes">
      <p>
        <strong>Bring more than words.</strong> Send photos, videos, or voice
        notes. Incoming files are limited to 20 MB; voice and video decoding use
        your agent’s local tools.
      </p>
      <p>
        <strong>This Mac is home base.</strong> Keep it awake, online, and
        running Agent Home. Your configured Codex service and Telegram handle
        their parts of the conversation.
      </p>
    </div>
  </div>
</template>
