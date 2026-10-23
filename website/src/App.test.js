import {mount} from '@vue/test-utils';
import {afterEach, describe, expect, it, vi} from 'vitest';
import App from './App.vue';

afterEach(() => { vi.unstubAllGlobals(); history.replaceState(null, '', '/'); });

describe('public project site', () => {
  it('opens a direct installation link after Vue renders its target section', () => {
    history.replaceState(null, '', '/#start');
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const wrapper = mount(App, {attachTo: document.body});
    try {
      expect(scroll).toHaveBeenCalledOnce();
      expect(scroll.mock.contexts[0].id).toBe('start');
    } finally {
      wrapper.unmount();
      delete Element.prototype.scrollIntoView;
    }
  });

  it('works without the local service and labels the workspace as an example', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const wrapper = mount(App);
    expect(wrapper.get('h1').text()).toContain('Talk to yourCodex agent,anywhere.');
    expect(wrapper.get('figcaption').text()).toContain('Example workspace');
    expect(wrapper.text()).toContain('uv run --managed-python --group macos-build python packaging/macos/build.py');
    expect(wrapper.text()).toContain('uv run python bridge.py');
    expect(fetch).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('switches media examples without making network requests or losing the demo label', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const wrapper = mount(App);
    for (const label of ['Photo', 'Voice', 'Video', 'Text']) {
      await wrapper.findAll('.demo-tabs button').find(button => button.text() === label).trigger('click');
      expect(wrapper.get('.demo-tabs [aria-pressed="true"]').text()).toBe(label);
      expect(wrapper.get('.chat-day').text()).toBe('ILLUSTRATIVE CONVERSATION');
    }
    expect(fetch).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Windows support is planned; it is not currently supported.');
    wrapper.unmount();
  });

  it('connects every local navigation link to a real section and keeps external links explicit', () => {
    const wrapper = mount(App);
    for (const link of wrapper.findAll('a')) {
      const href = link.attributes('href');
      if (href.startsWith('#')) expect(wrapper.find(href).exists(), href).toBe(true);
      else expect(href).toMatch(/^https:\/\//);
    }
    expect(wrapper.get('.nav-source').attributes('href')).toBe('https://github.com/firstprayer/agent-home-public');
    wrapper.unmount();
  });
});
