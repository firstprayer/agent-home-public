<script setup>
import {onBeforeUnmount, onMounted, ref} from 'vue';
import {api} from '../api.js';

const emit = defineEmits(['saved']);
const settings = ref(null);
const dbPath = ref('');
const folders = ref('');
const busy = ref(false);
const error = ref('');
const success = ref('');
const request = new AbortController();

function populate(value) {
  settings.value = value;
  dbPath.value = value.db_path;
  folders.value = value.sleeve_allowed_folders.join('\n');
}
async function load() {
  error.value = '';
  try { populate(await api('/api/settings', undefined, request.signal)); }
  catch (failure) { if (!request.signal.aborted) error.value = failure.message; }
}
async function save() {
  if (busy.value || !settings.value?.editable) return;
  busy.value = true;
  error.value = success.value = '';
  try {
    const value = await api('/api/settings', {db_path: dbPath.value.trim(),
      sleeve_allowed_folders: folders.value.split('\n').map(p => p.trim()).filter(Boolean)}, request.signal);
    populate(value);
    success.value = 'Settings saved. Changes are active.';
    emit('saved');
  } catch (failure) { if (!request.signal.aborted) error.value = failure.message; }
  finally { busy.value = false; }
}
onMounted(load);
onBeforeUnmount(() => request.abort());
</script>

<template>
  <section aria-labelledby="settings-title">
    <div class="section-header"><div><span class="eyebrow">APPLICATION</span><h1 id="settings-title">Settings</h1><p>Storage and sleeve creation apply across all agent identities.</p></div></div>
    <div class="settings-content">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="success" class="success" role="status">{{ success }}</p>
      <p v-if="!settings && !error" class="muted">Loading settings…</p>
      <button v-if="!settings && error" class="secondary" @click="load">Retry</button>
      <template v-if="settings">
        <p v-if="!settings.editable" class="policy-help">Start with a service configuration file to save settings.</p>
        <form @submit.prevent="save">
          <fieldset :disabled="busy || !settings.editable">
            <h2>Storage</h2>
            <label for="database-path">Database path</label>
            <input id="database-path" v-model="dbPath" required spellcheck="false" aria-describedby="database-help">
            <small id="database-help">Choose a new database file to move all current data immediately. Existing files are never overwritten. The original database remains as a private backup.</small>
            <h2 class="settings-heading">Codex sleeves</h2>
            <label for="allowed-folders">Allowed working folders</label>
            <textarea id="allowed-folders" v-model="folders" rows="5" spellcheck="false" placeholder="/Users/you/projects" aria-describedby="folders-help"></textarea>
            <small id="folders-help">One directory per line. Each directory and its subdirectories are allowed. An empty list disables creation. Symlinks are checked against their actual destination.</small>
            <p class="policy-help">New sleeves run Codex with --yolo (no approval prompts or sandbox). This list controls where they start; it does not restrict their later filesystem access. Existing sleeves keep running when these settings change.</p>
            <div class="dialog-actions"><button type="submit">{{ busy ? 'Saving…' : 'Save settings' }}</button></div>
          </fieldset>
        </form>
        <p class="policy-help">Service configuration: <code>{{ settings.config_path || 'Not configured' }}</code><br>Dashboard port: {{ settings.port }}</p>
      </template>
    </div>
  </section>
</template>
