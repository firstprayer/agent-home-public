import {expect, it, vi} from 'vitest';
import {api} from './api.js';

it('sends JSON mutations with cancellation support and reports structured errors', async () => {
  const fetch = vi.fn().mockResolvedValue({ok: false, text: async () => '{"error":"Sleeve already assigned"}'});
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  await expect(api('/api/example', {thread_id: 'exact'}, controller.signal)).rejects.toThrow('Sleeve already assigned');
  expect(fetch).toHaveBeenCalledWith('/api/example', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal, body: '{"thread_id":"exact"}',
  });
});
