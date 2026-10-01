#!/usr/bin/env node
// Copies the shared client files from _shared/ into every example that uses
// them, so each example folder works on its own when copied out of the repo.
//
//   node scripts/sync-shared.mjs          write the copies
//   node scripts/sync-shared.mjs --check  exit 1 if any copy is out of date
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const check = process.argv.includes('--check');

// example folder -> shared files it carries
const copies = {
  'live-printer-dashboard': ['typescript/printago.ts', 'typescript/realtime.ts', 'typescript/tsconfig.json'],
  'camera-snapshot-archiver': ['typescript/printago.ts', 'typescript/realtime.ts', 'typescript/tsconfig.json'],
  'parts-catalog-kiosk': ['typescript/printago.ts', 'typescript/tsconfig.json'],
  'order-intake-webhook': ['typescript/printago.ts', 'typescript/tsconfig.json'],
  'upload-and-print': ['python/printago.py'],
  'csv-batch-print': ['python/printago.py'],
  'queue-control': ['python/printago.py'],
  'production-report': ['python/printago.py'],
  'prometheus-exporter': ['python/printago.py'],
};

let stale = 0;
for (const [example, files] of Object.entries(copies)) {
  for (const file of files) {
    const source = readFileSync(join(root, '_shared', file), 'utf8');
    const name = file.split('/').pop();
    const target = join(root, example, name.endsWith('.ts') ? `src/${name}` : name);
    const current = existsSync(target) ? readFileSync(target, 'utf8') : null;
    if (current === source) continue;
    if (check) {
      console.error(`out of date: ${example}/${target.slice(join(root, example).length + 1)}`);
      stale++;
    } else {
      writeFileSync(target, source);
      console.log(`wrote ${target.slice(root.length + 1)}`);
    }
  }
}
if (check && stale) {
  console.error('Run: node scripts/sync-shared.mjs');
  process.exit(1);
}
