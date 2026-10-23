import {beforeEach, describe, expect, it, vi} from 'vitest';
vi.mock('vue', async original => ({...await original(), createApp: vi.fn(() => ({mount: vi.fn()}))}));
beforeEach(() => vi.resetModules());
describe('startup navigation', () => {
  it.each(['#settings', '#guide', '#external-identities'])('preserves the supported %s address on reload', async hash => {
    history.replaceState(null, '', `/${hash}`);
    await import('./main.js');
    expect(location.hash).toBe(hash);
  });
  it('removes unsupported legacy fragments before mounting', async () => {
    history.replaceState(null, '', '/#unsupported-legacy-address');
    await import('./main.js');
    expect(location.hash).toBe('');
  });
});
