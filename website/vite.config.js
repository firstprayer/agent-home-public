import {fileURLToPath} from 'node:url';
import vue from '@vitejs/plugin-vue';
import {defineConfig} from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: '/agent-home-public/',
  plugins: [vue()],
  build: {
    outDir: '../build/site',
    emptyOutDir: true,
  },
  server: {host: '127.0.0.1'},
  preview: {host: '127.0.0.1', port: 4174},
  test: {environment: 'jsdom', clearMocks: true},
});
