import {mkdir, copyFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve, dirname} from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const output = resolve(root, 'dist/web');
await mkdir(output, {recursive:true});
for (const name of ['index.html','app.js','api-client.mjs','cover.mjs','history.mjs','oauth-consent.mjs','ai-openapi.json','ai-instructions.md','_headers']) {
  await copyFile(resolve(root, 'preview', name), resolve(output, name));
}
console.log('Cloudflare Pages output: dist/web (static assets only; no credentials).');
