<script setup>
import {computed, nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {api, identityPath} from '../api.js';
import Modal from './Modal.vue';
import ResumeCommand from './ResumeCommand.vue';

const props = defineProps({identity: {type: Object, required: true}, identities: {type: Array, required: true}, defaultSocket: String, allowedFolders: {type: Array, default: () => []}});
const emit = defineEmits(['close', 'saved']);
const kind = ref(props.identity.sleeve?.kind || 'codex');
const socketPath = ref(props.identity.sleeve?.socket_path || props.defaultSocket);
const selectedThread = ref('');
const threads = ref([]);
const loadedSocket = ref('');
const loaded = ref(false);
const loading = ref(false);
const busy = ref(false);
const error = ref('');
const creating = ref(false);
const folder = ref('');
const created = ref('');
let creationRequestId;
let request;
const title = computed(() => `${props.identity.sleeve ? 'Change' : 'Assign'} sleeve · ${props.identity.name}`);
const choices = computed(() => threads.value.map(thread => ({...thread, owner: props.identities.find(identity =>
  identity.id !== props.identity.id && identity.sleeve?.thread_id === thread.id && identity.sleeve.socket_path === loadedSocket.value)?.name})));
const adminOwner = computed(() => props.identities.find(identity => identity.id !== props.identity.id && identity.sleeve?.kind === 'admin_app')?.name);
const canAssign = computed(() => kind.value === 'admin_app' ? !adminOwner.value : !loading.value && Boolean(selectedChoice.value));
const selectedChoice = computed(() => choices.value.find(thread => thread.id === selectedThread.value && !thread.owner));

async function recoverSleeve() {
  if (busy.value) return;
  busy.value = true;
  error.value = '';
  request?.abort();
  loading.value = false;
  try {
    const current = props.identity.sleeve;
    await api(`${identityPath(props.identity.name)}/sleeve/recover`, {
      thread_id: current.thread_id, socket_path: current.socket_path,
    });
    emit('saved');
  } catch (failure) { error.value = failure.message; }
  finally { busy.value = false; }
}

async function createSleeve() {
  if (busy.value || !props.allowedFolders.length || !folder.value.trim()) return;
  busy.value = true;
  error.value = '';
  request?.abort();
  loading.value = false;
  creationRequestId ||= crypto.randomUUID();
  try {
    const result = await api('/api/sleeves', {folder: folder.value.trim(), request_id: creationRequestId});
    socketPath.value = result.socket_path;
    await nextTick();
    await load();
    if (!loaded.value) return;
    selectedThread.value = result.thread_id;
    created.value = 'Codex sleeve created. Select Assign sleeve to connect this identity. It keeps running when Agent Home exits.';
    creating.value = false;
    creationRequestId = undefined;
  } catch (failure) { error.value = failure.message; }
  finally { busy.value = false; }
}

watch(kind, value => {
  creating.value = false;
  request?.abort();
  loading.value = false;
  error.value = '';
  if (value === 'codex') load();
});
watch(folder, () => { creationRequestId = undefined; });

watch(socketPath, () => {
  request?.abort();
  threads.value = [];
  selectedThread.value = '';
  loaded.value = false;
  loading.value = false;
});

async function load() {
  request?.abort();
  const controller = new AbortController();
  request = controller;
  const currentId = selectedThread.value || props.identity.sleeve?.thread_id;
  threads.value = [];
  selectedThread.value = '';
  loaded.value = false;
  loading.value = true;
  error.value = '';
  try {
    const result = await api(`/api/threads?socket_path=${encodeURIComponent(socketPath.value.trim())}`, undefined, controller.signal);
    if (controller.signal.aborted) return;
    threads.value = result.threads;
    loadedSocket.value = result.socket_path;
    loaded.value = true;
    selectedThread.value = choices.value.some(thread => thread.id === currentId && !thread.owner) ? currentId : '';
  } catch (failure) {
    if (!controller.signal.aborted) error.value = failure.message;
  } finally {
    if (request === controller) loading.value = false;
  }
}

async function save(detach = false) {
  if (busy.value || (!detach && !canAssign.value)) return;
  request?.abort();
  loading.value = false;
  busy.value = true;
  error.value = '';
  try {
    await api(`${identityPath(props.identity.name)}/sleeve`, detach ? {thread_id: null} : kind.value === 'admin_app' ? {kind: 'admin_app'} : {
      kind: 'codex', thread_id: selectedThread.value, socket_path: loadedSocket.value,
    });
    emit('saved');
  } catch (failure) {
    error.value = failure.message;
  } finally {
    busy.value = false;
  }
}

onMounted(() => { if (kind.value === 'codex') load(); });
onBeforeUnmount(() => request?.abort());
</script>

<template>
  <Modal :title="title" eyebrow="AGENT SLEEVE" :busy="busy" :error="error" @close="emit('close')">
    <p class="muted">Choose a sleeve. Stop an active Codex turn before replacing its sleeve.</p>
    <form @submit.prevent="creating ? createSleeve() : save()">
      <fieldset :disabled="busy">
        <label for="sleeve-kind">Sleeve type</label>
        <select id="sleeve-kind" v-model="kind"><option value="codex">Codex root agent</option><option value="admin_app" :disabled="Boolean(adminOwner)">AdminAppSleeve{{ adminOwner ? ` · Assigned to ${adminOwner}` : '' }}</option></select>
        <p v-if="kind === 'admin_app'" class="policy-help">Built-in administration. No Codex process required. Commands: /help, /list, /kill &lt;agent name&gt;. Only one identity can hold this sleeve. It shares the Agent Home server's availability.</p>
        <template v-else>
        <ResumeCommand v-if="identity.sleeve?.kind === 'codex'" :sleeve="identity.sleeve" />
        <div v-if="identity.sleeve_process && !creating" class="saved-sleeve">
          <strong>Current saved session</strong>
          <code>{{ identity.sleeve.thread_id }}</code>
          <p class="policy-help">Restore this session and its history. Reconnects the background service and reopens a stopped CLI. Previous instructions are not resent.</p>
          <button type="button" class="secondary" @click="recoverSleeve">{{ busy ? 'Restoring…' : 'Restore current sleeve' }}</button>
        </div>
        <template v-if="creating">
          <label for="new-sleeve-folder">Existing working folder</label>
          <input id="new-sleeve-folder" v-model="folder" required spellcheck="false">
          <p class="policy-help">Starts an independent Codex root agent with --yolo in the background. Sends one initialization message asking it to reply Ready before connecting. No approval prompts or sandbox. Agent Home does not stop this process on exit or restart.</p>
          <small v-if="!allowedFolders.length">Creation is disabled. Add allowed working folders in Settings first.</small>
          <small v-else>Allowed folders and subdirectories: {{ allowedFolders.join(', ') }}</small>
        </template>
        <template v-else>
        <p v-if="created" class="success" role="status">{{ created }}</p>
        <label for="socket-path">Codex socket</label>
        <div class="input-action"><input id="socket-path" v-model="socketPath" required><button type="button" class="secondary" :disabled="loading" @click="load">{{ loading ? 'Loading…' : 'Load' }}</button></div>
        <label for="sleeve-options">Available root agents</label>
        <select id="sleeve-options" v-model="selectedThread" required>
          <option value="" disabled>{{ loaded ? threads.length ? 'Choose a root agent' : 'No root agents available' : 'Load root agents to choose a sleeve' }}</option>
          <option v-for="thread in choices" :key="thread.id" :value="thread.id" :disabled="Boolean(thread.owner)">{{ thread.name || thread.agentNickname || 'Untitled' }} · {{ thread.cwd }} · {{ thread.id.slice(-8) }}{{ thread.owner ? ` · Assigned to ${thread.owner}` : '' }}</option>
        </select>
        <small v-if="loading">Loading available sleeves…</small>
        <small v-else-if="loaded">{{ threads.length }} root agents available. Select one to assign a sleeve.</small>
        <small v-else-if="error">The Codex server is unavailable. You can retry or detach the current sleeve.</small>
        <small v-else>Each sleeve can belong to one identity. Model, folder, and permission settings are preserved.</small>
        <button type="button" class="secondary new-sleeve" @click="creating = true">New Codex sleeve</button>
        </template>
        </template>
        <div class="dialog-actions">
          <button v-if="identity.sleeve && !creating" type="button" class="danger" @click="save(true)">Detach sleeve</button><span class="spacer"></span>
          <button v-if="creating" type="button" class="secondary" @click="creating = false">Back</button>
          <button type="button" class="secondary" @click="emit('close')">Cancel</button>
          <button v-if="creating" type="submit" :disabled="!allowedFolders.length || !folder.trim()">{{ busy ? 'Starting…' : 'Create sleeve' }}</button>
          <button v-else type="submit" :disabled="!canAssign">{{ busy ? 'Saving…' : 'Assign sleeve' }}</button>
        </div>
      </fieldset>
    </form>
  </Modal>
</template>
