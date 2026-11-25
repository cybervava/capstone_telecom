// Appendix, part 1: the divider, the scorecard, the eight-layer pattern, the full diagram.
// Appendix slides are numbered A1, A2, ... in their titles so a presenter can call one up.

const { S, HEX, G, APPENDIX_SECTION } = require("../lib/theme");
const { LAYOUT } = require("../lib/layouts");
const kit = require("../lib/kit");
const { architecturePng } = require("../lib/assets");
const { appendixSlide, sourceNote } = require("../lib/deck");
const { TESTS } = require("./parts/facts");

const CONTENTS = [
  "A1  Scorecard: every metric against its target",
  "A2  The eight-layer reference pattern",
  "A3  The architecture, in full",
  "A4  Decision records 1 to 4",
  "A5  Decision records 5 to 7, and what is still open",
  "A6  Failure handling, dependency by dependency",
  "A7  The sixteen steps, and what does each",
  "A8  What is kept where",
  "A9  The load test, in detail",
  "A10  The attack suite and the fairness check",
  "A11  The checklists: status and open items",
  "A12  The ROI model: inputs and sensitivity",
  "A13 to A16  Demo backup: four paths in screenshots",
  "A17, A18  Six likely objections, and our answers",
  "A19  What is not built, and what a pilot adds",
  "A20  Sources",
  "A21, A22  After the project review: three changes",
];

/** The divider before the appendix. */
async function dividerSlide(pres) {
  const slide = pres.addSlide({ masterName: LAYOUT.section, sectionTitle: APPENDIX_SECTION });
  slide.addText("Appendix", { placeholder: "title" });
  slide.addText("Reference slides for the questions. They are not part of the eighteen.", { placeholder: "body" });
  const half = Math.ceil(CONTENTS.length / 2);
  [CONTENTS.slice(0, half), CONTENTS.slice(half)].forEach((column, index) => {
    kit.text(
      slide,
      column.map((line, row) => ({ text: line, options: { breakLine: row < column.length - 1, paraSpaceAfter: 9 } })),
      { x: 5.45 + index * 3.72, y: 1.42, w: 3.5, h: 5.0, fontSize: 14, color: HEX.darkText, objectName: `Contents ${index + 1}` },
    );
  });
  sourceNote(slide, "the contents of this deck's appendix.");
}

const MET = { bold: true, color: HEX.okInk };
const CAVEAT = { bold: true, color: HEX.amberInk };

const SCORECARD = [
  ["Outage correctly detected for affected customers", "at least 95%", "97.47% of affected contacts, weighted by kind", "140 contacts replayed on 32 nodes", { text: "MET", options: MET }],
  ["Unnecessary truck rolls cut, in simulation", "at least 50%", "100.0% in what the workflow books: 0 of 3,225. End to end: not measurable here", "38 contacts replayed; baseline 4,450 visits", { text: "MET, CAVEAT", options: CAVEAT }],
  ["Credits match policy", "100%", "4,050 of 4,050 grid cases; 14 of 14 worked out by hand", "every boundary of the placeholder policy", { text: "MET", options: MET }],
  ["Zero duplicate tickets per node", "zero", "Every customer of one node who writes in shares one incident; 1 a node in 8 of 8 playings", "named tests; 72 curveball contacts", { text: "MET, IN TESTS", options: MET }],
  ["Agent stays available during a 10x surge", "stays available", "100.00% of 13,941 and of 14,041 contacts accepted; none turned away", "two half-hour runs, 1 rising to 10 a second", { text: "MET, CAVEAT", options: CAVEAT }],
  ["Complaint reference issued within 2 minutes", "within 120 s", "Slowest 0.253 s, and 0.11 s with the API down; none without one after 120 s", "13,941 and 14,041 surge contacts", { text: "MET", options: MET }],
  ["Curveball: safe degraded mode", "no false all-clear", "8 of 8 playings hold every rule; 0 all-clear messages with the API down under load", "72 contacts; 14,041 surge contacts", { text: "MET", options: MET }],
];

/** A1: the scorecard. */
async function scorecardSlide(pres) {
  const slide = appendixSlide(pres, "A1  Scorecard: every metric of the brief against its target");
  kit.table(slide, ["METRIC (BRIEF P.2)", "TARGET", "RESULT, MEASURED IN SIMULATION", "ON HOW MANY", "STATUS"], SCORECARD, { y: G.APX_BODY_Y, colW: [2.9, 1.45, 4.3, 2.2, 1.283], name: "Scorecard" });
  kit.text(
    slide,
    [
      { text: "The caveats. ", options: { bold: true } },
      { text: "Truck rolls: what staff decide for an insisting customer, no replay can measure. Surge: one machine, the code before one later fix; 99.5% accepted was our own pass line. " },
      { text: "The test suite. ", options: { bold: true } },
      { text: `${TESTS.count} tests, none failed, ${TESTS.coverage} coverage with branches counted (gate ${TESTS.gate}): ${TESTS.where}, commit ${TESTS.commit}, ${TESTS.date}.` },
    ],
    { x: G.MX, y: 5.86, w: G.CW, h: 0.72, fontSize: 12, color: S.ink2, valign: "middle", objectName: "Scorecard reading" },
  );
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Evaluation.pdf, section 1; evals/reports/ (outage_detection, truck_rolls, credit_policy, curveball); loadtest/reports/surge-brief-before-fix.md. Stand-in data.");
  sourceNote(slide, "presentation/G3_Telco_Evaluation.pdf, sections 1, 2.3 and 3.4; evals/reports/outage_detection.md, truck_rolls.md, credit_policy.md, curveball.md (runs of 25 November 2026); loadtest/reports/surge-brief-before-fix.md (16 November 2026); tests/e2e/test_outage_journey.py (every customer of one node shares one incident, each with a reference of their own; the test opens the contacts one after another); tests/integration/test_episodes.py (40 simultaneous reports open one episode); the test suite in GitLab pipeline 3116487891, commit 797ab92, 25 November 2026 (2,902 passed, 95.78%; the same 2,902 on one laptop on commit 772467c).");
}

const LAYERS = [
  ["1  Channels", "A customer chat page, a staff desk, and call transcripts as text from a trusted contact-centre system. Every message of a conversation says it comes from an automated assistant.", "BUILT"],
  ["2  Input and output guardrails", "In: a signed pass, rate limits, masking before any model call, a check for wording aimed at the assistant. Out: pre-written messages, quoted sources, checked claims about the network.", "BUILT"],
  ["3  Orchestrator", "A LangGraph state machine that saves each conversation's progress in PostgreSQL. The supervisor is plain code: 12 decisions a run, then a person.", "BUILT"],
  ["4  Specialist agents", "Triage; troubleshooting with citations; diagnostics, resolution and credit, dispatch; the executor as the Action agent; proactive notification with the feed consumer.", "BUILT"],
  ["5  Human approval gate", "A supervisor approves a credit above $500, bound to the hash of the frozen request and checked again by the executor. Staff can take any conversation over.", "BUILT"],
  ["6  Tools and systems", "Typed connections to six systems, one gateway key for each agent identity. Rules in code. The knowledge index holds the router guides only; the credit policy is a versioned file applied by code.", "BUILT, NO ML"],
  ["7  Data and ingestion", "Streaming: the feed consumer and its picture of the network. Batch: the join from customer to service to node, and the versioned guide index.", "BUILT"],
  ["8  Trust and operations", "A key for each agent, a managed secret store, an audit log the database refuses to rewrite, seven evaluations, one of them an attack suite. Not built: metrics, dashboards, alerts, a runbook.", "IN PART"],
];

/** A2: the eight-layer reference pattern, mapped. */
async function layersSlide(pres) {
  const slide = appendixSlide(pres, "A2  The eight-layer reference pattern: no layer is removed");
  const rows = LAYERS.map(([layer, where, state]) => [
    { text: layer, options: { bold: true } },
    where,
    { text: state, options: { bold: true, color: state === "BUILT" ? HEX.okInk : HEX.amberInk } },
  ]);
  kit.table(slide, ["LAYER IN THE BRIEF", "WHERE IT LIVES IN THIS SYSTEM", "STATE"], rows, { y: G.APX_BODY_Y, colW: [2.45, 8.25, 1.433], name: "Eight layers" });
  kit.sourceLine(slide, "Sources: the brief, p.4-5; presentation/G3_Telco_Architecture.pdf, section 2. The complaint rules are mapped to controls (docs/regulatory_controls.md) and are not quoted to customers.");
  sourceNote(slide, "the brief, p.4 (the eight-layer pattern) and p.5 (its mapping table); presentation/G3_Telco_Architecture.pdf, section 2 (the eight layers; no layer removed; the machine-learning model of layer 6 and the dashboards and alerts of layer 8 are left out and said so); docs/EXECUTION_PLAN.md, section 3; docs/regulatory_controls.md.");
}

const DIAGRAM_NOTES = [
  { head: "Five programs, one image", body: "The API, the orchestrator, the feed consumer, the executor and the MCP server (not drawn) share one PostgreSQL database." },
  { head: "Keys", body: "The API holds no SkyLink key. The specialists hold four read-only keys. The executor holds the only key that writes." },
  { head: "Stand-in", body: "SkyLink's side is one simulator, mock-oss, playing the gateway and the six systems." },
];

/** A3: the architecture diagram of the architecture document, whole. */
async function diagramSlide(pres) {
  const slide = appendixSlide(pres, "A3  The architecture in full, as in the architecture document");
  const imageH = 5.02;
  const imageW = (imageH * 2400) / 1430;
  kit.panel(slide, { x: G.MX, y: 1.52, w: imageW + 0.2, h: imageH + 0.2, shadow: true, name: "Diagram ground" });
  slide.addImage({ path: architecturePng(), x: G.MX + 0.1, y: 1.62, w: imageW, h: imageH, altText: "The care platform between its users and SkyLink's systems: the full architecture diagram", objectName: "Architecture diagram (presentation/src/assets/architecture.png)" });
  const nx = G.MX + imageW + 0.45;
  const nw = G.MX + G.CW - nx;
  DIAGRAM_NOTES.forEach((note, index) => {
    kit.text(
      slide,
      [
        { text: note.head, options: { bold: true, color: S.teal, breakLine: true } },
        { text: note.body },
      ],
      { x: nx, y: 1.62 + index * 1.66, w: nw, h: 1.56, fontSize: 14, objectName: `Diagram note ${index + 1}` },
    );
  });
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, Figure 1 and sections 1 and 4 (presentation/src/assets/architecture.png); docker-compose.yml; docs/contracts/mock_oss_api.md.");
}

module.exports = { slides: [dividerSlide, scorecardSlide, layersSlide, diagramSlide] };
