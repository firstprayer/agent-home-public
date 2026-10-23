import {flushPromises, mount} from '@vue/test-utils';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import App from './App.vue';
import GettingStarted from './components/GettingStarted.vue';
import {api} from './api.js';

vi.mock('./api.js', async original => ({...await original(), api: vi.fn()}));
const agent = {id: 'agent-id', name: 'Studio', sleeve: {kind: 'codex', thread_id: 'session-id'},
  telegram: {username: 'StudioDemoBot'}, polling: true, runtime: {status: 'idle'}};
const access = agentIds => [{communication_policy: {mode: 'specific_agents', agent_ids: agentIds}}];
beforeEach(() => { api.mockReset(); location.hash = ''; });
afterEach(() => vi.useRealTimers());

describe('first conversation guide', () => {
  it('offers identity creation for an empty workspace without attempting provider calls', async () => {
    const wrapper = mount(GettingStarted);
    await wrapper.findAll('button').find(button => button.text().includes('Create your first identity')).trigger('click');
    expect(wrapper.emitted('create')).toHaveLength(1);
    expect(wrapper.find('a[href*="StudioDemoBot"]').exists()).toBe(false);
    expect(api).not.toHaveBeenCalled();
  });

  it('requires a reachable sleeve, listening bot, and a policy allowing this exact identity', async () => {
    const wrapper = mount(GettingStarted, {props: {identities: [agent], externalIdentities: access(['other-id'])}});
    expect(wrapper.find('.first-message.ready').exists()).toBe(false);
    await wrapper.setProps({externalIdentities: access([agent.id])});
    expect(wrapper.get('.first-message a').attributes('href')).toBe('https://t.me/StudioDemoBot');
    await wrapper.setProps({connected: false});
    expect(wrapper.find('.first-message.ready').exists()).toBe(false);
    expect(wrapper.get('.setup-pending').text()).toBe('Waiting for the desktop service');
    await wrapper.setProps({connected: true});
    await wrapper.setProps({identities: [{...agent, polling: false}]});
    expect(wrapper.find('.first-message.ready').exists()).toBe(false);
    await wrapper.setProps({identities: [{...agent, runtime: {status: 'unavailable'}}]});
    expect(wrapper.find('.first-message.ready').exists()).toBe(false);
    await wrapper.setProps({identities: [agent], externalIdentities: [{communication_policy: {mode: 'always_blocked', agent_ids: []}}]});
    expect(wrapper.find('.first-message.ready').exists()).toBe(false);
  });

  it('keeps setup scoped to the selected identity and never treats Admin as a Codex session', async () => {
    const second = {...agent, id: 'second', name: 'Research', telegram: null, sleeve: null};
    const wrapper = mount(GettingStarted, {props: {identities: [agent, second, {...agent, id: 'admin', sleeve: {kind: 'admin_app'}}]}});
    expect(wrapper.findAll('option')).toHaveLength(2);
    await wrapper.get('#guide-agent').setValue(second.id);
    await wrapper.findAll('button').find(button => button.text().includes('Connect Telegram')).trigger('click');
    expect(wrapper.emitted('communication')[0]).toEqual([second]);
    await wrapper.setProps({identities: [agent]});
    expect(wrapper.get('#guide-agent').element.value).toBe(agent.id);
  });

  it('opens the Telegram form from the guide, preserving the ordinary creation defaults', async () => {
    location.hash = '#guide';
    api.mockResolvedValue({identities: [{...agent, telegram: null}], external_identities: [], events: [], pending_deliveries: 0, uncertain_instructions: [], unregistered_senders: []});
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.findAll('button').find(button => button.text().includes('Connect Telegram')).trigger('click');
    expect(wrapper.get('#communication-method').element.value).toBe('telegram');
    expect(wrapper.get('#bot-token').attributes('type')).toBe('password');
    wrapper.unmount();
  });

  it('filters the agent table locally and reports a disconnected desktop honestly', async () => {
    api.mockResolvedValueOnce({identities: [agent, {...agent, id: 'second', name: 'Research', sleeve: null, runtime: {status: 'unassigned'}}], external_identities: [], events: [], pending_deliveries: 0, uncertain_instructions: [], unregistered_senders: []});
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('input[type="search"]').setValue('Research');
    expect(wrapper.findAll('tbody tr')).toHaveLength(1);
    expect(wrapper.get('tbody').text()).toContain('Research');
    await wrapper.get('input[type="search"]').setValue('nothing');
    expect(wrapper.text()).toContain('No matching agents.');
    api.mockRejectedValueOnce(new Error('Connection lost'));
    await wrapper.findAll('button').find(button => button.text().includes('Refresh status')).trigger('click');
    await flushPromises();
    expect(wrapper.get('.service-status').text()).toBe('Service disconnected');
    expect(wrapper.text()).toContain('Status may be out of date');
    wrapper.unmount();
  });
});
