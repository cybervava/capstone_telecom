// Appendix, part 6: what the project review of 25 November 2026 changed. It comes last, so that
// no appendix slide changes its number.

const { S, HEX, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { appendixSlide, sourceNote } = require("../lib/deck");

const HEAD = ["CHANGE", "WHAT IT IS", "WHY", "TRADE-OFF"];
const COLS = [2.2, 3.5, 3.3, 3.133];

const CHANGES = [
  [
    "Amounts in US dollars\nclient decision D12",
    "Every amount is shown in dollars at the brief's figures: a visit costs $650, a supervisor approves a credit above $500. Nothing is converted.",
    "Asked for in the project review. The policy file, the data pack's fields, both desks and every report follow.",
    "The brief and the team's earlier documents give the same figures in rupees. The policy file says so.",
  ],
  [
    "8  A model beside the guide search\nADR 0002, client decision D13",
    "The search finds the sections of the customer's own router guide. A model names the one that fits and points at the words that show why. Code checks both.",
    "Keyword rules miss a fault told in other words. With gpt-5.4-mini: the right section in 144 of 144 answers, 48 descriptions on three router models; 129 by the rules alone.",
    "One more call in a conversation, about 1.4 s and a fee. Without a model, or if it fails, the search chooses as before. The steps stay the guide's own words.",
  ],
  [
    "9  Read-only tools over MCP\nADR 0003, client decision D14",
    "A sixth program serves four tools to other assistants: a node's status, a guide's steps, a credit quotation and the platform's health.",
    "Another assistant gets answers by the platform's own rules instead of guesses. One key that reads a node's status; every call typed, limited and logged.",
    "One key for every client and no sign-in for each. Closed on the AWS host. The model in the customer's path is still given no tool.",
  ],
];

/**
 * @param {string[][]} rows rows of change, what, why, trade-off
 * @returns {(string|object)[][]} table rows, with the change's name in bold over its origin
 */
function changeRows(rows) {
  return rows.map(([name, what, why, cost]) => {
    const [title, origin] = name.split("\n");
    return [
      { text: [{ text: title, options: { bold: true, breakLine: true } }, { text: origin, options: { fontSize: 11, color: HEX.ink2 } }], options: {} },
      what,
      why,
      cost,
    ];
  });
}

/** A21: the three changes of 25 November, and what was measured on them. */
async function reviewSlide(pres) {
  const slide = appendixSlide(pres, "A21  After the project review: three changes of 25 November");
  kit.table(slide, HEAD, changeRows(CHANGES), { y: G.APX_BODY_Y, colW: COLS, name: "The review's changes" });
  const y = 5.02;
  kit.panel(slide, { x: G.MX, y, w: G.CW, h: G.BODY_B - y, fill: HEX.tealSoft, name: "Measured panel" });
  kit.text(
    slide,
    [
      { text: "MEASURED ON THE CHANGED CODE, 25 NOVEMBER 2026  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.tealInk, breakLine: true } },
      { text: "2,902 tests passed" },
      { text: " · attack suite 61 of 61 runs, offline and with gpt-5.4-mini, three attacks added for a model that answers badly about a guide · " },
      { text: "31 of 31 checks that the suite can fail · nine of nine live journeys on the local stack, the MCP server's among them; on the AWS host, the eight it may run." },
    ],
    { x: G.MX + 0.2, y: y + 0.08, w: G.CW - 0.4, h: G.BODY_B - y - 0.16, fontSize: 14, valign: "middle", objectName: "Measured on the change" },
  );
  kit.sourceLine(slide, "Sources: docs/adr/0002 and 0003; docs/EXECUTION_PLAN.md, section 12 (D12 to D14) and the progress log of 25 November; evals/reports/retrieval_openai.md; evals/reports/attack_suite.md and attack_suite_openai.md.");
  sourceNote(slide, "docs/adr/0002-a-language-model-beside-the-guide-search.md and docs/adr/0003-read-only-tools-over-mcp.md (accepted 25 November 2026); docs/EXECUTION_PLAN.md, section 12, client decisions D12 to D14, and section 13, the progress log of 25 November (with what the code reviews changed); evals/reports/retrieval_openai.md (25 November 2026: 144 of 144 with gpt-5.4-mini-2026-03-17 on three router models, 129 of 144 by the search and the rules alone, 1.4 s median); evals/reports/attack_suite.md and attack_suite_openai.md (25 November 2026: 43 attacks, 61 runs, 61 passed, offline and live); scripts/attack_suite_can_fail.py (31 of 31); scripts/smoke.py (journey 9, the MCP server over HTTP). On the AWS host since release 797ab92 (GitLab pipeline 3116487891, 25 November 2026). The release before it, d686b41, failed journey 7 on a race in the check itself and the host went back by itself; 797ab92 fixes the check.");
}

const GUIDE_POINTS = [
  "The search keeps to the customer's own router guide. The keyword rules rank \"No internet\" first.",
  "The model, asked once, names \"Slow speed\" and points at the words that show why.",
  "Code finds those words in their sources before quoting them. The steps stay the guide's own.",
];

/** A22: the guide answer with a model, in two real screenshots. */
async function guideAnswerSlide(pres) {
  const slide = appendixSlide(pres, "A22  The guide answer with a model, and its audit trail");
  const top = G.APX_BODY_Y;
  const h = 3.55;
  const reply = await kit.shot(slide, "10-router-guide-steps-with-source.png", { x: G.MX, y: top, w: 4.5, h, crop: { left: 590, top: 712, width: 810, height: 648 } });
  kit.caption(slide, "Image 10, part of the reply to \"My connection is not down, it's just crawling, a 1 GB file shows 4 hours\".", { x: G.MX, y: reply.y + reply.h + 0.05, w: reply.w, h: 0.5 });
  const tx = reply.x + reply.w + 0.25;
  const trail = await kit.shot(slide, "10b-guide-audit-trail-crop.png", { x: tx, y: top, w: 3.6, h, crop: { left: 0, top: 746, width: 687, height: 586 } });
  kit.caption(slide, "Image 10b, the end of the same contact's audit trail, opened as staff.", { x: tx, y: trail.y + trail.h + 0.05, w: trail.w, h: 0.5 });
  const px = trail.x + trail.w + 0.3;
  const pw = G.MX + G.CW - px;
  const ph = (h - 0.2) / GUIDE_POINTS.length;
  GUIDE_POINTS.forEach((point, index) => {
    const y = top + index * (ph + 0.1);
    kit.panel(slide, { x: px, y, w: pw, h: ph, name: `Guide point ${index + 1}` });
    kit.dot(slide, index + 1, px + 0.14, y + (ph - 0.32) / 2, { d: 0.32, fontSize: 12 });
    kit.text(slide, point, { x: px + 0.58, y: y + 0.06, w: pw - 0.7, h: ph - 0.12, fontSize: 14, valign: "middle", objectName: `Guide point ${index + 1} text` });
  });
  kit.text(
    slide,
    [
      { text: "Without a model ", options: { bold: true } },
      { text: "the same words get the \"No internet\" steps, which is why the console's guide scene uses them. A section the model was not shown is refused, and a sentence of its own is never sent (attacks K1 to K3)." },
    ],
    { x: G.MX, y: 5.82, w: G.CW, h: 0.62, fontSize: 14, color: S.ink2, valign: "middle", objectName: "Without a model" },
  );
  kit.sourceLine(slide, "Real screenshots, cropped and not edited: the local stack, the guide scene retaken with gpt-5.4-mini choosing the section, 26 November 2026 (screenshots manifest, images 10 and 10b).");
  sourceNote(slide, "presentation/src/assets/screens/manifest.json, images 10 and 10b (read_by: openai; router QL-ON4 on the service record; the search found no-internet 1.0274, slow-speed 0.2798, power-cycle 0.2125 and wifi 0.2113; the model, gpt-5.4-mini-2026-03-17 with prompt guide@574ad366e385, chose slow-speed in 1,402 ms, the rules alone no-internet; both passages quoted; source guide-ql-on4 §slow-speed v1.1); src/skylink_care/agents/troubleshooting.py; src/skylink_care/guardrails/grounding.py; docs/adr/0002-a-language-model-beside-the-guide-search.md; evals/reports/attack_suite.md, family K.");
}

module.exports = { slides: [reviewSlide, guideAnswerSlide] };
