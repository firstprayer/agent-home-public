import {flushPromises, mount} from '@vue/test-utils';
import {afterEach, describe, expect, it, vi} from 'vitest';
import ResumeCommand from './components/ResumeCommand.vue';
import IdentityTable from './components/IdentityTable.vue';

const sleeve = {kind: 'codex', socket_path: '/tmp/codex.sock', cwd: '/projects/Research team',
  thread_id: '11111111-1111-4111-8111-111111111111'};
const expected = "codex resume --remote 'unix:///tmp/codex.sock' --cd '/projects/Research team' '11111111-1111-4111-8111-111111111111'";
afterEach(() => { vi.unstubAllGlobals(); });

describe('resume command', () => {
  it('copies the complete current address without changing permissions', async () => {
    const writeText = vi.fn().mockResolvedValue();
    vi.stubGlobal('navigator', {clipboard: {writeText}});
    const wrapper = mount(ResumeCommand, {props: {sleeve}});
    expect(wrapper.get('textarea').element.value).toBe(expected);
    await wrapper.get('button').trigger('click');
    await flushPromises();
    expect(writeText).toHaveBeenCalledWith(expected);
    expect(wrapper.get('[role="status"]').text()).toContain('Copied');
    expect(expected).not.toContain('--yolo');
    await wrapper.setProps({sleeve: {...sleeve, socket_path: '/tmp/replacement.sock'}});
    expect(wrapper.find('[role="status"]').exists()).toBe(false);
    expect(wrapper.get('textarea').element.value).toContain('unix:///tmp/replacement.sock');
  });

  it('quotes apostrophes and shell syntax as literal path contents', () => {
    const wrapper = mount(ResumeCommand, {props: {sleeve: {...sleeve,
      cwd: "/projects/it's $(touch nope); `echo nope`", socket_path: "/tmp/it's.sock"}}});
    const command = wrapper.get('textarea').element.value;
    expect(command).toContain("--remote 'unix:///tmp/it'\"'\"'s.sock'");
    expect(command).toContain("--cd '/projects/it'\"'\"'s $(touch nope); `echo nope`'");
  });

  it('keeps a selectable complete command when clipboard access fails', async () => {
    vi.stubGlobal('navigator', {clipboard: {writeText: vi.fn().mockRejectedValue(new Error('Denied'))}});
    const wrapper = mount(ResumeCommand, {props: {sleeve}});
    await wrapper.get('button').trigger('click');
    await flushPromises();
    expect(wrapper.get('textarea').element.value).toBe(expected);
    expect(wrapper.get('textarea').attributes()).toHaveProperty('readonly');
    expect(wrapper.get('[role="status"]').text()).toContain('Select and copy');
  });

  it('appears on assigned Codex rows and is absent for Admin or missing addresses', () => {
    const identities = [{id:'worker',name:'Research',sleeve,runtime:{status:'idle'}},
      {id:'admin',name:'Admin',sleeve:{kind:'admin_app'},runtime:{status:'ready'}},
      {id:'new',name:'New',sleeve:null,runtime:{status:'unassigned'}}];
    const wrapper = mount(IdentityTable, {props: {identities}});
    expect(wrapper.findAll('details')).toHaveLength(1);
    expect(wrapper.get('textarea').element.value).toBe(expected);
    expect(mount(ResumeCommand, {props: {sleeve:{...sleeve,cwd:null}}}).find('details').exists()).toBe(false);
  });
});
