<script setup>
import ResumeCommand from './ResumeCommand.vue';
defineProps({identities: {type: Array, required: true}});
defineEmits(['sleeve', 'communication']);
const statuses = {active: 'Working', ready: 'Ready', idle: 'Idle', unassigned: 'Needs sleeve', unavailable: 'Unavailable', checking: 'Checking', systemError: 'Runtime error', notLoaded: 'Not loaded'};
</script>

<template>
  <div class="table-wrap">
    <table>
      <caption class="sr-only">Agent identities, their current sleeves, runtime status, and communication settings</caption>
      <thead><tr><th scope="col">Identity</th><th scope="col">Current sleeve</th><th scope="col">Status</th><th scope="col">Communication</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
        <tr v-for="identity in identities" :key="identity.id">
          <td>
            <div class="identity-cell">
              <span class="avatar" aria-hidden="true">{{ identity.name.slice(0, 2).toUpperCase() }}</span>
              <div :title="identity.id"><strong>{{ identity.name }}</strong><code>{{ identity.id.slice(0, 8) }}</code></div>
            </div>
          </td>
          <td class="sleeve-cell">
            <template v-if="identity.sleeve?.kind === 'admin_app'"><strong>AdminAppSleeve</strong><span class="muted">Built-in commands · singleton</span></template>
            <template v-else-if="identity.sleeve">
              <strong>{{ identity.sleeve.thread_name || 'Untitled conversation' }}</strong>
              <span class="muted">{{ identity.sleeve.cwd || 'Codex sleeve' }}</span>
              <code :title="identity.sleeve.thread_id">{{ identity.sleeve.thread_id.slice(-8) }}</code>
              <ResumeCommand :sleeve="identity.sleeve" />
            </template>
            <span v-else class="muted">No sleeve assigned</span>
          </td>
          <td><span class="badge" :class="identity.runtime.status" :title="identity.runtime.detail || ''">{{ statuses[identity.runtime.status] || identity.runtime.status }}</span><span v-if="identity.sleeve_process" class="muted">{{ {running: 'CLI running', stopped: 'CLI stopped', checking: 'Checking CLI'}[identity.sleeve_process.status] }}</span></td>
          <td>
            <template v-if="identity.telegram">
              <strong>@{{ identity.telegram.username }}</strong>
              <span class="muted">{{ identity.polling ? 'Listening' : 'Not listening' }}</span>
            </template>
            <span v-else class="muted">No connection</span>
          </td>
          <td class="row-actions">
            <button class="quiet" :aria-label="`Manage sleeve for ${identity.name}`" @click="$emit('sleeve', identity)">{{ identity.sleeve ? 'Change sleeve' : 'Assign sleeve' }}</button>
            <button class="quiet" :aria-label="`Communication settings for ${identity.name}`" @click="$emit('communication', identity)">Settings</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
