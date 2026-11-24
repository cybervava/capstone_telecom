#!/usr/bin/env node
// Export Figure 1 of the architecture document for the slide deck.
//
//   node presentation/src/export_architecture.mjs
//
// The diagram has one source: the inline SVG between <!--FIG1--> and <!--/FIG1--> in
// src/pdf/G3_Telco_Architecture.html. This script writes
//   src/assets/architecture.svg   standalone, every style written onto its element as attributes
//   src/assets/architecture.png   2400 px wide, on white
// Headless Chrome does both, so the picture is the one the PDF shows. Nothing is fetched.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = join(HERE, 'pdf', 'G3_Telco_Architecture.html');
const ASSETS = join(HERE, 'assets');
const PNG_WIDTH_CSS = 1200; // rendered at twice this by the device scale factor
const CANDIDATES = [
  process.env.CHROME,
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean);

const chrome = CANDIDATES.find((path) => existsSync(path));
if (!chrome) {
  console.error('Chrome was not found. Set CHROME to the path of a Chrome or Chromium binary.');
  process.exit(2);
}

const html = readFileSync(SOURCE, 'utf8');
const start = html.indexOf('<!--FIG1-->');
const end = html.indexOf('<!--/FIG1-->');
if (start < 0 || end < 0) {
  console.error('The markers <!--FIG1--> and <!--/FIG1--> were not found in the source.');
  process.exit(1);
}
const svg = html.slice(start + '<!--FIG1-->'.length, end).trim();
const viewBox = /viewBox="0 0 (\d+) (\d+)"/.exec(svg);
if (!viewBox) {
  console.error('The diagram has no viewBox.');
  process.exit(1);
}
const [width, height] = [Number(viewBox[1]), Number(viewBox[2])];
// The document's own additions to the shared diagram classes travel with the diagram.
const ownStyle = /<style>([\s\S]*?)<\/style>/.exec(html)?.[1].replace(/@page[^{]*\{[^{}]*(\{[^{}]*\}[^{}]*)*\}/g, '') ?? '';

function runChrome(args) {
  const profile = mkdtempSync(join(tmpdir(), 'g3-arch-'));
  const run = spawnSync(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', `--user-data-dir=${profile}`,
    '--virtual-time-budget=4000', ...args], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  rmSync(profile, { recursive: true, force: true });
  if (run.status !== 0) {
    console.error(`Chrome failed\n${run.stderr}`);
    process.exit(1);
  }
  return run.stdout;
}

// The wrapper pages sit beside the PDF sources, so base.css and the fonts resolve as they do there.
const page = (body, script = '') => `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="base.css"><style>${ownStyle}
html, body { margin: 0; background: #fff; } svg { display: block; }</style></head>
<body>${body}${script}</body></html>`;

mkdirSync(ASSETS, { recursive: true });

// 1. The standalone SVG: each element gets its computed paint and type as attributes.
const INLINE = ['fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin'];
const TEXT = ['font-family', 'font-size', 'font-weight', 'letter-spacing'];
const inliner = `<script>
  const svg = document.querySelector('svg');
  const fix = (el, name) => {
    const value = getComputedStyle(el).getPropertyValue(name).trim();
    if (!value || value === 'normal' || (name === 'stroke-dasharray' && value === 'none')) return;
    // Plain numbers in user units: some slide tools do not read "px" inside an attribute.
    el.setAttribute(name, value.replace(/"/g, "'").replace(/([0-9])px/g, '$1'));
  };
  document.fonts.ready.then(() => {
    for (const el of svg.querySelectorAll('*')) {
      const tag = el.tagName.toLowerCase();
      if (tag === 'text') {
        ${JSON.stringify(TEXT)}.forEach((name) => fix(el, name));
        fix(el, 'fill');
        if (getComputedStyle(el).paintOrder.startsWith('stroke')) {
          ['stroke', 'stroke-width', 'stroke-linejoin', 'paint-order'].forEach((name) => fix(el, name));
        }
      } else {
        ${JSON.stringify(INLINE)}.forEach((name) => fix(el, name));
        if (getComputedStyle(el).strokeWidth === '0px' || getComputedStyle(el).stroke === 'none') {
          el.removeAttribute('stroke-width'); el.removeAttribute('stroke-dasharray');
          el.removeAttribute('stroke-linecap'); el.removeAttribute('stroke-linejoin');
        }
      }
      el.removeAttribute('class');
      el.removeAttribute('style');
    }
    svg.removeAttribute('class'); svg.removeAttribute('id');
    svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    svg.setAttribute('width', '${width}'); svg.setAttribute('height', '${height}');
    const out = document.createElement('textarea');
    out.id = 'out';
    out.textContent = new XMLSerializer().serializeToString(svg);
    document.body.replaceChildren(out);
  });
</script>`;
const wrapper = join(HERE, 'pdf', '_export_architecture.html');
writeFileSync(wrapper, page(svg, inliner));
const dumped = runChrome(['--dump-dom', pathToFileURL(wrapper).href]);
const field = /<textarea id="out">([\s\S]*?)<\/textarea>/.exec(dumped);
if (!field) {
  rmSync(wrapper, { force: true });
  console.error('The export page did not produce the diagram.');
  process.exit(1);
}
const decode = (text) => text.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
const standalone = `<?xml version="1.0" encoding="UTF-8"?>\n<!-- Exported from presentation/src/pdf/G3_Telco_Architecture.html by export_architecture.mjs. Edit the diagram there. -->\n${decode(field[1])}\n`;
writeFileSync(join(ASSETS, 'architecture.svg'), standalone);
console.log('assets/architecture.svg');

// 2. The PNG, from the same markup with the document's fonts.
const scale = PNG_WIDTH_CSS / width;
writeFileSync(wrapper, page(svg.replace('<svg ', `<svg style="width:${PNG_WIDTH_CSS}px;height:${Math.round(height * scale)}px" `)));
runChrome(['--hide-scrollbars', '--force-device-scale-factor=2', `--window-size=${PNG_WIDTH_CSS},${Math.round(height * scale)}`,
  `--screenshot=${join(ASSETS, 'architecture.png')}`, pathToFileURL(wrapper).href]);
rmSync(wrapper, { force: true });
console.log('assets/architecture.png');
