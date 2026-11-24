// Segment 6, curveball and ROI close (R1, 1 minute): slide 18.

const { HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { mainSlide, speakerNotes } = require("../lib/deck");

const SEGMENT = "close";
const ROLE = "R1";

// Three kinds of figure, kept apart. "lines" is how many lines the text takes in its column.
const WORTH = [
  { kind: "fact", tag: "CASE FACTS · BRIEF P.1", lines: 2, body: "30% of outage calls waste a visit of INR 650: INR 195 for each outage call." },
  { kind: "measured", tag: "MEASURED IN SIMULATION", lines: 3, body: "The workflow booked 0 of 28 unneeded visits and 10 of 10 needed ones. What staff then decide, no replay can measure." },
  { kind: "assumption", tag: "ASSUMPTION", lines: 4, body: "The model's base case, where every volume, rate and cost is assumed (A12): net INR 695,000 a month. No rupee saving is measured." },
];

const STAGES = ["Backtest", "Shadow mode", "Actions under approval"];
const HEAD_H = 0.4;
const BODY_TOP = 0.92; // where a column's content starts, below its label and headline

/**
 * A column of the closing slide: a dark panel with its label and headline.
 * @param {object} slide
 * @param {{x: number, w: number, h: number, label: string, head: string}} o
 * @returns {void}
 */
function column(slide, o) {
  kit.panel(slide, { x: o.x, y: G.BODY_Y, w: o.w, h: o.h, fill: HEX.darkPanel, name: `Panel ${o.label}` });
  kit.kicker(slide, o.label, { x: o.x + 0.22, y: G.BODY_Y + 0.16, w: o.w - 0.44, color: HEX.darkAccent });
  kit.text(slide, o.head, { x: o.x + 0.22, y: G.BODY_Y + 0.44, w: o.w - 0.44, h: HEAD_H, fontFace: FONT.head, fontSize: 18, bold: true, color: HEX.darkText, valign: "middle", objectName: o.head });
}

/** Slide 18: what changed, what it is worth, what we ask. */
async function closeSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "We ask for a gated, time-boxed broadband pilot: shadow mode before any automated action");

  const top = G.BODY_Y;
  const gap = 0.25;
  const colW = (G.CW - 2 * gap) / 3;
  const colH = 4.36;
  const x1 = G.MX;
  const x2 = G.MX + colW + gap;
  const x3 = G.MX + 2 * (colW + gap);

  // What changed: the curveball, as a re-scope with its trade-off.
  column(slide, { x: x1, w: colW, h: colH, label: "WHAT CHANGED: THE CURVEBALL", head: "A third answer: unknown" });
  kit.text(slide, "Network status is now outage, confirmed clear or unknown, and unknown is never reported as clear. The complaint reference depends on our database alone, so it still arrives at once. No router steps and no visit are offered until the status can be read.", {
    x: x1 + 0.22,
    y: top + BODY_TOP,
    w: colW - 0.44,
    h: 1.9,
    fontSize: 14,
    color: HEX.darkText,
    objectName: "The re-scope",
  });
  kit.panel(slide, { x: x1 + 0.14, y: top + 2.9, w: colW - 0.28, h: 1.32, fill: HEX.amberSoft, name: "Trade-off panel" });
  kit.text(
    slide,
    [
      { text: "THE TRADE-OFF  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.amberInk } },
      { text: "While the API is down, a customer on a healthy node gets no help with their own equipment and is with a person after about five minutes.", options: { color: HEX.ink } },
    ],
    { x: x1 + 0.28, y: top + 2.96, w: colW - 0.56, h: 1.2, fontSize: 14, valign: "middle", objectName: "The trade-off" },
  );

  // What it is worth: case facts, measured behaviour and assumptions kept apart.
  column(slide, { x: x2, w: colW, h: colH, label: "WHAT IT IS WORTH", head: "A scenario, not a forecast" });
  let y = top + BODY_TOP;
  for (const item of WORTH) {
    kit.tag(slide, item.kind, x2 + 0.22, y, { text: item.tag });
    const h = item.lines * 0.24 + 0.04;
    kit.text(slide, item.body, { x: x2 + 0.22, y: y + 0.3, w: colW - 0.44, h, fontSize: 14, color: HEX.darkText, objectName: `Worth: ${item.tag}` });
    y += 0.3 + h + 0.1;
  }

  // What we ask.
  column(slide, { x: x3, w: colW, h: colH, label: "WHAT WE ASK", head: "A pilot, in three stages" });
  const stageW = (colW - 0.44 - 0.2) / 3;
  STAGES.forEach((stage, index) => {
    const sx = x3 + 0.22 + index * (stageW + 0.1);
    slide.addText(
      [
        { text: `${index + 1}`, options: { bold: true, color: HEX.darkAccent, breakLine: true } },
        { text: stage, options: { color: HEX.darkText } },
      ],
      { x: sx, y: top + BODY_TOP, w: stageW, h: 0.9, shape: "roundRect", rectRadius: 0.06, fill: { color: HEX.ink }, line: { color: HEX.darkAccent, width: 1 }, margin: 4, fontSize: 12, align: "center", valign: "middle", objectName: `Stage ${stage}` },
    );
  });
  kit.text(
    slide,
    [
      { text: "On a defined set of nodes. It stops automated writes on: ", options: { bold: true } },
      { text: "a duplicate effect, a policy mismatch, a dispatch on stale data, a data leak.", options: { breakLine: true } },
      { text: "From SkyLink: ", options: { bold: true } },
      { text: "real contact volumes, the real policy and API contracts, and the client decisions still open (A5)." },
    ],
    { x: x3 + 0.22, y: top + BODY_TOP + 1.06, w: colW - 0.44, h: 1.96, fontSize: 14, color: HEX.darkText, paraSpaceAfter: 8, objectName: "The gates and the needs" },
  );
  kit.tag(slide, "assumption", x3 + 0.22, top + 3.98, { text: "PROPOSED, NOT YET AGREED" });

  kit.sourceLine(slide, "Sources: docs/adr/0001 (the curveball); presentation/G3_Telco_ROI.xlsx (Inputs, Benefits, Simulation evidence); the team's action plan, p.12, and the discovery report, section 12 (the pilot: proposals).", { dark: true });

  speakerNotes(slide, {
    role: ROLE,
    seconds: 60,
    talk: [
      "To close, three things.",
      "What changed: your curveball. We treated it as a re-scope. Network status gained a third answer, unknown, never reported as clear. The cost: while the API is down, a customer on a healthy node waits about five minutes for a person.",
      "What it is worth. Thirty per cent and 650 rupees are your brief's facts. In simulation the workflow booked none of 28 unneeded visits and all 10 needed ones. The rupee figures are a scenario: on the model's base case, where every volume, rate and cost is assumed, a net 695,000 rupees a month. No rupee saving is measured.",
      "Our ask: a time-boxed broadband pilot on a defined set of nodes. A backtest, then shadow mode, then actions under approval. Thank you.",
    ],
    sources: "docs/adr/0001-degraded-mode-when-the-network-api-fails.md (the third answer and its trade-off); presentation/G3_Telco_ROI.xlsx: Inputs!B9, B10 (30%, INR 650), Baseline!B16 (INR 195 per call), Inputs!B15 to B18, Costs!D7 and D13 (the assumed 10,000 calls, 50% cut, 20% of calls avoided at INR 60, INR 1,200,000 once and INR 400,000 a month), Benefits!B35 and B38 (net INR 695,000 a month, payback 1.73 months), Simulation evidence (0 of 28, 10 of 10; runs of 17 November 2026 UTC); G3_Telco_FDE_Action_Plan.pdf, p.12 (pilot ask and stop conditions: a proposal); discovery workshop report, section 12 (backtest, shadow, controlled automation); docs/EXECUTION_PLAN.md, section 12 (the client decisions still open: D1, D3 in part, D5, D7 to D11).",
  });
}

module.exports = { segment: SEGMENT, slides: [closeSlide] };
