// What capture_screens.mjs drives: the demo console, the customer desk and the staff desk, each
// through its own page in a headless Chrome, and the saving of what they show.
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO = resolve(HERE, '..', '..');
export const OUT = join(HERE, 'assets', 'screens');
// Customers cast by earlier runs. The platform keeps their conversations, so a retake gets new ones.
const HISTORY = join(HERE, '.capture_screens_history.json');
export const DESK = process.env.CAPTURE_DESK ?? 'http://127.0.0.1:8080/';
export const SIM = process.env.CAPTURE_SIM ?? 'http://127.0.0.1:8081';
const STAFF = { agent: 'demo.agent', supervisor: 'demo.supervisor', ops: 'demo.ops' };
// Page background kept around a close crop, in CSS pixels.
const CROP_MARGIN = 14;
// The browser window every image is taken in, unless a wider one is asked for.
export const WINDOW = { width: 1440, height: 900 };

export const say = (text) => process.stdout.write(`${text}\n`);
export const readJson = (file, fallback) => (existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : fallback);
export const istDate = (date) =>
  date.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Asia/Kolkata' });
const istTime = (date) =>
  date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' });

export function readEnv(file, wanted) {
  const found = {};
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const at = line.indexOf('=');
    const name = line.slice(0, at).trim();
    if (at > 0 && wanted.includes(name)) found[name] = line.slice(at + 1).trim().replace(/^["']|["']$/g, '');
  }
  return found;
}

// The two keys, from .env. They are typed into password fields and sent to the local stack only.
export function keys() {
  const env = readEnv(join(REPO, '.env'), ['CARE_DEV_STAFF_KEY', 'OSS_KEY_ADMIN']);
  if (!env.CARE_DEV_STAFF_KEY || !env.OSS_KEY_ADMIN) {
    throw new Error('CARE_DEV_STAFF_KEY and OSS_KEY_ADMIN must be set in .env (run `make init`)');
  }
  return { staff: env.CARE_DEV_STAFF_KEY, admin: env.OSS_KEY_ADMIN };
}

export const run = {
  keys: null,
  ctx: null,
  con: null,
  staff: {},
  history: readJson(HISTORY, []),
  shots: [],
  failed: [],
  // What this run changed on the simulator, to end it again whatever happens.
  outages: new Set(),
  lines: new Set(),
  networkDown: false,
};

export async function simApi(path, { method = 'GET', body } = {}) {
  const response = await fetch(`${SIM}${path}`, {
    method,
    headers: { 'X-API-Key': run.keys.admin, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`the simulator answered ${response.status} for ${method} ${path}`);
  return response.json();
}

// Waits until what `read` returns has stopped changing for `quietMs` (two of the page's own polls).
async function stable(page, read, { quietMs = 2800, timeout = 40000 } = {}) {
  const started = Date.now();
  let last = JSON.stringify(await read());
  let since = Date.now();
  while (Date.now() - started < timeout) {
    await page.waitForTimeout(300);
    const now = JSON.stringify(await read());
    if (now !== last) {
      last = now;
      since = Date.now();
    } else if (Date.now() - since >= quietMs) {
      break;
    }
  }
  return JSON.parse(last);
}

// --- saving an image ---------------------------------------------------------------------------

// The rectangle of the page that holds every one of the locators, with a margin of background.
async function regionOf(page, locators) {
  const boxes = [];
  for (const locator of locators) boxes.push(await locator.boundingBox());
  const size = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
  }));
  const left = Math.max(0, Math.min(...boxes.map((box) => box.x)) - CROP_MARGIN);
  const top = Math.max(0, Math.min(...boxes.map((box) => box.y)) - CROP_MARGIN);
  const right = Math.min(size.width, Math.max(...boxes.map((box) => box.x + box.width)) + CROP_MARGIN);
  const bottom = Math.min(size.height, Math.max(...boxes.map((box) => box.y + box.height)) + CROP_MARGIN);
  return { x: left, y: top, width: right - left, height: bottom - top };
}

// By how many CSS pixels the values in the customer desk's complaint-reference box run past its
// right border (0 when they fit, or on a page without one). The box does not grow with a long
// network chip, and an image that shows it says so in its notes.
const docketOverrun = (page) =>
  page.evaluate(() => {
    const docket = document.getElementById('docket');
    if (!docket || docket.hidden) return 0;
    const reach = [...docket.querySelectorAll('dd, dd *')].map((part) => part.getBoundingClientRect().right);
    return Math.max(0, Math.round(Math.max(...reach) - docket.getBoundingClientRect().right));
  });

// Saves the page as it stands: the whole page, only the window (`fullPage: false`), or the part of
// the page that holds the `crop` locators; in a window `width` wide when one is given. Records
// what it shows for the manifest.
export async function snap(page, file, meta, { crop = null, fullPage = true, width = WINDOW.width } = {}) {
  await page.bringToFront();
  if (width !== WINDOW.width) await page.setViewportSize({ ...WINDOW, width });
  await page.mouse.move(2, 2);
  await page.evaluate(() => {
    document.activeElement?.blur?.();
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(300); // the scroll and the lost focus are painted
  const target = join(OUT, file);
  const options = { path: target, animations: 'disabled' };
  if (crop) await page.screenshot({ ...options, fullPage: true, clip: await regionOf(page, crop) });
  else await page.screenshot({ ...options, fullPage });
  // A close crop of another part of the page does not show the box.
  const overrun = crop ? 0 : await docketOverrun(page);
  const cramped = overrun
    ? `The values in the complaint-reference box run ${overrun} px past its right border: a layout flaw of the page in a window this wide, not retouched.`
    : '';
  if (width !== WINDOW.width) await page.setViewportSize(WINDOW);
  const now = new Date();
  const header = readFileSync(target);
  run.shots.push({
    file,
    title: meta.title,
    scene: meta.scene,
    shows: meta.shows,
    visible_facts: meta.facts.filter(Boolean),
    customer_id: meta.customer ?? null,
    captured_at: `${now.toISOString()} (${istTime(now)} IST, ${istDate(now)})`,
    pixels: `${header.readUInt32BE(16)} x ${header.readUInt32BE(20)}`,
    notes: [meta.notes, cramped].filter(Boolean).join(' '),
  });
  say(`  saved ${file}`);
}

// --- the demo console --------------------------------------------------------------------------

export async function openConsole() {
  const con = await run.ctx.newPage();
  await con.goto(`${SIM}/console/`);
  await con.fill('#admin-key', run.keys.admin);
  await con.click('#key-form button[type="submit"]');
  await con.waitForFunction(() => document.getElementById('connection').dataset.tone === 'good');
  await waitCast(con, 1);
  return con;
}

async function waitCast(con, sceneNo) {
  await con.waitForFunction(
    (n) => {
      const kicker = document.querySelector('.script__kicker')?.textContent ?? '';
      if (!kicker.startsWith(`Scene ${n} `)) return false;
      const notice = document.querySelector('.script .notice')?.textContent ?? '';
      return notice ? !notice.startsWith('Finding') : Boolean(document.querySelector('.cast__facts'));
    },
    sceneNo,
    { timeout: 30000 },
  );
  if (!(await con.locator('.cast__facts').count())) {
    throw new Error(`the console cast nobody: ${await con.locator('.script .notice').textContent()}`);
  }
}

const castOf = (con) =>
  con.evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll('.cast__facts > div')].map((row) => [
        row.querySelector('dt').textContent,
        row.querySelector('dd').textContent,
      ]),
    ),
  );

const castChanged = (con, was) =>
  con.waitForFunction(
    (before) => {
      const now = document.querySelector('.cast__facts dd')?.textContent;
      return Boolean(now) && now !== before;
    },
    was,
    { timeout: 30000 },
  );

// The customer the console cast, or the next one ("Another customer") until it is one no earlier
// run used and, when a plan is named, one on that plan.
async function freshCast(con, plan) {
  for (let tries = 0; tries < 25; tries += 1) {
    const cast = await castOf(con);
    if (!run.history.includes(cast.Customer) && (!plan || cast.Plan === plan)) {
      run.history.push(cast.Customer);
      writeFileSync(HISTORY, `${JSON.stringify(run.history, null, 2)}\n`);
      return cast;
    }
    await con.locator('.cast__controls button', { hasText: 'Another customer' }).click();
    await castChanged(con, cast.Customer);
  }
  throw new Error('no fresh customer after 25 tries');
}

// Chooses a scene in the console's list (counted from 1) and leaves its customer as cast.
export async function selectScene(sceneNo) {
  await run.con.bringToFront();
  await run.con.locator('#scene-list .scene-list__item').nth(sceneNo - 1).click();
  await waitCast(run.con, sceneNo);
}

// Chooses a scene to play it: with a customer no earlier run used, on `plan` when one is named,
// and "again" with another customer when this run has played the scene already.
export async function openScene(sceneNo, { plan, again = false } = {}) {
  const con = run.con;
  await selectScene(sceneNo);
  if (again && (await con.locator('ol.cues > li.cue[data-state="done"]').count())) {
    const before = (await castOf(con)).Customer;
    await con.locator('.script__footer button', { hasText: 'Play it again' }).click();
    await castChanged(con, before);
  }
  const cast = await freshCast(con, plan);
  say(`  cast ${cast.Customer} on ${cast.Node} (${cast.Plan}, notices ${cast['Outage notices']})`);
  return cast;
}

// Presses Go on a cue (counted from 1) and returns what the console says it did.
export async function go(cueNo) {
  const con = run.con;
  await con.bringToFront();
  const cue = con.locator('ol.cues > li.cue').nth(cueNo - 1);
  await cue.locator('button.go').click();
  await con.waitForFunction(
    (index) => ['done', 'failed'].includes(document.querySelectorAll('ol.cues > li.cue')[index]?.dataset.state),
    cueNo - 1,
    { timeout: 30000 },
  );
  const result = await cue.locator('.cue__result').textContent();
  if ((await cue.getAttribute('data-state')) !== 'done') throw new Error(`cue ${cueNo}: ${result}`);
  const outage = result.match(/OUT-\d{8}-\d{4}/)?.[0];
  if (outage && result.includes('declared')) run.outages.add(outage);
  if (outage && result.includes('resolved')) run.outages.delete(outage);
  say(`  console: ${result}`);
  return result;
}

// What the customer says in the scene on show, as the console's cues give it.
export const linesOf = () => run.con.locator('ol.cues > li.cue[data-kind="say"] q').allTextContents();

// Sets how long ago the credit scene's outage began, as a presenter would in the scene's option.
export async function setMinutesAgo(minutes) {
  const input = run.con.locator('#minutes-ago');
  if ((await input.inputValue()) !== String(minutes)) {
    await input.fill(String(minutes));
    await input.press('Tab');
  }
  await run.con.waitForFunction((value) => document.getElementById('minutes-ago')?.value === value, String(minutes));
}

// Reads the board ("On the enterprise systems") once the scene's evidence holds every wanted text
// and has stopped changing, and returns what of it shows in the window. Choosing the scene again
// makes the console read the simulator at once instead of at its next poll.
export async function board(sceneNo, wanted, timeout = 45000) {
  const con = run.con;
  await con.bringToFront();
  const deadline = Date.now() + timeout;
  let before = '';
  for (;;) {
    await con.locator('#scene-list .scene-list__item').nth(sceneNo - 1).click();
    await con.waitForTimeout(900);
    const text = await con.locator('#evidence').innerText();
    if (wanted.every((part) => text.includes(part)) && text === before) return boardFacts(true);
    if (Date.now() > deadline) throw new Error(`the console's board never showed "${wanted.join('", "')}": ${text}`);
    before = text;
    await con.waitForTimeout(1600);
  }
}

// The texts of the elements a selector finds; with `windowOnly`, of those that lie wholly inside
// the browser window once the page is scrolled to its top (what an image of the window shows).
export const texts = (page, selector, windowOnly = false) =>
  page.evaluate(
    ([found, only]) => {
      if (only) window.scrollTo(0, 0);
      const shown = (box) => !only || (box.top >= 0 && box.bottom <= window.innerHeight);
      return [...document.querySelectorAll(found)]
        .filter((part) => shown(part.getBoundingClientRect()))
        .map((part) => part.textContent.replace(/\s+/g, ' ').trim());
    },
    [selector, windowOnly],
  );

// What the console says it did (its cues' results), what the board reads back from the simulator
// and what is in force; with `windowOnly`, only what lies inside the window, as for `texts`.
export const boardFacts = (windowOnly = false) =>
  run.con.evaluate((only) => {
    if (only) window.scrollTo(0, 0);
    const clean = (text) => text.replace(/\s+/g, ' ').trim();
    const shown = (part) => {
      const box = part.getBoundingClientRect();
      return !only || (box.top >= 0 && box.bottom <= window.innerHeight);
    };
    const proofs = [...document.querySelectorAll('#evidence .proof')].flatMap((proof) => {
      const title = clean(proof.querySelector('h3').textContent);
      const pairs = [...proof.querySelectorAll('.fact')]
        .filter(shown)
        .map((fact) => `${title}: ${clean(fact.querySelector('dt').textContent)}: ${clean(fact.querySelector('dd').textContent)}`);
      const lines = [...proof.querySelectorAll('.quiet-note, .sample, .hint')].filter(shown).map((line) => `${title}: ${clean(line.textContent)}`);
      return [...pairs, ...lines];
    });
    const world = [...document.querySelectorAll('#world .world__group')].flatMap((group) => {
      const rows = [...group.querySelectorAll('li, .quiet-note')]
        .filter(shown)
        .map((row) => clean([...row.querySelectorAll('span')].map((part) => part.textContent).join(' · ') || row.textContent));
      return rows.length ? [`In force now, ${clean(group.querySelector('h3').textContent)}: ${rows.join('; ')}`] : [];
    });
    const cues = [...document.querySelectorAll('ol.cues > li.cue .cue__result')].filter(shown).map((line) => `Console: ${clean(line.textContent)}`);
    return [...cues, ...proofs, ...world];
  }, windowOnly);

export const castFacts = (cast) => Object.entries(cast).map(([term, value]) => `${term}: ${value}`);

// --- the customer desk -------------------------------------------------------------------------

export async function openDesk(customerId) {
  const desk = await run.ctx.newPage();
  await desk.goto(`${DESK}?customer=${customerId}`);
  await desk.waitForFunction(() => document.getElementById('persona-status').textContent.startsWith('Signed in as'));
  return desk;
}

export async function send(desk, text) {
  await desk.bringToFront();
  await desk.fill('#message', text);
  await desk.click('#send');
  say(`  customer: ${text}`);
}

export const deskState = (desk) =>
  desk.evaluate(() => {
    const text = (id) => document.getElementById(id)?.textContent.trim() ?? '';
    return {
      session: text('persona-status'),
      ref: text('docket-ref'),
      issuedIn: text('docket-latency'),
      status: text('docket-status'),
      network: [...document.getElementById('docket-network').children].map((part) => part.textContent.trim()),
      messages: [...document.querySelectorAll('#thread li')].map((item) => ({
        outbound: item.dataset.direction === 'outbound',
        who: item.querySelector('.who').textContent,
        body: item.querySelector('p').textContent,
      })),
      trail: [...document.querySelectorAll('#trail li')].map(
        (item) => `${item.querySelector('.step').textContent} | ${item.querySelector('.meta').textContent}`,
      ),
    };
  });

// The assistant's messages in a conversation, in order.
export const said = (state) => state.messages.filter((message) => message.outbound).map((message) => message.body);

const settled = (desk) =>
  stable(desk, async () => {
    const now = await deskState(desk);
    return { ...now, trail: now.trail.length };
  });

// Waits for the assistant's `count`-th message, which must contain every phrase, and for the
// docket to stop changing. Fails with what was said instead when a phrase is missing.
export async function reply(desk, count, phrases = [], timeout = 60000) {
  await desk.waitForFunction(
    (n) => document.querySelectorAll('#thread li[data-direction="outbound"]').length >= n,
    count,
    { timeout },
  );
  await settled(desk);
  const state = await deskState(desk);
  const body = said(state)[count - 1];
  say(`  assistant: ${body.replaceAll('\n', ' / ')}`);
  const missing = phrases.filter((phrase) => !body.includes(phrase));
  if (missing.length) throw new Error(`reply ${count} lacks "${missing.join('", "')}". It said: ${body}`);
  return state;
}

// Waits for a message of the assistant's that contains the phrase, whichever it is.
export async function replyWith(desk, phrase, timeout = 60000) {
  await desk.waitForFunction(
    (wanted) => [...document.querySelectorAll('#thread li[data-direction="outbound"] p')].some((p) => p.textContent.includes(wanted)),
    phrase,
    { timeout },
  );
  await settled(desk);
  const state = await deskState(desk);
  say(`  assistant: ${said(state).at(-1).replaceAll('\n', ' / ')}`);
  return state;
}

export function deskFacts(state, { trail = false } = {}) {
  return [
    state.session,
    `Complaint reference: ${state.ref}`,
    `Issued in: ${state.issuedIn}`,
    `Status: ${state.status}`,
    `Network: ${state.network.join(' · ')}`,
    ...state.messages.map((message) => `${message.outbound ? message.who : 'You'}: "${message.body}"`),
    ...(trail ? state.trail.map((step) => `Audit trail: ${step}`) : []),
  ];
}

// "Open as staff" on the customer desk: the audit trail of this conversation.
export async function openTrail(desk, wanted = '') {
  await desk.bringToFront();
  await desk.click('#trail-refresh');
  // The first click asks for the staff key; once the trail is open the same button refreshes it.
  if (await desk.locator('#trail-form').isVisible()) {
    await desk.fill('#trail-key', run.keys.staff);
    await desk.click('#trail-form button[type="submit"]');
  }
  await desk.waitForFunction(
    (step) => {
      const trail = document.getElementById('trail');
      return document.getElementById('trail-form').hidden && trail.children.length > 0 && trail.textContent.includes(step);
    },
    wanted,
    { timeout: 20000 },
  );
  await settled(desk);
  return deskState(desk);
}

// --- the staff desk ----------------------------------------------------------------------------

export async function staffDesk(role) {
  if (run.staff[role]) return run.staff[role];
  const page = await run.ctx.newPage();
  await page.goto(new URL('staff.html', DESK).href);
  await page.selectOption('#role', role);
  await page.fill('#name', STAFF[role]);
  await page.fill('#staff-key', run.keys.staff);
  await page.click('#sign-form button[type="submit"]');
  await page.waitForFunction(() => document.getElementById('sign-status').textContent.startsWith('Signed in as'));
  run.staff[role] = page;
  return page;
}

// Waits for the desk's next refresh (it re-reads its lists every three seconds).
export async function refreshed(page) {
  const before = await page.textContent('#sign-status');
  await page.waitForFunction((was) => document.getElementById('sign-status').textContent !== was, before, { timeout: 15000 });
}

export const staffFacts = (page) =>
  page.evaluate(() => {
    const clean = (text) => text.replace(/\s+/g, ' ').trim();
    const text = (id) => clean(document.getElementById(id).textContent);
    const rows = (root) =>
      [...root.querySelectorAll('dl > div')].map((row) => `${clean(row.querySelector('dt').textContent)} ${clean(row.querySelector('dd').textContent)}`);
    const slips = [...document.querySelectorAll('#approvals .slip')].map((slip) => {
      const shown = rows(slip).slice(0, slip.querySelector('details').open ? undefined : 5);
      return `Credit slip: ${clean(slip.querySelector('.slip__amount').textContent)} · ${clean(slip.querySelector('.chip').textContent)} · ${shown.join(' · ')}`;
    });
    const queue = [...document.querySelectorAll('#queue .queue__row')].map(
      (row) => `With a person: ${clean([...row.children].map((part) => part.textContent).join(' · '))}`,
    );
    const ops = [...document.querySelectorAll('#ops .slip')].map(
      (slip) => `Operations item: ${clean(slip.querySelector('.slip__kind').textContent)} · ${clean(slip.querySelector('.summary').textContent)} · ${rows(slip).join(' · ')}`,
    );
    const messages = [...document.querySelectorAll('#case-thread li')].map(
      (item) => `Case conversation, ${item.querySelector('.who').textContent}: "${item.querySelector('p').textContent}"`,
    );
    const trail = [...document.querySelectorAll('#case-trail li')].map(
      (item) => `Case audit trail: ${item.querySelector('.step').textContent} | ${item.querySelector('.meta').textContent}`,
    );
    const opened = document.getElementById('case').hidden
      ? []
      : [text('case-heading'), `Case reference: ${text('case-ref')}`, `Case summary: ${text('case-summary')}`, ...messages, ...trail];
    return [
      text('sign-status').replace(/ Updated .*$/, ''),
      `Credits awaiting a decision: ${text('approvals-count')}`,
      ...slips,
      `With a person: ${text('queue-count')}`,
      ...queue,
      `Operations: ${text('ops-count')}`,
      text('ops-load'),
      ...ops,
      ...opened,
    ];
  });
