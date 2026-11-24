#!/usr/bin/env node
// Real screenshots of the running LOCAL demonstration, for the deck and the other documents.
//
//   node presentation/src/capture_screens.mjs                 every scene, in order
//   node presentation/src/capture_screens.mjs visit guide     only these (to retake)
//
// Scenes: happy handover approval smallcredit guardrail visit guide curveball ops freeplay
//
// It plays the scenes of the demo console (the simulator's own page) in a headless Chrome, types
// what the customer says into the customer desk, acts on the staff desk, and saves what the pages
// show. Nothing is mocked, edited or composited: every image is the product's own page in a state
// the product reached. It needs the stack already running with the demo timings (`make demo`) and
// never starts, stops, seeds or resets it. What it changes on the simulator (an outage, a failing
// line, the Network Monitoring API) it ends again.
//
// The two keys are read from .env and typed into password fields. They are never printed, never
// written to a file and never in an image.
//
// Output: assets/screens/NN-name.png and assets/screens/manifest.json (what each image shows, with
// the figures and phrases read from the page at the moment it was taken). The pages are driven
// through capture_screens_lib.mjs, beside this file.
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import {
  DESK, OUT, REPO, SIM, WINDOW, board, boardFacts, castFacts, deskFacts, deskState, go, istDate, keys, linesOf,
  openConsole, openDesk, openScene, openTrail, readEnv, readJson, refreshed, reply, replyWith, run, said, say,
  selectScene, send, setMinutesAgo, simApi, snap, staffDesk, staffFacts, texts,
} from './capture_screens_lib.mjs';

const MANIFEST = join(OUT, 'manifest.json');
// playwright-core is not a dependency of this repository. Install it where Node finds it from this
// folder, or set PLAYWRIGHT_CORE to its folder.
const PLAYWRIGHT = process.env.PLAYWRIGHT_CORE ?? 'playwright-core';
const CHROME = [
  process.env.CHROME,
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
].find((path) => path && existsSync(path));
// The documented supervisor example: INR 4,999 a month, out for 50 hours, INR 520.73.
const SUPERVISOR_PLAN = 'INR 4999.00 a month';
// A plan on which a five-hour outage earns more than the policy's minimum, so the formula shows.
const SMALL_CREDIT_PLAN = 'INR 9999.00 a month';
// The credit scene's line says "two days". For its five-hour variant the customer says this instead.
const FIVE_HOUR_LINE = 'Our office internet has been down for five hours, this is hurting our business';
const CLOSING_NOTE =
  'We confirmed the outage on your node with our network team. A technician visit would not ' +
  'restore your service sooner, so none is booked. You will be told here when service is back.';
const ALL = ['happy', 'handover', 'approval', 'smallcredit', 'guardrail', 'visit', 'guide', 'curveball', 'ops', 'freeplay'];
const LOCAL = new Set(['127.0.0.1', 'localhost']);
// A window wide enough for the customer desk's complaint-reference box to hold a long network chip.
const WIDE = 1600;
const OUTAGE_ID = /OUT-\d{8}-\d{4}/;
const AMOUNT = /INR [\d,]+\.\d{2}/;
// A full run takes about four minutes. One far longer was interrupted (a laptop that slept).
const LONGEST_RUN_MINUTES = 15;
const STAFF_NAMES = 'The staff names (demo.agent, demo.supervisor, demo.ops) are demo personas typed by the capture script.';

// --- scene 1: the happy path -------------------------------------------------------------------

async function happy() {
  const cast = await openScene(1);
  const play = { cast, customer: cast.Customer, scene: 'Scene 1, happy path: a known outage', lines: await linesOf() };
  await happyConsole(play);
  await happyDesk(play);
  return play;
}

async function happyConsole(play) {
  const { con } = run;
  const meta = { scene: play.scene, customer: play.customer };
  const listed = (titles) => titles.map((title, index) => `Scene list ${index + 1}: ${title}`);
  const start = [
    await con.textContent('#connection'),
    `${await con.textContent('.script__kicker')}: ${await con.textContent('.script__title')}`,
    ...castFacts(play.cast),
  ];
  await snap(con, '01-console-happy-path-start.png', {
    ...meta,
    title: 'The demo console, unlocked, at the start of the happy path',
    shows: 'The demo console after the admin key was accepted: the seven scenes and free play on the left, the happy-path scene with the customer it cast and its first cue in the middle, and on the right what the simulated enterprise systems hold (nothing yet) and what is in force (nothing).',
    facts: [...start, ...listed(await texts(con, '#scene-list .scene-list__title', true)), ...(await boardFacts(true))],
    notes: 'The window only (the top 900 px of the page). 01b is the whole page with all four cues. The admin key field is hidden once the key is accepted.',
  }, { fullPage: false });
  await snap(con, '01b-console-happy-path-start-full.png', {
    ...meta,
    title: 'The demo console, whole page, at the start of the happy path',
    shows: 'The same state as image 01, the whole page: all four cues of the happy path, each with what the presenter should expect, in the platform\'s own words.',
    facts: [
      ...start,
      ...listed(await texts(con, '#scene-list .scene-list__title')),
      ...(await texts(con, 'ol.cues .cue__label')).map((label, index) => `Cue ${index + 1}: ${label.replace(/^The customer says/, 'The customer says: ')}`),
      ...(await boardFacts()),
    ],
  });
  play.outageId = (await go(1)).match(OUTAGE_ID)[0];
  const inWindow = [...castFacts(play.cast), ...(await board(1, ['Outage notices', 'Said no to notices']))];
  await snap(con, '03-console-proactive-notices.png', {
    ...meta,
    facts: inWindow,
    title: 'Neighbours on the node were told before they called, and only those who agreed',
    shows: `The console after the fibre cut was declared: outage ${play.outageId} is in force, and the board on the right shows how many outage notices the platform sent to customers on the node, that none went to a customer who had declined notices, and a sample of the text.`,
    notes: 'The figures are read back from the simulator by the console. "Said no to notices: N on the node · 0 sent one" is the consent check. The window only; 03b is the board alone.',
  }, { fullPage: false });
  await snap(con, '03b-console-proactive-notices-board.png', {
    ...meta,
    facts: (await boardFacts()).filter((fact) => !fact.startsWith('Console:')),
    title: 'Close crop: the notices the platform sent, read back from the simulator',
    shows: 'The right-hand board of image 03 alone: the notices sent for the outage, the consent check, a sample notice with its channel and time, the node\'s incidents, and the outage in force.',
    notes: 'A crop of the page, not a separate state.',
  }, { crop: [con.locator('aside.board')] });
}

async function happyDesk(play) {
  const meta = { scene: play.scene, customer: play.customer };
  play.desk = await openDesk(play.customer);
  await send(play.desk, play.lines[0]);
  let state = await reply(play.desk, 1, [play.outageId, 'expected back by']);
  await snap(play.desk, '02-happy-path-outage-reply.png', {
    ...meta,
    title: 'Happy path: a reference at once, and a reply that names the outage and its estimate',
    shows: 'The customer desk after the customer\'s first message during a declared outage: the complaint reference with the time it took to issue, the network chip showing the confirmed outage and its number, and the assistant\'s reply naming the outage, its cause and the estimate with its source. It says no restart and no technician visit are needed.',
    facts: deskFacts(state),
    notes: `The audit trail panel on the right is a staff view and was not opened yet. "Issued in" is measured by the page, from Send to the reference. 02w is the same state in a wider window.`,
  });
  await snap(play.desk, '02w-happy-path-outage-reply-wide.png', {
    ...meta,
    title: 'Happy path, in a wider window: the same reply, with the complaint-reference box uncramped',
    shows: `The same state as image 02, moments later, in a ${WIDE} px wide window: the complaint reference with the time it took to issue, the confirmed outage with its number, and the assistant's reply naming the outage, its cause and the estimate with its source.`,
    facts: deskFacts(await deskState(play.desk)),
    notes: `Taken ${WIDE} px wide instead of 1440 (so the image is ${WIDE * 1.5} px wide), because at 1440 the long "outage confirmed" chip pushes the values in the complaint-reference box past its border (see the notes of image 02). Nothing else differs.`,
  }, { width: WIDE });
  state = await openTrail(play.desk);
  await snap(play.desk, '02b-happy-path-with-audit-trail.png', {
    ...meta,
    title: 'Happy path: the same conversation with its audit trail opened as staff',
    shows: 'The customer desk of image 02 after "Open as staff": the audit trail of this contact beside the conversation, each step with its outcome, who did it, how long it took and the versions it ran under.',
    facts: deskFacts(state, { trail: true }),
    notes: 'Each entry reads "step → outcome", then the actor, the duration in ms where the step is timed, and the versions (rules or model, prompt). The staff key field is hidden once the key is accepted.',
  });
}

async function happyEnd(play) {
  const meta = { scene: play.scene, customer: play.customer };
  await selectScene(1);
  await go(3);
  await reply(play.desk, 2, ['resolved the outage']);
  await send(play.desk, play.lines[1]);
  await reply(play.desk, 3);
  await play.desk.waitForFunction(() => document.getElementById('docket-status').textContent === 'CLOSED', null, { timeout: 30000 });
  const state = await openTrail(play.desk);
  await snap(play.desk, '04b-happy-path-restored-and-closed.png', {
    ...meta,
    title: 'Happy path: told when the outage ends, and the contact closes on the customer\'s word',
    shows: `The whole happy-path conversation after outage ${play.outageId} was resolved: the restoration message sent by itself, the customer's confirmation, and the closing reply. The status is CLOSED and the audit trail lists every step.`,
    facts: deskFacts(state, { trail: true }),
    notes: 'This outage lasted minutes, so no service credit is due: under the stand-in policy a broadband outage shorter than 240 minutes earns nothing. Image 04 shows an outage long enough to earn one.',
  });
  await snap(run.con, '03c-console-restored-notices.png', {
    ...meta,
    title: 'After the outage: everyone who was told is told it is over',
    shows: 'The console after the outage was resolved and the customer confirmed: the restored notices sent, one incident on the node, now resolved, with the complaints linked to it, and no technician visit.',
    facts: [...castFacts(play.cast), ...(await board(1, ['Restored notices', 'resolved']))],
    notes: 'The window only.',
  }, { fullPage: false });
}

// --- scene 2: the customer insists, a person takes over ----------------------------------------

async function handover() {
  const cast = await openScene(2);
  const lines = await linesOf();
  const first = run.con.locator('ol.cues > li.cue').first();
  if ((await first.getAttribute('data-state')) !== 'done') await go(1);
  const desk = await openDesk(cast.Customer);
  await send(desk, lines[0]);
  await reply(desk, 1, ['expected back by']);
  await send(desk, lines[1]);
  await reply(desk, 2, ['I am not arranging any of them now']);
  await send(desk, lines[2]);
  const state = await reply(desk, 3, ['A member of our team will take over']);
  const play = {
    cast, desk, ref: state.ref, customer: cast.Customer,
    scene: 'Scene 2, exception path: the customer insists it is their router',
    outage: `The console's first cue: ${await first.locator('.cue__result').textContent()}.`,
  };
  await handoverCase(play);
  await handoverClosed(play);
}

async function handoverCase(play) {
  const agent = await staffDesk('agent');
  await agent.bringToFront();
  const row = agent.locator('#queue .queue__row', { hasText: play.ref });
  await row.waitFor({ timeout: 20000 });
  await row.click();
  await agent.waitForFunction(() => document.querySelectorAll('#case-thread li').length >= 6 && document.querySelectorAll('#case-trail li').length > 0);
  await refreshed(agent);
  const meta = { scene: play.scene, customer: play.customer, facts: await staffFacts(agent) };
  const context = `${play.outage} The customer asked twice for a technician during a confirmed outage: the assistant declined on the evidence the first time and handed over the second. ${STAFF_NAMES}`;
  await snap(agent, '11a-staff-desk-handover-case.png', {
    ...meta,
    title: 'The staff desk: the queue and the handed-over case, whole page',
    shows: 'The staff desk signed in as an agent, the whole page. "With a person" lists the case the assistant handed over, with its priority and reason; the opened case, in the left column, shows the summary written for the person, the whole conversation, the audit trail and the "Mark done" box.',
    notes: `A very tall image (the case opens in the left column): use 11a2 and 11a3 on a slide. ${context} A credit slip or an operations item listed here was on the stack before this scene and is not part of it.`,
  });
  const firstSaid = meta.facts.find((fact) => fact.startsWith('Case conversation'));
  await snap(agent, '11a2-staff-desk-queue-and-case-summary.png', {
    ...meta,
    title: 'The staff desk: the handed-over case in the queue, opened, with the summary for the person',
    shows: 'The top of image 11a: who is at the desk, the three lists (the case is the row under "With a person"), and the opened case with its reason, complaint reference, the summary the assistant wrote for the person, and the start of the conversation.',
    facts: meta.facts.filter((fact) => !fact.startsWith('Case audit trail') && (!fact.startsWith('Case conversation') || fact === firstSaid)),
    notes: `A crop of the page, not a separate state. ${context} A credit slip or an operations item listed here was on the stack before this scene and is not part of it.`,
  }, { crop: [agent.locator('.masthead'), agent.locator('.panel--ops'), agent.locator('#case-thread li').first()] });
  await snap(agent, '11a3-staff-desk-case-audit-trail.png', {
    ...meta,
    title: 'Close crop: the audit trail of the handed-over case, ending in the handoff',
    shows: 'The foot of the opened case: its audit trail, every step with outcome, actor, duration and versions, ending with the handoff and its reason, and the box where staff write a note to the customer and press "Mark done".',
    facts: meta.facts.filter((fact) => fact.startsWith('Case audit trail')),
    notes: 'A crop of the page, not a separate state.',
  }, { crop: [agent.locator('#case h3', { hasText: 'Audit trail' }), agent.locator('#case-trail'), agent.locator('#done-form')] });
}

async function handoverClosed(play) {
  const agent = await staffDesk('agent');
  await agent.bringToFront();
  await agent.fill('#done-note', CLOSING_NOTE);
  await agent.click('#done');
  await agent.waitForFunction(() => document.getElementById('case').hidden, null, { timeout: 20000 });
  const state = await reply(play.desk, 4, ['Their note to you']);
  await snap(play.desk, '11b-customer-told-case-closed.png', {
    scene: play.scene,
    customer: play.customer,
    title: 'After "Mark done": the customer is told a person dealt with the case, the note quoted',
    shows: 'The customer desk of the handed-over conversation after the agent pressed "Mark done" with a note: the last message, from STAFF, tells the customer that a member of the team has dealt with the complaint and quotes the note as the staff member wrote it.',
    facts: deskFacts(state),
    notes: `The note was typed on the staff desk by the capture script; the platform sends it to the customer as written. ${play.outage}`,
  });
  await snap(run.con, '11c-console-one-incident-per-node.png', {
    scene: play.scene,
    customer: play.customer,
    title: 'One incident for the node, however many customers call',
    shows: 'The console on the exception-path scene: the incident on the node with the complaints linked to it, no technician visit on the line and no credit on the account.',
    facts: [...castFacts(play.cast), ...(await board(2, ['complaint']))],
    notes: `The window only. ${play.outage}`,
  }, { fullPage: false });
}

// --- scene 6: credits ----------------------------------------------------------------------------

// The credit scene up to the restoration message. The outage is resolved as soon as the customer
// has been answered: the policy prices every started hour, so a slow presenter adds an hour.
async function playCredit(minutes, plan, line) {
  const cast = await openScene(6, { plan, again: true });
  await setMinutesAgo(minutes);
  const lines = await linesOf();
  const desk = await openDesk(cast.Customer);
  const outageId = (await go(1)).match(OUTAGE_ID)[0];
  await send(desk, line ?? lines[0]);
  await desk.waitForFunction(() => document.querySelectorAll('#thread li[data-direction="outbound"]').length >= 1, null, { timeout: 60000 });
  await go(3);
  const state = await reply(desk, 2, ['resolved the outage', 'service credit of INR']);
  const restored = said(state)[1];
  return { cast, desk, outageId, state, restored, ref: state.ref, customer: cast.Customer, amount: restored.match(AMOUNT)[0] };
}

async function approval() {
  const play = await playCredit(3000, SUPERVISOR_PLAN);
  play.scene = 'Scene 6, human approval: a credit above INR 500 waits for a supervisor';
  if (!play.restored.includes('sent for approval')) throw new Error(`the credit was not sent for approval: ${play.restored}`);
  await snap(play.desk, '05a-customer-credit-sent-for-approval.png', {
    scene: play.scene,
    customer: play.customer,
    title: `Human approval: the customer is told a credit of ${play.amount} has been sent for approval`,
    shows: `The customer desk of a small-business customer after an outage that began 3,000 minutes (50 hours) earlier was resolved: the restoration message states the credit of ${play.amount} under the policy version and that it has been sent for approval. Nothing is applied yet.`,
    facts: deskFacts(play.state),
    notes: `Outage ${play.outageId} was declared backdated by 3,000 minutes on the console and resolved within the same minute, so 50 started hours are priced.`,
  });
  await approvalDesks(play);
  await approvalApplied(play);
}

async function approvalDesks(play) {
  const meta = { scene: play.scene, customer: play.customer };
  const supervisor = await staffDesk('supervisor');
  play.slip = supervisor.locator('#approvals .slip', { hasText: play.ref });
  await play.slip.waitFor({ timeout: 30000 });
  const agent = await staffDesk('agent');
  await refreshed(agent);
  await refreshed(agent);
  const agentFacts = await staffFacts(agent);
  if (agentFacts.some((fact) => fact.includes(play.ref))) throw new Error('the agent desk lists the supervisor credit');
  const others = agentFacts.some((fact) => fact.startsWith('Credit slip'))
    ? ' The slip marked "needs an agent" is not part of this scene: it is an older request that was already on this stack (a credit of that size waits for an agent only while every credit is held for review, which this stack no longer does).'
    : '';
  await snap(agent, '05b-agent-desk-credit-not-offered.png', {
    ...meta,
    title: `Signed in as an agent: the ${play.amount} credit is not offered`,
    shows: `The staff desk signed in as an agent while the ${play.amount} request for ${play.ref} waits: it is not in the agent's list of credits awaiting a decision. An agent is offered only what an agent may decide.`,
    facts: agentFacts,
    notes: `Taken after the request was on the supervisor's desk and after two further refreshes of this one.${others} ${STAFF_NAMES}`,
  });
  await supervisor.bringToFront();
  await play.slip.locator('details.working > summary').click();
  await play.slip.locator('details.working[open] dl').waitFor();
  const facts = await staffFacts(supervisor);
  await snap(supervisor, '05-supervisor-credit-slip.png', {
    ...meta,
    title: `Signed in as a supervisor: the ${play.amount} credit slip with how it was worked out`,
    shows: `The staff desk signed in as a supervisor: under "Credits awaiting a decision" the slip for ${play.amount} marked "needs a supervisor", with the complaint, outage, service and policy version, and "How it was worked out" opened: monthly fee, outage minutes, billable hours, multiplier and the result of the formula.`,
    facts,
    notes: `The supervisor decides on the request exactly as shown: it is frozen, and if it changes the decision is refused.${others} ${STAFF_NAMES}`,
  });
  await snap(supervisor, '05c-credit-slip-worked-out-crop.png', {
    ...meta,
    title: `Close crop: the ${play.amount} credit slip, "How it was worked out" opened`,
    shows: 'The credit slip alone: the amount, who must decide, the frozen request (complaint, outage, service, policy version, when it lapses), the working from the policy, and the note field with the Approve and Reject buttons.',
    facts: facts.filter((fact) => fact.includes(play.ref)),
    notes: 'A crop of image 05, not a separate state. The working is done in code from the policy file: monthly fee x billable hours x multiplier / 720.',
  }, { crop: [play.slip] });
}

async function approvalApplied(play) {
  const meta = { scene: play.scene, customer: play.customer };
  await play.slip.locator('button', { hasText: 'Approve' }).click();
  await play.slip.waitFor({ state: 'detached', timeout: 20000 });
  const applied = await reply(play.desk, 3, ['has been applied to your account', 'credit reference']);
  await snap(play.desk, '06-credit-approved-customer-told.png', {
    ...meta,
    title: 'After the supervisor approves: the credit is applied once, with its reference',
    shows: `The same customer conversation after the supervisor pressed Approve: a follow-up message says the service credit of ${play.amount} has been applied to the account and gives the credit reference.`,
    facts: deskFacts(applied),
  });
  await snap(run.con, '06b-console-credit-on-account.png', {
    ...meta,
    title: 'On the billing system: one credit on the account, with who approved it',
    shows: 'The console on the human-approval scene after the approval: the credit on the customer\'s account as the simulated billing system holds it, with its reference, amount, status and the supervisor who approved it.',
    facts: [...castFacts(play.cast), ...(await board(6, ['CR-', 'applied']))],
    notes: 'The window only. The hint under the minutes box is the console\'s own text.',
  }, { fullPage: false });
  const trail = await openTrail(play.desk);
  await snap(play.desk, '12-audit-trail-close-crop.png', {
    ...meta,
    title: 'Close crop: the audit trail of the contact whose credit a supervisor approved',
    shows: 'The audit trail panel of this contact, opened as staff: every step from the complaint to the credit, each with its outcome, the actor that did it (an agent of the platform, the executor, the supervisor), its duration in milliseconds where timed, and the versions in force.',
    facts: deskFacts(trail, { trail: true }).filter((fact) => fact.startsWith('Audit trail')),
    notes: 'A crop of the customer desk after "Open as staff". Each entry reads "step → outcome", then actor · duration · versions. The messages were read by the offline keyword rules (rules-v1).',
  }, { crop: [play.desk.locator('.panel--trail')] });
}

async function smallCredit() {
  const play = await playCredit(300, SMALL_CREDIT_PLAN, FIVE_HOUR_LINE);
  const scene = 'Scene 6 with a 300-minute outage: a small credit is applied without review';
  if (!play.restored.includes('It is being applied to your account')) throw new Error(`the small credit was not applied without review: ${play.restored}`);
  const applied = await reply(play.desk, 3, ['has been applied to your account', 'credit reference']);
  await snap(play.desk, '04-small-credit-applied-without-review.png', {
    scene,
    customer: play.customer,
    title: `The outage is resolved: a credit of ${play.amount} is applied without review`,
    shows: `The customer desk after an outage that began 300 minutes earlier was resolved: the restoration message carries a service credit of ${play.amount} that "is being applied", and the follow-up says it "has been applied" with its credit reference. No person was asked.`,
    facts: deskFacts(applied),
    notes: `A separate outage (${play.outageId}), declared backdated by 300 minutes with the console's credit scene, because the happy-path outage of images 02 and 03 was minutes old and earns nothing (the stand-in policy pays from 240 minutes). The customer's line is the scene's, with "five hours" in place of "two days" to fit this outage. On this stack a credit up to INR 500 is applied without review (client decision D3, execution plan); the code's default holds every credit for a person.`,
  });
  await snap(run.con, '04c-console-small-credit-on-account.png', {
    scene,
    customer: play.customer,
    title: 'On the billing system: the small credit, applied with no approver',
    shows: 'The console after the small credit: the credit on the customer\'s account as the simulated billing system holds it. No approver is named, because none was asked.',
    facts: [...castFacts(play.cast), ...(await board(6, ['CR-', 'applied']))],
    notes: 'The window only. The hint under the minutes box is the console\'s own text and describes its first cast, on INR 4,999 a month.',
  }, { fullPage: false });
}

// --- the other scenes ----------------------------------------------------------------------------

async function guardrail() {
  const cast = await openScene(7);
  const lines = await linesOf();
  const desk = await openDesk(cast.Customer);
  await send(desk, lines[0]);
  await reply(desk, 1, ["I can't help with other requests"]);
  const state = await openTrail(desk, 'guardrail.input');
  await snap(desk, '08-guardrail-injection-refused.png', {
    title: 'A guardrail block: a prompt injection is refused and flagged in the audit trail',
    scene: 'Scene 7, guardrail: a prompt injection',
    customer: cast.Customer,
    shows: 'The customer desk after a message that tells the assistant to ignore its instructions, apply an INR 5,000 credit and print its system prompt: a polite refusal that keeps the reference, and, opened as staff, the audit trail with the input guardrail\'s entry.',
    facts: deskFacts(state, { trail: true }),
    notes: 'No credit was applied and no prompt was shown. The guardrail entry in the trail is "guardrail.input → INJECTION_SUSPECTED".',
  });
}

async function visit() {
  const cast = await openScene(4);
  const lines = await linesOf();
  const meta = { scene: 'Scene 4, dispatch: the line fails its test and a technician is booked', customer: cast.Customer };
  await go(1);
  run.lines.add(cast.Line);
  const desk = await openDesk(cast.Customer);
  await send(desk, lines[0]);
  let state = await reply(desk, 1, ['found a fault']);
  await snap(desk, '09a-visit-line-fault-held.png', {
    ...meta,
    title: 'A failing line on a healthy node: no visit yet, held while a wider fault is ruled out',
    shows: 'The customer desk after the first message: no outage is reported for the area, the remote line test found a fault, and the repair is held while the platform checks whether it is part of a wider network problem.',
    facts: deskFacts(state),
    notes: 'The hold is 20 seconds on this stack (the demo timing), which the message rounds up to "1 minute"; the product default is 10 minutes.',
  });
  state = await reply(desk, 2, ['I can book a visit', '\n1. '], 150000);
  await snap(desk, '09b-visit-slots-offered.png', {
    ...meta,
    title: 'After the hold: the line is tested again and numbered slots are offered',
    shows: 'The same conversation after the hold: the line was tested again and still fails, no outage is reported, and the assistant offers numbered technician slots. Nothing is booked until the customer chooses.',
    facts: deskFacts(state),
  });
  await send(desk, lines[1]);
  state = await replyWith(desk, 'visit is booked for', 90000);
  await snap(desk, '09c-visit-booked.png', {
    ...meta,
    title: 'The customer chooses slot 2: the visit is booked, with its appointment number',
    shows: 'The whole conversation: the fault, the hold, the slots, the customer\'s choice ("2"), the assistant saying it is booking that slot, and the confirmation of the booked visit with its appointment number.',
    facts: deskFacts(state),
  });
  await snap(run.con, '09d-console-visit-on-field-system.png', {
    ...meta,
    title: 'On the field-service system: exactly one appointment on the line',
    shows: 'The console on the dispatch scene: the appointment as the simulated field-service system holds it, and the line still made to fail its test under "In force now".',
    facts: [...castFacts(cast), ...(await board(4, ['AP-', 'booked']))],
    notes: 'The window only.',
  }, { fullPage: false });
  await go(4);
  run.lines.delete(cast.Line);
}

async function guide() {
  const cast = await openScene(3);
  const lines = await linesOf();
  const desk = await openDesk(cast.Customer);
  await send(desk, lines[0]);
  const state = await reply(desk, 1, ['Source: guide-', '\n1. ']);
  await snap(desk, '10-router-guide-steps-with-source.png', {
    title: 'Router advice: numbered steps quoted from the customer\'s own router guide, with the source',
    scene: 'Scene 3, troubleshooting: no outage, steps from the guide',
    customer: cast.Customer,
    shows: `The customer desk of a customer whose service record has a ${cast.Router} router: the network chip says none reported, the reply says no outage is reported and the remote line test passed, then gives numbered steps from the guide for that router model, ending with the source line (guide, section, version).`,
    facts: [`Router on the service record: ${cast.Router}`, ...deskFacts(state)],
    notes: 'The router model comes from the service record, never from the message. The steps are quoted from the guide section the retriever chose; a backtick in a step is the guide\'s own markup, shown as written.',
  });
}

async function curveball() {
  const cast = await openScene(5);
  const lines = await linesOf();
  const meta = { scene: 'Scene 5, the curveball: the Network Monitoring API is down', customer: cast.Customer };
  await go(1);
  run.networkDown = true;
  const desk = await openDesk(cast.Customer);
  await send(desk, lines[0]);
  let state = await reply(desk, 1, ["can't verify the live network status"]);
  await snap(desk, '07-curveball-status-not-verified.png', {
    ...meta,
    title: 'The curveball: a reference at once, and no all-clear the platform cannot verify',
    shows: 'The customer desk while the Network Monitoring API refuses every call: the complaint reference is still issued at once, the network chip says not verified, and the reply says the live network status cannot be verified. It gives no all-clear and no device steps, and books no technician.',
    facts: deskFacts(state),
  });
  await snap(run.con, '07c-console-network-api-down.png', {
    ...meta,
    title: 'Backstage during the curveball: the Network Monitoring API is down',
    shows: 'The console on the curveball scene: the Network Monitoring API listed under "Failing APIs", and the count of calls it has refused since it was taken down.',
    facts: [...castFacts(cast), ...(await board(5, ['Calls it refused']))],
    notes: 'The window only.',
  }, { fullPage: false });
  await go(3);
  run.networkDown = false;
  state = await reply(desk, 2, [], 120000);
  await snap(desk, '07b-curveball-recovered.png', {
    ...meta,
    title: 'Degraded-mode recovery: the platform looks again by itself and follows up',
    shows: 'The same conversation after the Network Monitoring API came back: without a new message from the customer, the platform checked the network and the line and followed up in the same conversation. The chip now reads none reported.',
    facts: deskFacts(state),
    notes: 'On this stack the platform looks again every 10 seconds (the demo timing).',
  });
}

async function ops() {
  const page = await staffDesk('ops');
  await refreshed(page);
  await snap(page, '13-operations-queue.png', {
    title: 'The staff desk signed in as operations: the operations queue and the backlog line',
    scene: 'Staff desk, operations view',
    shows: 'The staff desk signed in as operations staff: the line that says whether the assistant is keeping up, and the queue of what went wrong with no customer waiting on it. Operations staff are not shown credits.',
    facts: await staffFacts(page),
    notes: `This queue and backlog line are what the build has for operations; there are no metrics, dashboards or alerts. Any item listed is whatever the stack had raised by the time of capture; an empty list means nothing was waiting. ${STAFF_NAMES}`,
  });
}

async function freeplay() {
  const { con } = run;
  await con.bringToFront();
  await con.locator('#scene-list .scene-list__item').last().click();
  await con.locator('form.tool').first().waitFor();
  await snap(con, '14-console-free-play.png', {
    title: 'The demo console: free play',
    scene: 'Free play',
    shows: 'The console\'s free-play page: find customers by any field, declare an outage on any node, give it a new estimate, set any line\'s test result, and make any simulated API fail in any of its ways.',
    facts: (await con.locator('form.tool h3').allTextContents()).map((title) => `Tool: ${title}`),
    notes: 'Nothing was run from this page for the images.',
  });
}

// --- start and end -----------------------------------------------------------------------------

async function attempt(name, scene) {
  say(`\n${name}`);
  try {
    return await scene();
  } catch (error) {
    run.failed.push({ scene: name, reason: error.message.split('\n')[0] });
    say(`  NOT CAPTURED: ${error.message.split('\n')[0]}`);
    return null;
  }
}

// Ends what this run started on the simulator, whatever happened.
async function restore() {
  const done = [];
  if (run.networkDown) {
    for (const target of ['network', 'network_feed']) await simApi('/admin/v1/chaos', { method: 'POST', body: { target, mode: 'ok' } });
    done.push('Network API restored');
  }
  for (const serviceId of run.lines) {
    await simApi('/admin/v1/line-faults', { method: 'POST', body: { service_id: serviceId, fault: false } });
    done.push(`${serviceId} passes again`);
  }
  for (const outageId of run.outages) {
    await simApi(`/admin/v1/outages/${outageId}/resolve`, { method: 'POST' });
    done.push(`${outageId} resolved`);
  }
  const [outages, chaos, faults] = await Promise.all([
    simApi('/admin/v1/outages?active=true'),
    simApi('/admin/v1/chaos'),
    simApi('/admin/v1/line-faults'),
  ]);
  say(`\nRestored: ${done.join(', ') || 'nothing was left to end'}.`);
  say(`In force now: ${outages.outages.length} outage(s), ${chaos.rules.length} failing API(s), ${faults.line_faults.length} failing line(s).`);
}

// How the running stack is set up, from the settings the stack script wrote (no secret is read).
function stackSettings() {
  const file = join(REPO, 'var', 'run', 'care-worker.env');
  const wanted = ['CARE_LLM_PROVIDER', 'CARE_CREDIT_REVIEW_ALL', 'CARE_DISPATCH_HOLD_SECONDS', 'CARE_NETWORK_RECHECK_SECONDS'];
  return existsSync(file) ? readEnv(file, wanted) : {};
}

function noteFor(stack, now) {
  const reader = stack.CARE_LLM_PROVIDER === 'offline'
    ? 'The offline keyword rules read the customers\' messages: no language model was called.'
    : `The customers' messages were read by the "${stack.CARE_LLM_PROVIDER ?? 'unknown'}" provider.`;
  const credits = stack.CARE_CREDIT_REVIEW_ALL === 'false'
    ? 'credits up to INR 500 applied without review (above INR 500 a supervisor approves)'
    : 'every credit held for a person';
  return (
    `Real screenshots of the local demonstration stack on this machine, taken on ${istDate(now)} (Indian time) by ` +
    'presentation/src/capture_screens.mjs in headless Chrome: a 1440 x 900 window at 1.5x, light theme, reduced motion, the ' +
    `browser's clock set to Asia/Kolkata. ${reader} All data is synthetic and the client, SkyLink Telecom, is fictional; the ` +
    '"enterprise systems" are the team\'s simulator. Nothing in any image is mocked, edited or composited; a "crop" is a part ' +
    `of the real page. The stack ran with a technician visit offered after ${stack.CARE_DISPATCH_HOLD_SECONDS ?? '?'} seconds ` +
    `(the product default is 10 minutes), the network looked at again every ${stack.CARE_NETWORK_RECHECK_SECONDS ?? '?'} seconds, and ${credits}. ` +
    'visible_facts were read from the page at the moment of capture; "pixels" is the size of the image. File numbers 01 to 12 ' +
    'follow the twelve shots the deck asked for, a letter marks a companion image of the same scene, and 13 and 14 are extras.'
  );
}

function writeManifest(wanted, minutes) {
  const stack = stackSettings();
  const now = new Date();
  const before = readJson(MANIFEST, { shots: [], runs: [] });
  const kept = before.shots.filter((old) => !run.shots.some((shot) => shot.file === old.file));
  const shots = [...kept, ...run.shots].sort((a, b) => a.file.localeCompare(b.file));
  // Every run that wrote into this manifest: a full one, then any retakes of single scenes.
  const thisRun = {
    finished_at: now.toISOString(),
    scenes: wanted,
    took_minutes: Number(minutes.toFixed(1)),
    images_saved: run.shots.map((shot) => shot.file),
    not_captured: run.failed,
  };
  const manifest = {
    note: noteFor(stack, now),
    known_blemish:
      'On customer-desk images whose network chip reads "outage confirmed · HH:MM IST", the values in the complaint-reference ' +
      'box run a few pixels past its right border. That is how the page lays out in a 1440 px window; it was not retouched, and ' +
      'each such image says so in its notes. 02w is the happy-path reply in a 1600 px window, where the box has room.',
    stack_settings: stack,
    runs: [...(before.runs ?? []), thisRun].slice(-10),
    shots,
    // A note someone left about entries corrected by hand stays for as long as any of them does.
    ...(before.manifest_edited_by_hand && kept.length ? { manifest_edited_by_hand: before.manifest_edited_by_hand } : {}),
  };
  writeFileSync(MANIFEST, `${JSON.stringify(manifest, null, 2)}\n`);
  if (minutes > LONGEST_RUN_MINUTES) {
    say(`\nWARNING: this run took ${minutes.toFixed(0)} minutes. The machine may have slept in the middle: check that the times in the images follow one another, or run it again.`);
  }
  say(`\n${run.shots.length} image(s) saved, ${shots.length} in the manifest, ${run.failed.length} scene(s) not captured.`);
}

async function play(wanted) {
  const has = (name) => wanted.includes(name);
  run.con = await openConsole();
  const played = has('happy') ? await attempt('happy path (start)', happy) : null;
  if (has('handover')) await attempt('handover', handover);
  if (played) await attempt('happy path (end)', () => happyEnd(played));
  const rest = { approval, smallcredit: smallCredit, guardrail, visit, guide, curveball, ops, freeplay };
  for (const [name, scene] of Object.entries(rest)) {
    if (has(name)) await attempt(name, scene);
  }
}

function playwright() {
  try {
    return createRequire(import.meta.url)(PLAYWRIGHT);
  } catch (error) {
    throw new Error(
      'playwright-core was not found. Install it (npm install playwright-core) or set PLAYWRIGHT_CORE to its folder.',
      { cause: error },
    );
  }
}

async function main() {
  const wanted = process.argv.slice(2).length ? process.argv.slice(2) : ALL;
  const unknown = wanted.filter((name) => !ALL.includes(name));
  if (unknown.length) throw new Error(`unknown scene(s): ${unknown.join(', ')}. Scenes: ${ALL.join(' ')}`);
  if (!LOCAL.has(new URL(DESK).hostname) || !LOCAL.has(new URL(SIM).hostname)) {
    throw new Error('this script drives only a stack on this machine');
  }
  const started = Date.now();
  run.keys = keys();
  mkdirSync(OUT, { recursive: true });
  if (!CHROME) throw new Error('Chrome was not found. Set CHROME to the path of a Chrome or Chromium binary.');
  const { chromium } = playwright();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  run.ctx = await browser.newContext({
    viewport: WINDOW,
    deviceScaleFactor: 1.5,
    colorScheme: 'light',
    reducedMotion: 'reduce',
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
  });
  run.ctx.setDefaultTimeout(30000);
  try {
    await play(wanted);
  } finally {
    await restore().catch((error) => say(`COULD NOT RESTORE THE SIMULATOR: ${error.message}`));
    await browser.close();
    writeManifest(wanted, (Date.now() - started) / 60000);
  }
  if (run.failed.length) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 2;
});
