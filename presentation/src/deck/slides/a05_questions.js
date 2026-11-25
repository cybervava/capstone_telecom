// Appendix, part 5: the six likely objections, what is not built, and the sources.
//
// The objections are the ones the team's action plan lists (p.11), one for each stakeholder
// the panel plays. Each answer is drawn from the repository's documents and admits what is open.

const { S, HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { appendixSlide, sourceNote } = require("../lib/deck");

const TOP = G.APX_BODY_Y;

const OBJECTIONS_A = [
  {
    icon: "LuTarget",
    role: "Chief Customer Officer",
    question: "Which savings are measured, and which are assumed?",
    answer:
      "Two figures are your brief's: 30% and $650. Measured, in simulation: the workflow booked 0 of 28 visits that were not needed and 10 of 10 that were. Assumed: 10,000 outage calls a month, half the wasted visits, a fifth of calls avoided at $60, $400,000 a month to run. On those the net is $695,000 a month. No dollar saving is measured, and churn is held at zero.",
    open: "SkyLink's real contact volumes, and what staff decide for customers who insist.",
  },
  {
    icon: "LuRadioTower",
    role: "NOC Manager",
    question: "How do you know the status is fresh?",
    answer:
      "A live reading more than 60 seconds old does not count. The event feed is numbered: after a skipped number or 90 seconds of silence its record stops counting as evidence. A case the feed wakes reads the network again for itself, and the executor reads the node again before it books a visit. A reply about the network says when the status was verified and where an estimate comes from.",
    open: "How fresh a status must be is yours to set. The ten-minute hold should follow your real delay in declaring an outage.",
  },
  {
    icon: "LuTruck",
    role: "Field Operations Lead",
    question: "Are you suppressing necessary visits?",
    answer:
      "No. A visit is offered when the node is confirmed clear and the line still fails its test after a ten-minute hold. In the replay every needed visit was booked, 10 of 10, a median of 10.0 minutes after the contact, and none of the baseline's 3,225 unnecessary ones. A customer who goes on asking ends with a person, who holds the evidence and decides.",
    open: "Which test results justify a visit is yours to confirm. 38 replayed contacts are not a sample to take rates from.",
  },
];

const OBJECTIONS_B = [
  {
    icon: "LuScale",
    role: "Regulatory Affairs Manager",
    question: "Is this an official reference, and when is a complaint resolved?",
    answer:
      "The reference is issued by the platform's own ledger when the contact is accepted, then registered with Incident Management; that write is replayed for up to 24 hours while its outcome is unknown, then given to a person. A complaint is resolved when service is restored and that is confirmed: the customer is asked, and it closes on their yes or after a quiet period, 24 hours by default.",
    open: "Whether our reference is the official number (D5). The complaint rules are a placeholder. Nothing reports closure times.",
  },
  {
    icon: "LuCable",
    role: "OSS/BSS Integration Architect",
    question: "What happens after a timeout with an unknown write outcome?",
    answer:
      "Only the executor repeats a write. It replays the identical stored request under the same idempotency key, so the receiver can recognise the repeat and apply it once: after 2 seconds, doubling to 60, with a person told after 8 attempts. Nobody is told a credit was not applied while its outcome is unknown. A test loses the answer to an incident write and still ends with one incident.",
    open: "It holds only if SkyLink's write APIs treat a repeated key as the same request. The simulator does (assumption A8).",
  },
  {
    icon: "LuHeadphones",
    role: "Contact Centre Head",
    question: "Can a human take over with complete context?",
    answer:
      "Yes. A handed-over case reaches the staff desk with its reason, a summary built from the recorded facts, the whole conversation and its audit trail. About two dozen situations hand a conversation to a person. When staff close a case the customer is told once, with the closing note quoted as the staff member's own.",
    open: "Taking a live conversation over is an API route: the staff desk has no button for it yet, and staff cannot reply to the customer from the desk.",
  },
];

/**
 * A slide of three objections: who asks, what we answer, what is still open.
 * @param {object} slide
 * @param {object[]} items
 * @returns {Promise<void>}
 */
async function objections(slide, items) {
  const gap = 0.14;
  const rowH = (G.BODY_B - TOP - 2 * gap) / 3;
  const askW = 3.4;
  const openW = 2.85;
  const answerW = G.CW - askW - openW;
  for (const [index, item] of items.entries()) {
    const y = TOP + index * (rowH + gap);
    kit.panel(slide, { x: G.MX, y, w: G.CW - openW, h: rowH, shadow: true, name: `Objection card ${item.role}` });
    await kit.iconDot(slide, item.icon, G.MX + 0.18, y + 0.18, { d: 0.5 });
    kit.text(
      slide,
      [
        { text: item.role, options: { bold: true, color: S.teal, breakLine: true } },
        { text: `“${item.question}”`, options: { fontFace: FONT.head, bold: true } },
      ],
      { x: G.MX + 0.82, y: y + 0.12, w: askW - 0.92, h: rowH - 0.24, fontSize: 14, objectName: `Question: ${item.role}` },
    );
    kit.text(slide, item.answer, { x: G.MX + askW + 0.1, y: y + 0.08, w: answerW - 0.3, h: rowH - 0.16, fontSize: 14, valign: "middle", objectName: `Answer: ${item.role}` });
    kit.panel(slide, { x: G.MX + G.CW - openW + 0.12, y, w: openW - 0.12, h: rowH, fill: HEX.amberSoft, name: `Open panel ${item.role}` });
    kit.text(
      slide,
      [
        { text: "STILL OPEN", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.amberInk, breakLine: true } },
        { text: item.open },
      ],
      { x: G.MX + G.CW - openW + 0.28, y: y + 0.08, w: openW - 0.44, h: rowH - 0.16, fontSize: 14, valign: "middle", objectName: `Open: ${item.role}` },
    );
  }
}

/** A17: objections from the sponsor, the NOC and field operations. */
async function objectionsOneSlide(pres) {
  const slide = appendixSlide(pres, "A17  Likely objections: the sponsor, the NOC, field operations");
  await objections(slide, OBJECTIONS_A);
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_ROI.xlsx; evals/reports/truck_rolls.md; presentation/G3_Telco_Architecture.pdf, sections 2, 3 and 11. Measured in simulation, on stand-in data.");
  sourceNote(slide, "the team's action plan, p.11 (the objections); presentation/G3_Telco_ROI.xlsx (Inputs, Benefits, Simulation evidence); evals/reports/truck_rolls.md (0 of 3,225 unnecessary visits; 10 of 10 needed ones, a median of 10.0 minutes after the contact; 38 contacts replayed; 28 insisting customers ended with a person); presentation/G3_Telco_Architecture.pdf, section 2 (90 seconds of silence), section 3 (the visit rule, a ten-minute hold) and section 11 (a reading over 60 seconds old does not count); presentation/G3_Telco_Evaluation.pdf, section 3.1 (the hold must be set from the real delay).");
}

/** A18: objections from regulatory affairs, the integration architect and the contact centre. */
async function objectionsTwoSlide(pres) {
  const slide = appendixSlide(pres, "A18  Likely objections: regulation, integration, the contact centre");
  await objections(slide, OBJECTIONS_B);
  kit.sourceLine(slide, "Sources: docs/regulatory_controls.md; presentation/G3_Telco_Checklists.pdf, items 3.7 and 4.7; presentation/G3_Telco_Architecture.pdf, section 11; docs/EXECUTION_PLAN.md (A8, D5).");
  sourceNote(slide, "the team's action plan, p.11 (the objections); docs/regulatory_controls.md, \"Complaint reference\" and \"Resolution and closure\" (the reference is issued by the platform's ledger and registered with Incident Management; a complaint closes on the customer's yes or after a quiet period, 24 hours by default; the 4-hour closure target is not met and nothing reports closure times); presentation/G3_Telco_Checklists.pdf, item 4.7 (a write is replayed by the executor under its idempotency key, at 2 s doubling to 60 s; a person is told after 8 attempts) and item 3.7 (takeover is an API route; closing a case tells the customer once); presentation/G3_Telco_Evaluation.pdf, section 3.4 (the lost incident write; assumption A8); docs/HOW_IT_WORKS.md, section 7.3 (about two dozen situations hand over); docs/EXECUTION_PLAN.md, section 12 (D5).");
}

const LIMITS = [
  {
    label: "NOT BUILT",
    color: HEX.redInk,
    lines: [
      "Metrics, dashboards, alerts, a runbook",
      "Machine learning: no churn score, no anomaly model, no learned embedder",
      "A second server, a load balancer, a standby database",
      "A private link to real SkyLink systems",
      "Real staff sign-in; a database role for each program",
      "A retention job; export or erasure on request",
      "A reply and a takeover button on the staff desk",
      "A cost estimate for a pilot",
    ],
  },
  {
    label: "A PILOT WOULD CHANGE",
    color: HEX.amberInk,
    lines: [
      "More than one copy of the programs, across two zones",
      "SkyLink's real systems, behind a gateway SkyLink runs, over a private link",
      "A managed queue and stream; a standby database",
      "Short-lived keys from SkyLink's identity system",
      "The real data, guides, credit policy and API contracts in place of the stand-ins",
      "A ruling on where the language model may process messages",
    ],
  },
  {
    label: "A PILOT WOULD HAVE TO MEASURE",
    color: HEX.tealInk,
    lines: [
      "The NOC's real delay in declaring an outage",
      "How often a line fails for reasons of its own",
      "How many customers go on asking, and what staff then decide",
      "Guide retrieval on real customers' words",
      "Whether the real write APIs honour an idempotency key",
      "The surge on the deployed stack, with the model in the turn",
      "How long closures take",
    ],
  },
];

/** A19: what is not built, and what a pilot would add. */
async function limitsSlide(pres) {
  const slide = appendixSlide(pres, "A19  What is not built, and what a pilot would change");
  const gap = 0.25;
  const colW = (G.CW - 2 * gap) / 3;
  const colH = G.BODY_B - TOP;
  LIMITS.forEach((column, index) => {
    const x = G.MX + index * (colW + gap);
    kit.panel(slide, { x, y: TOP, w: colW, h: colH, shadow: true, name: `Panel ${column.label}` });
    kit.kicker(slide, column.label, { x: x + 0.22, y: TOP + 0.16, w: colW - 0.44, color: column.color });
    kit.text(slide, kit.bulletRuns(column.lines, { paraSpaceAfter: 6 }), { x: x + 0.22, y: TOP + 0.5, w: colW - 0.44, h: colH - 0.6, fontSize: 14, objectName: `${column.label}: list` });
  });
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 10; presentation/G3_Telco_Evaluation.pdf, section 10; README.md, \"Not built yet\"; docs/HOW_IT_WORKS.md, section 13.");
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, section 10 (not built; what a production pilot would change); presentation/G3_Telco_Evaluation.pdf, section 10 (what a pilot would have to measure); README.md, \"Not built yet\"; docs/HOW_IT_WORKS.md, section 13; docs/EXECUTION_PLAN.md, \"Known gaps after M4\".");
}

const SOURCES = [
  {
    head: "The brief and the two plans",
    lines: [
      "The brief: Group 3, Telco: Outage-Aware Customer Care Agent (PDF, not in the public repository)",
      "G3_Telco_FDE_Action_Plan.pdf, 13 November 2026: written before the build, not in the public repository",
      "The discovery workshop report, 22 November 2026: a simulated workshop",
    ],
  },
  {
    head: "The submission package, 25 November 2026",
    lines: ["presentation/G3_Telco_Problem_Statement.pdf", "presentation/G3_Telco_Architecture.pdf", "presentation/G3_Telco_Evaluation.pdf", "presentation/G3_Telco_Checklists.pdf", "presentation/G3_Telco_ROI.xlsx"],
  },
  {
    head: "The repository, branch dev, release 797ab92",
    lines: [
      "README.md, with the declaration of the AI assistant used",
      "docs/HOW_IT_WORKS.md, docs/EXECUTION_PLAN.md",
      "docs/adr/0001 to 0003: degraded mode, a model beside the guide search, tools over MCP",
      "docs/threat_model.md, regulatory_controls.md, data_inventory.md",
      "src/skylink_care/, src/mock_oss/, tests/",
    ],
  },
  {
    head: "Measurements, all in simulation",
    lines: [
      "evals/reports/: seven evaluations and two live-model runs, 25 November 2026 (UTC)",
      "loadtest/reports/: three surge reports, 16 November 2026 (UTC)",
      "reports/data_profile.md, 15 November 2026",
      "2,902 tests: GitLab pipeline 3116487891 and one laptop, 25 November 2026",
      "presentation/src/assets/screens/manifest.json: 35 screenshots",
    ],
  },
];

/** A20: the sources. */
async function sourcesSlide(pres) {
  const slide = appendixSlide(pres, "A20  Sources: where every figure in this deck comes from");
  const gap = 0.25;
  const colW = (G.CW - gap) / 2;
  const cardH = 1.9;
  SOURCES.forEach((group, index) => {
    const x = G.MX + (index % 2) * (colW + gap);
    const y = TOP + Math.floor(index / 2) * (cardH + 0.16);
    kit.panel(slide, { x, y, w: colW, h: cardH, name: `Sources card ${index + 1}` });
    kit.text(slide, group.head, { x: x + 0.22, y: y + 0.1, w: colW - 0.44, h: 0.32, fontFace: FONT.head, fontSize: 14, bold: true, color: S.teal, valign: "middle", objectName: group.head });
    kit.text(slide, kit.bulletRuns(group.lines, { paraSpaceAfter: 1 }), { x: x + 0.22, y: y + 0.44, w: colW - 0.44, h: cardH - 0.5, fontSize: 12, objectName: `${group.head}: list` });
  });
  const noteY = TOP + 2 * (cardH + 0.16);
  kit.panel(slide, { x: G.MX, y: noteY, w: G.CW, h: G.BODY_B - noteY, fill: S.ink, name: "Stand-ins panel" });
  kit.text(
    slide,
    "SkyLink Telecom is fictional and all data is synthetic. The datasets, the credit policy, the router guides and the mock APIs are stand-ins built by the team, because the supplied ones were not received. Every result in this deck is measured in simulation on them.",
    { x: G.MX + 0.24, y: noteY + 0.06, w: G.CW - 0.48, h: G.BODY_B - noteY - 0.12, fontSize: 14, color: HEX.darkText, valign: "middle", objectName: "Stand-ins" },
  );
  kit.sourceLine(slide, "Each main slide's speaker notes end with the file its figures come from. The deck is built by presentation/src/deck/build_deck.js.");
  sourceNote(slide, "README.md, \"Two things that are stand-ins\" and \"AI assistance\"; the dates of each report are those printed in its line \"Written by the run of\"; presentation/src/assets/screens/manifest.json (35 images, 26 November 2026, Indian time).");
}

module.exports = { slides: [objectionsOneSlide, objectionsTwoSlide, limitsSlide, sourcesSlide] };
