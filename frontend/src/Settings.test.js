import {flushPromises, mount} from '@vue/test-utils';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import Settings from './components/Settings.vue';
import SleeveDialog from './components/SleeveDialog.vue';
import {api} from './api.js';

vi.mock('./api.js', async original => ({...await original(), api: vi.fn()}));
const settings = {db_path:'/state/home.sqlite3', port:8765, sleeve_allowed_folders:[], config_path:'/config/service.toml', editable:true};
const props = {identity:{id:'identity', name:'Research', sleeve:null}, identities:[], defaultSocket:'/default.sock', allowedFolders:['/projects']};
const THREAD='11111111-1111-4111-8111-111111111111';
beforeEach(() => api.mockReset());

describe('application settings', () => {
  it('saves application-wide storage and directories without inventing defaults', async () => {
    api.mockResolvedValueOnce(settings).mockResolvedValueOnce({...settings,db_path:'/new/home.sqlite3',sleeve_allowed_folders:['/projects','/more']});
    const wrapper=mount(Settings);
    await flushPromises();
    expect(wrapper.get('#allowed-folders').element.value).toBe('');
    expect(wrapper.text()).toContain('empty list disables creation');
    expect(wrapper.text()).toContain('original database remains');
    await wrapper.get('#database-path').setValue('/new/home.sqlite3');
    await wrapper.get('#allowed-folders').setValue('/projects\n\n /more ');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(api.mock.calls[1].slice(0,2)).toEqual(['/api/settings',{db_path:'/new/home.sqlite3',sleeve_allowed_folders:['/projects','/more']}]);
    expect(wrapper.get('[role="status"]').text()).toContain('Changes are active');
    expect(wrapper.emitted('saved')).toHaveLength(1);
  });
  it('keeps failed edits and communicates save errors', async () => {
    api.mockResolvedValueOnce(settings).mockRejectedValueOnce(new Error('existing files are never overwritten'));
    const wrapper=mount(Settings);
    await flushPromises();
    await wrapper.get('#database-path').setValue('/existing.sqlite3');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain('never overwritten');
    expect(wrapper.get('#database-path').element.value).toBe('/existing.sqlite3');
    expect(wrapper.emitted('saved')).toBeUndefined();
  });
});

describe('independent Codex sleeve creation', () => {
  it('disables creation when no folders are allowed', async () => {
    api.mockResolvedValue({socket_path:'/default.sock',threads:[]});
    const wrapper=mount(SleeveDialog,{props:{...props,allowedFolders:[]}});
    await flushPromises();
    await wrapper.get('.new-sleeve').trigger('click');
    await wrapper.get('#new-sleeve-folder').setValue('/projects/test');
    expect(wrapper.text()).toContain('Add allowed working folders in Settings first');
    expect(wrapper.get('button[type="submit"]').element.disabled).toBe(true);
    await wrapper.get('form').trigger('submit');
    expect(api).toHaveBeenCalledTimes(1);
  });
  it('creates on the default daemon then preselects the exact new root without assigning silently', async () => {
    api.mockResolvedValueOnce({socket_path:'/custom.sock',threads:[]})
      .mockResolvedValueOnce({thread_id:THREAD,socket_path:'/default.sock',folder:'/projects/test'})
      .mockResolvedValueOnce({socket_path:'/default.sock',threads:[{id:THREAD,cwd:'/projects/test'}]});
    const wrapper=mount(SleeveDialog,{props:{...props,defaultSocket:'/custom.sock'}});
    await flushPromises();
    await wrapper.get('.new-sleeve').trigger('click');
    await wrapper.get('#new-sleeve-folder').setValue('/projects/test');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(api.mock.calls[1][0]).toBe('/api/sleeves');
    expect(api.mock.calls[1][1]).toEqual({folder:'/projects/test',request_id:expect.any(String)});
    expect(wrapper.get('#sleeve-options').element.value).toBe(THREAD);
    expect(wrapper.get('button[type="submit"]').element.disabled).toBe(false);
    expect(wrapper.text()).toContain('keeps running when Agent Home exits');
    expect(api).toHaveBeenCalledTimes(3);
    expect(wrapper.emitted('saved')).toBeUndefined();
  });
  it('retains the request ID after a lost creation response', async () => {
    api.mockResolvedValueOnce({socket_path:'/default.sock',threads:[]})
      .mockRejectedValueOnce(new Error('Connection lost')).mockRejectedValueOnce(new Error('Check roots before retrying'));
    const wrapper=mount(SleeveDialog,{props});
    await flushPromises();
    await wrapper.get('.new-sleeve').trigger('click');
    await wrapper.get('#new-sleeve-folder').setValue('/projects/test');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    const first=api.mock.calls[1][1].request_id;
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(api.mock.calls[2][1].request_id).toBe(first);
    expect(wrapper.text()).toContain('Check roots');
  });
});
