#!/usr/bin/env node
// Render one or more of the PDFs from their HTML source with headless Chrome.
//
//   node presentation/src/build_pdf.mjs G3_Telco_Architecture [G3_Telco_Evaluation ...]
//   node presentation/src/build_pdf.mjs --all
//
// src/pdf/<name>.html becomes presentation/<name>.pdf. Chrome is found on its usual paths, or
// through the CHROME environment variable. Nothing is fetched from the network: the stylesheet
// and the fonts are beside the sources.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(HERE, '..');
const CANDIDATES = [
  process.env.CHROME,
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean);

const chrome = CANDIDATES.find((path) => existsSync(path));
if (!chrome) {
  console.error('Chrome was not found. Set CHROME to the path of a Chrome or Chromium binary.');
  process.exit(2);
}

let names = process.argv.slice(2);
if (names.includes('--all')) {
  names = readdirSync(join(HERE, 'pdf')).filter((f) => f.endsWith('.html') && !f.startsWith('_')).map((f) => f.slice(0, -5));
}
if (!names.length) {
  console.error('usage: build_pdf.mjs <name> [...] | --all');
  process.exit(2);
}

let failed = 0;
for (const name of names) {
  const source = join(HERE, 'pdf', `${name}.html`);
  if (!existsSync(source)) {
    console.error(`no such source: ${source}`);
    failed += 1;
    continue;
  }
  const target = join(OUT, `${name}.pdf`);
  // A profile of its own for each run, so that several builds at once do not share a lock.
  const profile = mkdtempSync(join(tmpdir(), 'g3-pdf-'));
  const run = spawnSync(chrome, [
    '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--run-all-compositor-stages-before-draw',
    `--user-data-dir=${profile}`, '--no-first-run', '--virtual-time-budget=4000',
    `--print-to-pdf=${target}`, pathToFileURL(source).href,
  ], { encoding: 'utf8' });
  rmSync(profile, { recursive: true, force: true });
  if (run.status !== 0 || !existsSync(target)) {
    console.error(`${name}: Chrome failed\n${run.stderr}`);
    failed += 1;
  } else {
    console.log(`${name}.pdf`);
  }
}
process.exit(failed ? 1 : 0);
