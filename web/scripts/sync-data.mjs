// Copies bundled, versioned data from ../fixtures into public/ before dev/build:
// map context layers, downloadable sample files, the guided-sample fixture and font licenses.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = join(dirname(fileURLToPath(import.meta.url)), '..');
const fixtures = join(web, '..', 'fixtures');
const pub = join(web, 'public');

function copyDir(src, dest, filter = () => true) {
  if (!existsSync(src)) return 0;
  mkdirSync(dest, { recursive: true });
  let n = 0;
  for (const f of readdirSync(src)) {
    if (!filter(f)) continue;
    copyFileSync(join(src, f), join(dest, f));
    n++;
  }
  return n;
}

const counts = {
  context: copyDir(join(fixtures, 'context'), join(pub, 'data', 'context'), (f) => f.endsWith('.geojson')),
  samples: copyDir(join(fixtures, 'golden'), join(pub, 'data', 'samples'), (f) => f.endsWith('.geojson')),
  demo: copyDir(join(fixtures, 'demo'), join(pub, 'data', 'demo'), (f) => f.endsWith('.json')),
};
mkdirSync(join(pub, 'licenses'), { recursive: true });
copyFileSync(join(web, 'node_modules/@fontsource/source-sans-3/LICENSE'), join(pub, 'licenses', 'SourceSans3-OFL.txt'));
copyFileSync(join(web, 'node_modules/@fontsource/ibm-plex-mono/LICENSE'), join(pub, 'licenses', 'IBMPlexMono-OFL.txt'));
console.log('sync-data:', JSON.stringify(counts));
