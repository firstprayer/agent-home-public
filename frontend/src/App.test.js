import {flushPromises, mount} from '@vue/test-utils';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import App from './App.vue';
import IdentityTable from './components/IdentityTable.vue';
import IdentityDialog from './components/IdentityDialog.vue';
import SleeveDialog from './components/SleeveDialog.vue';
import {api} from './api.js';

vi.mock('./api.js', async original => ({...await original(), api: vi.fn()}));

const THREAD = '11111111-1111-4111-8111-111111111111';
const identity = (overrides = {}) => ({
  id: 'identity-uuid', name: 'Research partner', sleeve: null, telegram: null,
  runtime: {status: 'unassigned'}, polling: false,
  ...overrides,
});
const state = identities => ({identities, external_identities: [], unregistered_senders: [], default_socket: '/tmp/codex.sock', events: [], pending_deliveries: 0, uncertain_instructions: []});
const sleeveProps = current => ({identity: current, identities: [current], defaultSocket: '/tmp/codex.sock'});
beforeEach(() => { api.mockReset(); location.hash = ''; });

describe('identity dashboard', () => {
  it('distinguishes a stopped CLI from a still-idle Codex session', () => {
    const current = identity({sleeve: {kind: 'codex', thread_id: THREAD},
      runtime: {status: 'idle'}, sleeve_process: {status: 'stopped'}});
    const wrapper = mount(IdentityTable, {props: {identities: [current]}});
    expect(wrapper.text()).toContain('Idle');
    expect(wrapper.text()).toContain('CLI stopped');
  });

  it('restores the saved address rather than the currently selected replacement', async () => {
    const current = identity({sleeve: {kind: 'codex', thread_id: THREAD, socket_path: '/tmp/original.sock'},
      sleeve_process: {status: 'stopped'}});
    api.mockResolvedValue({threads: [], socket_path: '/tmp/original.sock'});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(current)});
    await flushPromises();
    await wrapper.get('#socket-path').setValue('/tmp/replacement.sock');
    api.mockResolvedValueOnce({thread_id: THREAD});
    await wrapper.findAll('button').find(button => button.text() === 'Restore current sleeve').trigger('click');
    await flushPromises();
    expect(api).toHaveBeenLastCalledWith('/api/identities/Research%20partner/sleeve/recover',
      {thread_id: THREAD, socket_path: '/tmp/original.sock'});
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('keeps recovery errors visible and retains the saved session', async () => {
    const current = identity({sleeve: {kind: 'codex', thread_id: THREAD, socket_path: '/tmp/original.sock'},
      sleeve_process: {status: 'stopped'}});
    api.mockResolvedValue({threads: [], socket_path: '/tmp/original.sock'});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(current)});
    await flushPromises();
    api.mockRejectedValueOnce(new Error('Saved history is missing'));
    await wrapper.findAll('button').find(button => button.text() === 'Restore current sleeve').trigger('click');
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe('Saved history is missing');
    expect(wrapper.text()).toContain(THREAD);
    expect(wrapper.emitted('saved')).toBeUndefined();
  });

  it('preserves keyed action controls during status updates and escapes identity names', async () => {
    const current = identity({name: '<img src=x onerror=alert(1)>'});
    const wrapper = mount(IdentityTable, {props: {identities: [current]}});
    const action = wrapper.find('button').element;
    await wrapper.setProps({identities: [{...current, runtime: {status: 'active'}}]});
    expect(wrapper.find('button').element).toBe(action);
    expect(wrapper.text()).toContain('Working');
    expect(wrapper.find('img').exists()).toBe(false);
    expect(wrapper.text()).toContain(current.name);
  });

  it('loads state and pauses polling while a dialog is open', async () => {
    vi.useFakeTimers();
    api.mockResolvedValue(state([identity()]));
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.get('[aria-label="Workspace overview"]').text()).toContain('Agent identities01');
    expect(wrapper.text()).toContain('1 identity awaits a sleeve');
    await wrapper.find('.section-header button').trigger('click');
    await vi.advanceTimersByTimeAsync(8000);
    expect(api).toHaveBeenCalledTimes(1);
    expect(wrapper.find('dialog').element.open).toBe(true);
    await wrapper.find('dialog [aria-label="Close dialog"]').trigger('click');
    await vi.advanceTimersByTimeAsync(4000);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('opens external identity management through top-level navigation', async () => {
    api.mockResolvedValue(state([identity()]));
    const wrapper = mount(App);
    await flushPromises();
    await wrapper.get('nav a[href="#external-identities"]').trigger('click');
    expect(wrapper.get('nav a[href="#external-identities"]').attributes('aria-current')).toBe('page');
    expect(wrapper.get('h1').text()).toBe('External Identities');
    expect(wrapper.text()).toContain('Unregistered senders are rejected.');
    await wrapper.get('.section-header button').trigger('click');
    expect(wrapper.get('dialog').text()).toContain('New external identity');
  });

  it('keeps creation available when initial state loading fails', async () => {
    api.mockRejectedValueOnce(new Error('Service unavailable'));
    const wrapper = mount(App);
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toBe('Service unavailable');
    await wrapper.find('.section-header button').trigger('click');
    expect(wrapper.findComponent(IdentityDialog).exists()).toBe(true);
  });
});

describe('identity and communication dialogs', () => {
  it('creates an identity without requiring Telegram or a sleeve', async () => {
    api.mockResolvedValue({});
    const wrapper = mount(IdentityDialog);
    expect(wrapper.text()).not.toContain('Communication Rules');
    expect(wrapper.text()).toContain('Sender access is managed in External Identities.');
    expect(wrapper.find('textarea').exists()).toBe(false);
    await wrapper.get('#identity-name').setValue('Independent');
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenCalledWith('/api/identities', {name: 'Independent', telegram: null});
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('keeps existing token settings private without configuring sender policy', async () => {
    api.mockResolvedValue({});
    const wrapper = mount(IdentityDialog, {props: {identity: identity({telegram: {username: 'ResearchBot'}})}});
    expect(wrapper.get('#bot-token').element.required).toBe(false);
    expect(wrapper.get('#bot-token').attributes('type')).toBe('password');
    expect(wrapper.get('#bot-token').element.value).toBe('');
    expect(wrapper.find('textarea').exists()).toBe(false);
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenCalledWith('/api/identities/Research%20partner/communication', {});
  });

  it('requires a token for a new connection and retains input after a failed save', async () => {
    api.mockRejectedValueOnce(new Error('Invalid bot token'));
    const wrapper = mount(IdentityDialog);
    await wrapper.get('#identity-name').setValue('Research');
    await wrapper.get('#communication-method').setValue('telegram');
    expect(wrapper.get('#bot-token').element.required).toBe(true);
    await wrapper.get('#bot-token').setValue('invalid-token');
    await wrapper.get('form').trigger('submit');
    expect(api.mock.calls[0][1].telegram).toEqual({token: 'invalid-token'});
    expect(wrapper.get('[role="alert"]').text()).toBe('Invalid bot token');
    expect(wrapper.get('#identity-name').element.value).toBe('Research');
    expect(wrapper.emitted('saved')).toBeUndefined();
  });
});

describe('sleeve assignment', () => {
  it('assigns AdminAppSleeve even when Codex is unavailable', async () => {
    api.mockRejectedValueOnce(new Error('Codex offline')).mockResolvedValueOnce({});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(identity())});
    await flushPromises();
    await wrapper.get('#sleeve-kind').setValue('admin_app');
    expect(wrapper.find('#socket-path').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('Codex offline');
    expect(wrapper.get('button[type="submit"]').element.disabled).toBe(false);
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenLastCalledWith('/api/identities/Research%20partner/sleeve', {kind: 'admin_app'});
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('opens an existing admin sleeve without requesting Codex and disables another owner', async () => {
    const current = identity({sleeve: {kind: 'admin_app'}, runtime: {status: 'ready'}});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(current)});
    await flushPromises();
    expect(api).not.toHaveBeenCalled();
    expect(wrapper.get('#sleeve-kind').element.value).toBe('admin_app');
    expect(wrapper.text()).toContain('/help, /list, /kill');
    expect(wrapper.text()).not.toContain('/health');
    expect(wrapper.text()).not.toContain('/agents');
    const other = identity({id: 'other', name: 'Other', sleeve: null});
    await wrapper.setProps({identity: other, identities: [current, other]});
    expect(wrapper.get('option[value="admin_app"]').element.disabled).toBe(true);
    expect(wrapper.get('button[type="submit"]').element.disabled).toBe(true);
  });

  it('shows the admin runtime without Codex identifiers in the agent table', () => {
    const wrapper = mount(IdentityTable, {props: {identities: [identity({sleeve: {kind: 'admin_app'}, runtime: {status: 'ready'}})]}});
    expect(wrapper.text()).toContain('AdminAppSleeve');
    expect(wrapper.text()).toContain('Ready');
    expect(wrapper.text()).not.toContain('Untitled conversation');
  });

  it('disables already-owned sleeves and submits the exact selected address', async () => {
    const current = identity();
    const other = identity({id: 'other', name: 'Other', sleeve: {thread_id: 'owned', socket_path: '/tmp/codex.sock'}});
    api.mockResolvedValueOnce({socket_path: '/tmp/codex.sock', threads: [
      {id: THREAD, name: 'Available', cwd: '/repo'}, {id: 'owned', name: 'Owned', cwd: '/other'},
    ]}).mockResolvedValueOnce({});
    const wrapper = mount(SleeveDialog, {props: {...sleeveProps(current), identities: [current, other]}});
    await flushPromises();
    expect(wrapper.find('#thread-id').exists()).toBe(false);
    expect(wrapper.text()).not.toContain('Exact conversation ID');
    expect(wrapper.get('#sleeve-options').element.required).toBe(true);
    expect(wrapper.get('option[value="owned"]').element.disabled).toBe(true);
    await wrapper.get('#sleeve-options').setValue(THREAD);
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenLastCalledWith('/api/identities/Research%20partner/sleeve', {kind: 'codex', thread_id: THREAD, socket_path: '/tmp/codex.sock'});
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('allows detaching an unreachable sleeve', async () => {
    api.mockRejectedValueOnce(new Error('Codex offline')).mockResolvedValueOnce({});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(identity({sleeve: {thread_id: THREAD, socket_path: '/tmp/lost.sock'}}))});
    await flushPromises();
    expect(wrapper.text()).toContain('Codex offline');
    await wrapper.get('.danger').trigger('click');
    expect(api).toHaveBeenLastCalledWith('/api/identities/Research%20partner/sleeve', {thread_id: null});
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });

  it('does not submit without a listed, available root agent', async () => {
    api.mockResolvedValueOnce({socket_path: '/tmp/codex.sock', threads: []});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(identity())});
    await flushPromises();
    expect(wrapper.get('button[type="submit"]').element.disabled).toBe(true);
    expect(wrapper.text()).toContain('No root agents available');
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('ignores an old conversation lookup after the socket changes', async () => {
    let resolveOld;
    api.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce({socket_path: '/tmp/new.sock', threads: [{id: THREAD, name: 'New server', cwd: '/new'}]});
    const wrapper = mount(SleeveDialog, {props: sleeveProps(identity())});
    await wrapper.get('#socket-path').setValue('/tmp/new.sock');
    await wrapper.get('.input-action button').trigger('click');
    await flushPromises();
    resolveOld({socket_path: '/tmp/codex.sock', threads: [{id: 'stale', name: 'Old server', cwd: '/old'}]});
    await flushPromises();
    expect(wrapper.text()).toContain('New server');
    expect(wrapper.text()).not.toContain('Old server');
  });
});
