<script setup>
defineProps({identities: {type: Array, required: true}, agents: {type: Array, required: true}, senders: {type: Array, required: true}});
defineEmits(['create', 'edit', 'register']);
const policies = {always_allowed: 'Always allowed', always_blocked: 'Always blocked', specific_agents: 'Specific agents'};
const agentNames = (policy, agents) => policy.agent_ids.map(id => agents.find(agent => agent.id === id)?.name || id).join(', ') || 'No agents selected';
</script>

<template>
  <section aria-labelledby="external-identities-title">
    <div class="section-header"><div><span class="eyebrow">ACCESS CONTROL</span><h1 id="external-identities-title">External Identities</h1><p>Recognize the sender. Apply their communication policy.</p></div><button @click="$emit('create')">+ New external identity</button></div>
    <p class="summary">Unregistered senders are rejected. Policies apply across all communication connections.</p>
    <div class="table-wrap">
      <table>
        <caption class="sr-only">External identities and their communication policies</caption>
        <thead><tr><th scope="col">External identity</th><th scope="col">Provider</th><th scope="col">Communication policy</th><th scope="col">Allowed agents</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody><tr v-for="identity in identities" :key="identity.id">
          <td><strong>{{ identity.username || identity.user_id }}</strong><code>{{ identity.user_id || 'Account ID pending first contact' }}</code></td>
          <td>Telegram</td>
          <td><span class="badge" :class="identity.communication_policy.mode">{{ policies[identity.communication_policy.mode] }}</span></td>
          <td class="policy-agents">{{ identity.communication_policy.mode === 'always_allowed' ? 'All current and future agents' : identity.communication_policy.mode === 'always_blocked' ? 'None' : agentNames(identity.communication_policy, agents) }}</td>
          <td class="row-actions"><button class="quiet" :aria-label="`Edit policy for ${identity.username || identity.user_id}`" @click="$emit('edit', identity)">Edit policy</button></td>
        </tr></tbody>
      </table>
    </div>
    <div v-if="!identities.length" class="empty"><h2>No external identities registered.</h2><p>Register a sender to set their access policy.</p></div>
    <div class="table-footer"><span>{{ identities.length }} registered {{ identities.length === 1 ? 'identity' : 'identities' }}</span></div>
  </section>
  <section class="activity" aria-labelledby="unregistered-title">
    <div class="section-header"><div><h2 id="unregistered-title">Unregistered senders</h2><p>Recent private senders. Their messages were rejected and will not be replayed.</p></div></div>
    <div v-if="senders.length" class="table-wrap"><table>
      <caption class="sr-only">Unregistered Telegram senders</caption>
      <thead><tr><th scope="col">Sender</th><th scope="col">Contacted agents</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="sender in senders" :key="sender.user_id"><td><strong>{{ sender.username ? `@${sender.username.replace(/^@/, '')}` : sender.first_name || 'Telegram user' }}</strong><code>{{ sender.user_id }}</code></td><td>{{ sender.agents.join(', ') }}</td><td class="row-actions"><button class="quiet" :aria-label="`Register sender ${sender.user_id}`" @click="$emit('register', sender)">Register identity</button></td></tr></tbody>
    </table></div>
    <p v-else class="summary">No recent unregistered senders.</p>
  </section>
</template>
