<script setup>
import {computed, ref, watch} from 'vue';

const props = defineProps({sleeve: {type: Object, required: true}});
const feedback = ref('');
// Quote every argument so workspace names cannot become shell syntax.
const quote = value => `'${value.replaceAll("'", "'\"'\"'")}'`;
const command = computed(() => {
  const {kind, socket_path, thread_id, cwd} = props.sleeve;
  if (kind !== 'codex' || !socket_path || !thread_id || !cwd) return '';
  return `codex resume --remote ${quote(`unix://${socket_path}`)} --cd ${quote(cwd)} ${quote(thread_id)}`;
});
watch(command, () => { feedback.value = ''; });

async function copy() {
  try {
    await navigator.clipboard.writeText(command.value);
    feedback.value = 'Copied. Paste into your terminal.';
  } catch {
    feedback.value = 'Clipboard unavailable. Select and copy the command above.';
  }
}
</script>

<template>
  <details v-if="command" class="resume-command">
    <summary>Connect in terminal</summary>
    <label>Run this command to join the current session
      <textarea :value="command" readonly rows="3" spellcheck="false" @focus="$event.target.select()"></textarea>
    </label>
    <button type="button" class="quiet" @click="copy">Copy resume command</button>
    <span v-if="feedback" role="status">{{ feedback }}</span>
  </details>
</template>

<style scoped>
.resume-command { max-width: 32rem; margin-top: .5rem; }
summary { cursor: pointer; }
label { display: block; margin-top: .75rem; }
textarea { display: block; width: 100%; margin-top: .5rem; font-family: monospace; }
[role="status"] { display: block; margin-top: .5rem; }
</style>
