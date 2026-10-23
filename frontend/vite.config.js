import {fileURLToPath} from 'node:url';
import vue from '@vitejs/plugin-vue';
import {defineConfig} from 'vitest/config';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [vue()],
  build: {
    outDir: '../agent_home/static/build',
    emptyOutDir: true,
  },
  server: {
    host: '127.0.0.1',
    proxy: {'/api': 'http://127.0.0.1:8765'},
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.js'],
    clearMocks: true,
  },
});
