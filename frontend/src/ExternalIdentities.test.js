import {mount} from '@vue/test-utils';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import ExternalIdentities from './components/ExternalIdentities.vue';
import ExternalIdentityDialog from './components/ExternalIdentityDialog.vue';
import {api} from './api.js';

vi.mock('./api.js', () => ({api: vi.fn()}));
const agents = [{id: 'agent-one', name: 'Research'}, {id: 'agent-two', name: 'Producer'}];
const external = (mode = 'specific_agents') => ({id: 'external-one', provider: 'telegram', username: '@owner', user_id: '42', communication_policy: {mode, agent_ids: mode === 'specific_agents' ? ['agent-one'] : []}});
beforeEach(() => api.mockReset());

describe('external identity registry', () => {
  it('shows each identity policy and its agent scope', () => {
    const wrapper = mount(ExternalIdentities, {props: {agents, identities: [external(), {...external('always_allowed'), id: 'other', username: '@other'}], senders: []}});
    expect(wrapper.text()).toContain('Specific agents');
    expect(wrapper.text()).toContain('Research');
    expect(wrapper.text()).toContain('All current and future agents');
    expect(wrapper.findAll('tbody tr')).toHaveLength(2);
  });

  it('keeps unregistered senders separate and prefills registration without granting access', async () => {
    const sender = {user_id: '99', username: 'new_user', agents: ['Research']};
    const wrapper = mount(ExternalIdentities, {props: {agents, identities: [], senders: [sender]}});
    await wrapper.get('[aria-label="Register sender 99"]').trigger('click');
    expect(wrapper.emitted('register')).toEqual([[sender]]);
    const dialog = mount(ExternalIdentityDialog, {props: {agents, sender}});
    expect(dialog.get('#external-user-id').element.value).toBe('99');
    expect(dialog.get('#communication-policy').element.value).toBe('always_blocked');
    expect(api).not.toHaveBeenCalled();
  });

  it('registers an external identity with default blocked policy', async () => {
    api.mockResolvedValue({});
    const wrapper = mount(ExternalIdentityDialog, {props: {agents}});
    await wrapper.get('#external-username').setValue('@new_user');
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenCalledWith('/api/external-identities', {provider: 'telegram', user_id: null, username: '@new_user', communication_policy: {mode: 'always_blocked', agent_ids: []}});
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('updates only the policy with selected stable agent IDs', async () => {
    api.mockResolvedValue({});
    const current = external();
    const wrapper = mount(ExternalIdentityDialog, {props: {agents, identity: current}});
    expect(wrapper.get('#external-user-id').element.disabled).toBe(true);
    await wrapper.get('input[value="agent-one"]').setValue(false);
    await wrapper.get('input[value="agent-two"]').setValue(true);
    expect(api).not.toHaveBeenCalled();
    expect(current.communication_policy.agent_ids).toEqual(['agent-one']);
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenCalledWith('/api/external-identities/external-one', {communication_policy: {mode: 'specific_agents', agent_ids: ['agent-two']}});
  });

  it('clears agent scope for always-allowed and preserves selection for empty scope rejection', async () => {
    api.mockResolvedValue({});
    const wrapper = mount(ExternalIdentityDialog, {props: {agents, identity: external()}});
    await wrapper.get('#communication-policy').setValue('always_allowed');
    expect(wrapper.text()).toContain('Can talk to all current and future agents.');
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenLastCalledWith('/api/external-identities/external-one', {communication_policy: {mode: 'always_allowed', agent_ids: []}});
    await wrapper.get('#communication-policy').setValue('specific_agents');
    await wrapper.get('input[value="agent-one"]').setValue(false);
    expect(wrapper.text()).toContain('No selection rejects all messages.');
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenLastCalledWith('/api/external-identities/external-one', {communication_policy: {mode: 'specific_agents', agent_ids: []}});
  });

  it('retains policy input after a failed save and cancels without mutation', async () => {
    api.mockRejectedValueOnce(new Error('Policy references an unknown agent identity'));
    const wrapper = mount(ExternalIdentityDialog, {props: {agents, identity: external()}});
    await wrapper.get('form').trigger('submit');
    expect(wrapper.get('[role="alert"]').text()).toBe('Policy references an unknown agent identity');
    expect(wrapper.get('input[value="agent-one"]').element.checked).toBe(true);
    expect(wrapper.emitted('saved')).toBeUndefined();
    api.mockClear();
    await wrapper.get('.dialog-actions .secondary').trigger('click');
    expect(api).not.toHaveBeenCalled();
    expect(wrapper.emitted('close')).toHaveLength(1);
  });
});
