// @vitest-environment node
import {mkdtemp, readFile, readdir, rm, stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'vite';
import {expect, it} from 'vitest';

it('builds a Pages artifact with working project-path assets and only public site files', async () => {
  const outDir = await mkdtemp(join(tmpdir(), 'agent-home-pages-'));
  try {
    await build({configFile: fileURLToPath(new URL('./vite.config.js', import.meta.url)),
      logLevel: 'silent', build: {outDir, emptyOutDir: true}});
    expect((await readdir(outDir)).sort()).toEqual(['assets', 'index.html', 'mark.svg']);
    const html = await readFile(join(outDir, 'index.html'), 'utf8');
    const assets = [...html.matchAll(/(?:src|href)="(\/[^\"]+)"/g)].map(match => match[1]);
    expect(assets.length).toBeGreaterThanOrEqual(3);
    for (const asset of assets) {
      expect(asset).toMatch(/^\/agent-home-public\//);
      expect((await stat(join(outDir, asset.slice('/agent-home-public/'.length)))).isFile()).toBe(true);
    }
  } finally {
    await rm(outDir, {recursive: true, force: true});
  }
});
