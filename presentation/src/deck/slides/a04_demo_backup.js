// Appendix, part 4: the demonstration told in real screenshots, in case the live one fails.
//
// Every picture is a crop of an image in presentation/src/assets/screens/ (see manifest.json):
// the local stack on 26 November 2026 (Indian time), with the offline keyword rules reading the messages.
// The live demonstration runs on AWS with OpenAI's gpt-5.4-mini. Nothing in a picture is edited.

const { S, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { appendixSlide, sourceNote } = require("../lib/deck");

const TOP = G.APX_BODY_Y;
const BACKUP_SOURCE = "Real screenshots, cropped and not edited: the local stack, offline keyword rules, 26 November 2026 (presentation/src/assets/screens/manifest.json). The live demonstration runs on AWS.";

/**
 * The story of a path, as numbered points in columns.
 * @param {object} slide
 * @param {string[]} points
 * @param {{x: number, y: number, w: number, h: number, columns: number}} o
 * @returns {void}
 */
function story(slide, points, o) {
  const gap = 0.2;
  const colW = (o.w - (o.columns - 1) * gap) / o.columns;
  const rows = Math.ceil(points.length / o.columns);
  const rowH = (o.h - (rows - 1) * 0.1) / rows;
  points.forEach((point, index) => {
    const x = o.x + (index % o.columns) * (colW + gap);
    const y = o.y + Math.floor(index / o.columns) * (rowH + 0.1);
    kit.panel(slide, { x, y, w: colW, h: rowH, name: `Story card ${index + 1}` });
    kit.dot(slide, index + 1, x + 0.14, y + (rowH - 0.32) / 2, { d: 0.32, fontSize: 12 });
    kit.text(slide, point, { x: x + 0.58, y: y + 0.06, w: colW - 0.7, h: rowH - 0.12, fontSize: 14, valign: "middle", objectName: `Story ${index + 1}` });
  });
}

/** A13: the happy path. */
async function happyPathSlide(pres) {
  const slide = appendixSlide(pres, "A13  Demo backup: the happy path, a known outage");
  const reply = await kit.shot(slide, "02w-happy-path-outage-reply-wide.png", { x: G.MX, y: TOP, w: 8.25, crop: { left: 30, top: 325, width: 1580, height: 600 } });
  kit.caption(slide, "Image 02w. The customer desk: the complaint reference with the time it took to issue, the confirmed outage, and the reply naming the outage, its cause and the estimate with its source.", { x: G.MX, y: reply.y + reply.h + 0.05, w: 8.25, h: 0.4 });
  const board = await kit.shot(slide, "03-console-proactive-notices.png", { x: G.MX + 8.5, y: TOP, w: G.CW - 8.5, h: reply.h, crop: { left: 1525, top: 480, width: 605, height: 500 }, align: "center" });
  kit.caption(slide, "Image 03. The console: notices sent, read back from the simulator.", { x: G.MX + 8.5, y: board.y + board.h + 0.05, w: G.CW - 8.5, h: 0.4 });
  story(
    slide,
    [
      "The NOC declares a fibre cut. 179 notices go to 179 customers on the node; 50 had said no, and none of them is sent one.",
      "The customer writes in. The complaint reference is issued in 15 ms.",
      "The reply names the outage, its cause, the estimate and where it comes from. No restart, no technician.",
      "When the outage is resolved the customer is told, and the contact closes on their word.",
    ],
    { x: G.MX, y: 5.26, w: G.CW, h: 1.3, columns: 4 },
  );
  kit.sourceLine(slide, BACKUP_SOURCE);
  sourceNote(slide, "presentation/src/assets/screens/manifest.json, images 02w, 03 and 04b (reference SKL-261126-049130-5 issued in 15 ms; 179 notices sent to 179 customers; said no to notices: 50 on the node, 0 sent one; the contact closed on the customer's confirmation); src/mock_oss/console/scenes.js, scene 1.");
}

/** A14: the exception, a failure handled. */
async function exceptionSlide(pres) {
  const slide = appendixSlide(pres, "A14  Demo backup: the Network Monitoring API goes down");
  const file = "07-curveball-status-not-verified.png";
  const col1 = { x: G.MX, w: 4.3 };
  const col2 = { x: G.MX + 4.55, w: 3.0 };
  const col3 = { x: G.MX + 7.8, w: G.CW - 7.8 };

  const reply = await kit.shot(slide, file, { x: col1.x, y: TOP, w: col1.w, crop: { left: 596, top: 330, width: 800, height: 480 } });
  kit.caption(slide, "Image 07. The reply gives no all-clear and no device steps.", { x: col1.x, y: reply.y + reply.h + 0.04, w: col1.w, h: 0.24 });
  const follow = await kit.shot(slide, "07b-curveball-recovered.png", { x: col1.x, y: reply.y + reply.h + 0.36, w: col1.w, crop: { left: 590, top: 815, width: 810, height: 310 } });
  kit.caption(slide, "Image 07b. The follow-up, sent without a new message.", { x: col1.x, y: follow.y + follow.h + 0.04, w: col1.w, h: 0.24 });

  const docket = await kit.shot(slide, file, { x: col2.x, y: TOP, w: col2.w, crop: { left: 42, top: 566, width: 470, height: 304 } });
  kit.caption(slide, "Image 07. The reference, and the network chip.", { x: col2.x, y: docket.y + docket.h + 0.04, w: col2.w, h: 0.24 });
  const backstage = await kit.shot(slide, "07c-console-network-api-down.png", { x: col2.x, y: docket.y + docket.h + 0.36, w: col2.w, crop: { left: 1520, top: 840, width: 610, height: 440 } });
  kit.caption(slide, "Image 07c. Backstage, on the console.", { x: col2.x, y: backstage.y + backstage.h + 0.04, w: col2.w, h: 0.24 });

  story(
    slide,
    [
      "The console takes the Network Monitoring API down. Every call to it now fails: 3 refused so far.",
      "The customer still gets a complaint reference, in 10 ms.",
      "The reply says the status cannot be verified: no all-clear, no router steps, no technician.",
      "When the API returns, the platform looks again by itself: no outage reported, and the line test passed.",
    ],
    { x: col3.x, y: TOP, w: col3.w, h: G.BODY_B - TOP, columns: 1 },
  );
  kit.sourceLine(slide, BACKUP_SOURCE);
  sourceNote(slide, "presentation/src/assets/screens/manifest.json, images 07, 07b and 07c (reference SKL-261126-049137-0 issued in 10 ms; network chip \"not verified\"; calls the API refused: 3; the follow-up after the API came back); src/mock_oss/console/scenes.js, scene 5; docs/adr/0001-degraded-mode-when-the-network-api-fails.md.");
}

/** A15: the human approval. */
async function approvalSlide(pres) {
  const slide = appendixSlide(pres, "A15  Demo backup: a credit above $500 waits for a supervisor");
  const slip = await kit.shot(slide, "05c-credit-slip-worked-out-crop.png", { x: G.MX, y: TOP, w: 3.05, h: 4.5 });
  kit.caption(slide, "Image 05c. The supervisor's slip, with its working.", { x: G.MX, y: slip.y + slip.h + 0.04, w: 3.05, h: 0.24 });

  const midX = G.MX + 3.3;
  const midW = 4.3;
  const agent = await kit.shot(slide, "05b-agent-desk-credit-not-offered.png", { x: midX, y: TOP, w: midW, crop: { left: 30, top: 480, width: 1250, height: 390 } });
  kit.caption(slide, "Image 05b. Signed in as an agent: the credit is not offered.", { x: midX, y: agent.y + agent.h + 0.04, w: midW, h: 0.24 });
  const told = await kit.shot(slide, "06-credit-approved-customer-told.png", { x: midX, y: agent.y + agent.h + 0.36, w: midW, crop: { left: 590, top: 1440, width: 810, height: 235 } });
  kit.caption(slide, "Image 06. After the approval, the customer is told.", { x: midX, y: told.y + told.h + 0.04, w: midW, h: 0.24 });
  const billing = await kit.shot(slide, "06b-console-credit-on-account.png", { x: midX, y: told.y + told.h + 0.36, w: midW, crop: { left: 1525, top: 950, width: 600, height: 160 } });
  kit.caption(slide, "Image 06b. The billing system: one credit, and who approved it.", { x: midX, y: billing.y + billing.h + 0.04, w: midW, h: 0.24 });

  story(
    slide,
    [
      "A plan of $4,999 a month, out for 50 hours: 4,999 × 50 × 1.5 / 720 = $520.73 in our run, worked out in code.",
      "Above $500 the request waits. An agent is not offered it.",
      "The supervisor sees the frozen request and its working, and approves.",
      "The executor applies the credit once, and the customer is told with the credit reference.",
    ],
    { x: midX + midW + 0.25, y: TOP, w: G.MX + G.CW - (midX + midW + 0.25), h: G.BODY_B - TOP, columns: 1 },
  );
  kit.sourceLine(slide, BACKUP_SOURCE);
  sourceNote(slide, "presentation/src/assets/screens/manifest.json, images 05b, 05c, 06 and 06b (the slip: $520.73, needs a supervisor, monthly fee $4999.00, outage 3000 min, billable hours 50, multiplier × 1.5; credit CR-BE8EBDFAEEA0 applied, supervisor:demo.supervisor, a demo persona); README.md, \"Service credits and approvals\"; src/skylink_care/domain/credit.py:289 (hours are rounded up, so a run resolved a minute later bills a 51st hour: 4,999 × 51 × 1.5 / 720 = $531.14).");
}

/** A16: the guardrail block. */
async function guardrailSlide(pres) {
  const slide = appendixSlide(pres, "A16  Demo backup: a prompt injection is refused and logged");
  const file = "08-guardrail-injection-refused.png";
  const talk = await kit.shot(slide, file, { x: G.MX, y: TOP, w: 5.9, crop: { left: 580, top: 320, width: 830, height: 490 } });
  kit.caption(slide, "Image 08. The message, and the polite refusal that keeps the reference.", { x: G.MX, y: talk.y + talk.h + 0.05, w: 5.9, h: 0.24 });
  const trail = await kit.shot(slide, file, { x: G.MX + 6.15, y: TOP, w: 4.0, h: talk.h, crop: { left: 1460, top: 220, width: 660, height: 580 } });
  kit.caption(slide, "Image 08, opened as staff: the audit trail, with the input guardrail's entry.", { x: G.MX + 6.15, y: trail.y + trail.h + 0.05, w: 5.9, h: 0.24 });
  kit.text(slide, "No credit was applied and no prompt was shown.", { x: trail.x + trail.w + 0.25, y: TOP, w: G.MX + G.CW - (trail.x + trail.w + 0.25), h: 1.2, fontSize: 16, bold: true, color: S.teal, objectName: "The outcome" });
  story(
    slide,
    [
      "The message tells the assistant to ignore its instructions, apply $5,000 and print its system prompt.",
      "The reply declines, politely, and still carries the complaint reference.",
      "The trail records it: guardrail.input → INJECTION_SUSPECTED.",
    ],
    { x: G.MX, y: 5.42, w: G.CW, h: 1.14, columns: 3 },
  );
  kit.sourceLine(slide, BACKUP_SOURCE);
  sourceNote(slide, "presentation/src/assets/screens/manifest.json, image 08 (the message, the refusal, the entry guardrail.input → INJECTION_SUSPECTED; no credit applied and no prompt shown); presentation/G3_Telco_Checklists.pdf, item 1.1; src/mock_oss/console/scenes.js, scene 7.");
}

module.exports = { slides: [happyPathSlide, exceptionSlide, approvalSlide, guardrailSlide] };
