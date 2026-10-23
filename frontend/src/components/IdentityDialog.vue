<script setup>
import {computed, reactive, ref} from 'vue';
import {api, identityPath} from '../api.js';
import Modal from './Modal.vue';

const props = defineProps({identity: {type: Object, default: null}, connectTelegram: {type: Boolean, default: false}});
const emit = defineEmits(['close', 'saved']);
const busy = ref(false);
const error = ref('');
const form = reactive({
  name: '', method: props.identity?.telegram || props.connectTelegram ? 'telegram' : 'none', token: '',
});
const title = computed(() => props.identity ? `Communication · ${props.identity.name}` : 'New identity');

async function save() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  const body = {};
  if (form.method === 'none') body.telegram = null;
  else if (form.token.trim()) body.telegram = {token: form.token.trim()};
  try {
    if (props.identity) await api(`${identityPath(props.identity.name)}/communication`, body);
    else await api('/api/identities', {...body, name: form.name});
    emit('saved');
  } catch (failure) {
    error.value = failure.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <Modal :title="title" eyebrow="AGENT IDENTITY" :busy="busy" :error="error" @close="emit('close')">
    <p class="muted">{{ identity ? 'Manage how this identity receives messages. Its identity and sleeve stay in place.' : 'Create a lasting identity. You can assign a sleeve later.' }}</p>
    <form @submit.prevent="save">
      <fieldset :disabled="busy">
        <template v-if="!identity"><label for="identity-name">Name</label><input id="identity-name" v-model="form.name" maxlength="80" required autocomplete="off" placeholder="e.g. Research partner"></template>
        <label for="communication-method">Communication</label>
        <select id="communication-method" v-model="form.method"><option value="none">No connection</option><option value="telegram">Telegram bot</option></select>
        <template v-if="form.method === 'telegram'">
          <label for="bot-token">Telegram bot token</label>
          <input id="bot-token" v-model="form.token" type="password" :required="!identity?.telegram" autocomplete="new-password" :placeholder="identity?.telegram ? 'Leave blank to keep the current token' : 'Paste your BotFather token'" aria-describedby="token-help">
          <small id="token-help">Stored locally. Leave blank when editing to keep the existing token.</small>
          <div class="form-tip"><strong>Need a bot?</strong><p>Open <a href="https://t.me/BotFather" rel="noreferrer">BotFather</a>, send <code>/newbot</code>, then paste the token it gives you here. Register your own Telegram account in External Identities to allow messages through.</p></div>
        </template>
        <p class="policy-help">Sender access is managed in External Identities.</p>
        <div class="dialog-actions"><button type="button" class="secondary" @click="emit('close')">Cancel</button><button type="submit">{{ busy ? 'Saving…' : identity ? 'Save changes' : 'Create identity' }}</button></div>
      </fieldset>
    </form>
  </Modal>
</template>
