<script setup>
import {computed, reactive, ref} from 'vue';
import {api} from '../api.js';
import Modal from './Modal.vue';

const props = defineProps({identity: {type: Object, default: null}, sender: {type: Object, default: null}, agents: {type: Array, required: true}});
const emit = defineEmits(['close', 'saved']);
const busy = ref(false);
const error = ref('');
const form = reactive({
  userId: props.identity?.user_id || props.sender?.user_id || '',
  username: props.identity?.username || props.sender?.username || '',
  mode: props.identity?.communication_policy.mode || 'always_blocked',
  agentIds: [...(props.identity?.communication_policy.agent_ids || [])],
});
const title = computed(() => props.identity ? `External identity · ${props.identity.username || props.identity.user_id}` : 'New external identity');

async function save() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  const communication_policy = {mode: form.mode, agent_ids: form.mode === 'specific_agents' ? form.agentIds : []};
  try {
    if (props.identity) await api(`/api/external-identities/${encodeURIComponent(props.identity.id)}`, {communication_policy});
    else await api('/api/external-identities', {provider: 'telegram', user_id: form.userId.trim() || null, username: form.username.trim() || null, communication_policy});
    emit('saved');
  } catch (failure) {
    error.value = failure.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <Modal :title="title" eyebrow="EXTERNAL IDENTITY" :busy="busy" :error="error" @close="emit('close')">
    <p class="muted">A registered sender and its access across all agent identities.</p>
    <form @submit.prevent="save">
      <fieldset :disabled="busy">
        <p class="provider-label">Provider <strong>Telegram</strong></p>
        <label for="external-username">Telegram username</label>
        <input id="external-username" v-model="form.username" :disabled="!!identity" :required="!form.userId.trim()" autocomplete="off" placeholder="@username">
        <label for="external-user-id">Telegram user ID</label>
        <input id="external-user-id" v-model="form.userId" :disabled="!!identity" :required="!form.username.trim()" inputmode="numeric" pattern="[1-9][0-9]*" autocomplete="off" placeholder="Numeric account ID">
        <small>Use a user ID or username. Once matched, the numeric account ID keeps access tied to that account.</small>
        <label for="communication-policy">Communication policy</label>
        <select id="communication-policy" v-model="form.mode">
          <option value="always_blocked">Always blocked</option>
          <option value="always_allowed">Always allowed</option>
          <option value="specific_agents">Specific agents</option>
        </select>
        <p v-if="form.mode === 'always_allowed'" class="policy-help">Can talk to all current and future agents.</p>
        <p v-else-if="form.mode === 'always_blocked'" class="policy-help">All incoming messages from this identity are rejected.</p>
        <fieldset v-else class="agent-choices">
          <legend>Allowed agents</legend>
          <label v-for="agent in agents" :key="agent.id" class="checkbox-label"><input v-model="form.agentIds" type="checkbox" :value="agent.id">{{ agent.name }}</label>
          <p v-if="!agents.length" class="muted">No agent identities yet.</p>
          <small>Only selected agents are allowed. No selection rejects all messages.</small>
        </fieldset>
        <div class="dialog-actions"><button type="button" class="secondary" @click="emit('close')">Cancel</button><button type="submit">{{ busy ? 'Saving…' : identity ? 'Save policy' : 'Register identity' }}</button></div>
      </fieldset>
    </form>
  </Modal>
</template>
