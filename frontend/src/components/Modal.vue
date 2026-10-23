<script setup>
import {onBeforeUnmount, onMounted, ref, useId} from 'vue';

defineProps({title: String, eyebrow: String, busy: Boolean, error: String});
const emit = defineEmits(['close']);
const dialog = ref(null);
const titleId = useId();
onMounted(() => dialog.value.showModal());
onBeforeUnmount(() => dialog.value.close());
</script>

<template>
  <dialog ref="dialog" :aria-labelledby="titleId" @cancel.prevent="!busy && emit('close')">
    <div class="dialog-header">
      <div><span class="eyebrow">{{ eyebrow }}</span><h2 :id="titleId">{{ title }}</h2></div>
      <button type="button" class="icon quiet" :disabled="busy" aria-label="Close dialog" @click="emit('close')">×</button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <slot />
  </dialog>
</template>
