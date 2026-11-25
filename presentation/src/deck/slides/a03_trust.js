// Appendix, part 3: the load test, the attack suite and fairness, the checklists, the ROI model.

const { S, HEX, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { appendixSlide, sourceNote } = require("../lib/deck");
const { CHECKLISTS, checklistTotals } = require("./parts/facts");

const SURGE_ROWS = [
  ["Contacts offered (of them on the failed nodes)", "13,941 (9,759)", "14,041 (9,829)"],
  ["Accepted", "100.00%: 13,941 of 13,941", "100.00%: 14,041 of 14,041"],
  ["Turned away by a rate limit; no reference after 120 s", "0; 0", "0; 0"],
  ["Complaint reference: median / 99th / slowest", "0.009 / 0.015 / 0.253 s", "0.009 / 0.014 / 0.11 s"],
  ["First reply: median / 95th / slowest", "0.177 / 1.409 / 1.83 s", "0.14 / 0.3 / 0.437 s"],
  ["Messages saying the network is clear", "3,372, each on a live status read", "0"],
  ["Outage notices sent / skipped (no consent, or too old)", "42,845 / 12,390", "46,613 / 13,303"],
];

const SURGE_LIMITS = [
  "One machine, which also ran the generator, the simulator and PostgreSQL.",
  "No language model; the simulator answers in milliseconds.",
  "The half-hour runs are of 16 November 2026, before the fix to the rule for failing lines, and not repeated since.",
  "No load test was run against the AWS host.",
];

/** A9: the load test, in detail. */
async function loadTestSlide(pres) {
  const slide = appendixSlide(pres, "A9  The load test: both half-hour runs of the tenfold surge");
  const tableW = 7.75;
  const rows = SURGE_ROWS.map(([measure, up, down]) => [{ text: measure, options: { bold: true } }, up, down]);
  kit.table(slide, ["HALF-HOUR RUN, 1 RISING TO 10 CONTACTS A SECOND", "EVERY DEPENDENCY UP", "NETWORK MONITORING DOWN"], rows, { y: G.APX_BODY_Y, w: tableW, colW: [3.35, 2.2, 2.2], name: "Surge runs" });
  kit.text(
    slide,
    [
      { text: "After the fix, the same day: ", options: { bold: true } },
      { text: "the same surge in two minutes a run, 1,891 of 1,891 contacts accepted and answered (893 and 998); not run since. " },
      { text: "A separate two-minute run ", options: { bold: true } },
      { text: "with a 1.5 s stand-in for a model on every turn: 893 of 893, and 382 messages read by the keyword rules instead." },
    ],
    { x: G.MX, y: 5.5, w: tableW, h: 1.0, fontSize: 14, valign: "middle", objectName: "After the fix" },
  );

  const px = G.MX + tableW + 0.3;
  const pw = G.MX + G.CW - px;
  kit.panel(slide, { x: px, y: G.APX_BODY_Y, w: pw, h: G.BODY_B - G.APX_BODY_Y, fill: HEX.amberSoft, name: "Limits panel" });
  kit.kicker(slide, "WHAT THE RUN DOES NOT PROVE", { x: px + 0.22, y: G.APX_BODY_Y + 0.16, w: pw - 0.44, color: HEX.amberInk });
  kit.text(slide, kit.bulletRuns(SURGE_LIMITS, { paraSpaceAfter: 8 }), { x: px + 0.22, y: G.APX_BODY_Y + 0.5, w: pw - 0.44, h: 3.1, fontSize: 14, objectName: "Limits of the load test" });
  kit.text(slide, "The figures say the design holds at ten contacts a second. They are no capacity figure.", { x: px + 0.22, y: G.APX_BODY_Y + 3.7, w: pw - 0.44, h: 1.1, fontSize: 14, bold: true, valign: "middle", objectName: "How to read the load test" });
  kit.sourceLine(slide, "Sources: loadtest/reports/surge-brief-before-fix.md, surge-quick.md, surge-simulated-model.md (16 November 2026); loadtest/README.md; presentation/G3_Telco_Evaluation.pdf, section 4.");
  sourceNote(slide, "loadtest/reports/surge-brief-before-fix.md (run of 16 November 2026, 07:37 UTC; offline keyword rules; one machine); loadtest/reports/surge-quick.md (16 November 2026: 893 and 998 contacts, 1,891 of 1,891); loadtest/reports/surge-simulated-model.md (893 of 893; 382 read by the rules); presentation/G3_Telco_Evaluation.pdf, sections 4.2 to 4.4; presentation/G3_Telco_Checklists.pdf, item 4.6 (no load test against the AWS host).");
}

const FAMILIES = [
  { name: "A  Instructions in a customer's message", runs: 14 },
  { name: "B  Instructions in a retrieved document", runs: 10 },
  { name: "C  A credit nobody approved", runs: 6 },
  { name: "D  A visit on a status nobody could read", runs: 5 },
  { name: "E  Another customer's data", runs: 7 },
  { name: "F  An all-clear without proof", runs: 2 },
  { name: "G  Personal data in a model payload", runs: 2 },
  { name: "H  Abuse, off-topic requests, edge cases", runs: 6 },
  { name: "I  A role or a key reaching past its tools", runs: 3 },
  { name: "J  A model's answer that tries to unlock an action", runs: 3 },
  { name: "K  A model that answers for the guide", runs: 3 },
];

/** A10: the attack suite by family, and the fairness check. */
async function attackSlide(pres) {
  const slide = appendixSlide(pres, "A10  The attack suite, family by family, and the fairness check");
  const leftW = 7.2;
  kit.panel(slide, { x: G.MX, y: G.APX_BODY_Y, w: leftW, h: G.BODY_B - G.APX_BODY_Y, shadow: true, name: "Attack suite panel" });
  slide.addChart(
    pres.charts.BAR,
    [{ name: "Runs passed", labels: FAMILIES.map((family) => family.name), values: FAMILIES.map((family) => family.runs) }],
    kit.chartOptions({
      x: G.MX + 0.1,
      y: G.APX_BODY_Y + 0.1,
      w: leftW - 0.2,
      h: 4.2,
      barDir: "bar",
      catAxisOrientation: "maxMin",
      catAxisLabelFontSize: 11,
      valAxisHidden: true,
      valAxisMinVal: 0,
      valAxisMaxVal: 16,
      valGridLine: { style: "none" },
      chartColors: [HEX.teal],
      barGapWidthPct: 45,
      showValue: true,
      dataLabelPosition: "outEnd",
      dataLabelFormatCode: "0",
      showLegend: false,
      showTitle: true,
      title: "Attack runs passed, by family: 61 of 61, from 43 attacks; critical failures 0",
      objectName: "Chart: attack runs by family",
    }),
  );
  kit.text(slide, "Run of 25 November 2026, with every credit under review (the code's default). The suite has not been run for the shipped setting, which applies a credit up to $500 without review.", {
    x: G.MX + 0.22,
    y: G.APX_BODY_Y + 4.3,
    w: leftW - 0.44,
    h: 0.6,
    fontSize: 12,
    color: S.ink2,
    valign: "middle",
    objectName: "Attack suite setting",
  });

  const rx = G.MX + leftW + 0.3;
  const rw = G.MX + G.CW - rx;
  kit.panel(slide, { x: rx, y: G.APX_BODY_Y, w: rw, h: G.BODY_B - G.APX_BODY_Y, fill: S.ink, shadow: true, name: "Fairness panel" });
  kit.kicker(slide, "THE FAIRNESS CHECK", { x: rx + 0.22, y: G.APX_BODY_Y + 0.16, w: rw - 0.44, color: HEX.darkAccent });
  kit.stat(slide, {
    x: rx + 0.22,
    y: G.APX_BODY_Y + 0.44,
    w: rw - 0.44,
    value: "8 of 8 situations",
    label: "end the same way for consumer and small-business customers: 80 customers, the same messages, handovers, visit decisions and credit rule.",
    size: 26,
    valueH: 0.5,
    labelH: 1.0,
    color: HEX.darkAccent,
    labelColor: HEX.darkText,
  });
  kit.text(
    slide,
    kit.bulletRuns(
      ["The segment is read nowhere in the code: a test checks the source.", "What differs is the amount, which follows the fee. Three days out: 20.00% of the fee on every plan.", "Above $500 a supervisor approves, and small-business plans reach that level sooner.", "Not proven: statistical equality of outcomes."],
      { color: HEX.darkText, paraSpaceAfter: 6 },
    ),
    { x: rx + 0.22, y: G.APX_BODY_Y + 2.1, w: rw - 0.44, h: 2.8, fontSize: 14, objectName: "Fairness detail" },
  );
  kit.sourceLine(slide, "Sources: evals/reports/attack_suite.md (25 November 2026; the counts by family are summed from its rows); evals/reports/segment_parity.md (25 November 2026); tests/unit/test_decision_inputs.py.");
  sourceNote(slide, "evals/reports/attack_suite.md (run of 25 November 2026: 43 attacks, 61 runs, 61 passed, critical failures 0; every credit under review); presentation/G3_Telco_Evaluation.pdf, sections 6.1 and 6.4 (families A to K; segment parity, 8 of 8 situations over 80 customers; three days out, 20.00% of the fee); evals/reports/segment_parity.md; tests/unit/test_decision_inputs.py.");
}

const OPEN_ITEMS = [
  ["2.1  Secrets in a managed store", "Met in part", "No rotation of the platform's own secrets; a laptop keeps them in .env"],
  ["2.2  Least-privilege identity", "Met in part", "One database role for every process"],
  ["2.5  API authentication, rate limiting", "Met in part", "Limits counted in one process; one shared sign-in at the edge; staff sign-in is a stand-in"],
  ["3.1  Data inventory with retention", "Met in part", "Retention periods are proposals; no job enforces them"],
  ["3.2  Privacy principles", "Met in part", "No export or erasure on request; no mapping to the sections of the DPDP Act"],
  ["3.3  Domain rules mapped to controls", "Met in part", "The complaint rules are a placeholder; three mapped rules are not met"],
  ["3.8  AI disclosure", "Met in part", "Proactive outage notices do not say they are automated"],
  ["4.2  Deployment strategy", "Met in part", "Neither blue-green nor canary: a release stops the site (24 s on 22 November 2026)"],
  ["4.3  Tracing and dashboard", "Met in part", "No metrics and no dashboard; the optional traces cover the orchestrator only"],
  ["4.4  Alerts", "Not ticked", "No alert on error rate, latency, cost or a stale feed"],
  ["4.5  Offline set, online plan, drift check", "Met in part", "No online metrics plan and no drift check are written"],
  ["4.8  Runbook", "Not ticked", "No runbook is written"],
];

/** A11: the checklists, with every open item named. */
async function checklistsSlide(pres) {
  const totals = checklistTotals();
  const slide = appendixSlide(pres, "A11  The checklists: status, and every item not fully ticked");
  const byList = CHECKLISTS.map((list) => `${list.name.split(" and ")[0]} ${list.ticked}/${list.part}/${list.open}`).join(" · ");
  kit.text(
    slide,
    [
      { text: `${totals.items} items: ${totals.ticked} ticked, ${totals.part} met in part, ${totals.open} not ticked. `, options: { bold: true } },
      { text: `${byList}.` },
    ],
    { x: G.MX, y: G.APX_BODY_Y, w: G.CW, h: 0.3, fontSize: 14, valign: "middle", objectName: "Checklist counts" },
  );
  const rows = OPEN_ITEMS.map(([item, state, missing]) => [{ text: item, options: { bold: true } }, { text: state.toUpperCase(), options: { bold: true, fontSize: 11, color: state === "Not ticked" ? HEX.redInk : HEX.amberInk, valign: "middle" } }, missing]);
  kit.table(slide, ["ITEM OF THE BRIEF'S CHECKLISTS", "STATE", "WHAT IS MISSING"], rows, { y: G.APX_BODY_Y + 0.44, colW: [3.75, 1.25, 7.133], name: "Open checklist items" });
  kit.sourceLine(slide, "Source: presentation/G3_Telco_Checklists.pdf, sections 0 and 6 (state at commit 772467c, 25 November 2026), where each item also says what would close it. All ten guardrail items are ticked.");
  sourceNote(slide, "presentation/G3_Telco_Checklists.pdf, section 0 (34 items; 22 ticked, 10 met in part, 2 not ticked; guardrails 10/0/0, security 5/3/0, compliance and responsible AI 4/4/0, operations readiness 3/3/2) and section 6 (the twelve items not fully ticked, with what is missing and what would close each); README.md, \"The demo on AWS\" (24 s of downtime at the release of 22 November 2026).");
}

const ROI_INPUTS = [
  ["Outage calls ending in an unnecessary visit", "30%", "Case fact, brief p.1"],
  ["Cost of one technician visit", "$650", "Case fact, brief p.1"],
  ["Outage calls a month, pilot population", "10,000", "Assumption: replace with the observed baseline"],
  ["Reduction in unnecessary visits", "50%", "Assumption, set equal to the brief's target"],
  ["Calls avoided, and the cost of a call", "20%, $60", "Assumption"],
  ["Running cost a month", "$400,000", "Assumption, not a cloud quote"],
  ["One-time implementation cost", "$1,200,000", "Assumption"],
  ["Repeat-contact saving; churn benefit", "$0; $0", "Held at zero until a pilot can support them"],
];

const SENSITIVITY = [
  { reduction: "25% fewer", net: 207500 },
  { reduction: "50% fewer (base)", net: 695000 },
  { reduction: "75% fewer", net: 1182500 },
];

/** A12: the ROI model: inputs, assumptions, sensitivity. */
async function roiSlide(pres) {
  const slide = appendixSlide(pres, "A12  The ROI model: two case facts, the rest assumptions");
  const tableW = 7.3;
  const rows = ROI_INPUTS.map(([input, value, status]) => [input, { text: value, options: { bold: true } }, { text: status, options: { color: status.startsWith("Case fact") ? HEX.ink : HEX.amberInk } }]);
  kit.table(slide, ["INPUT", "BASE CASE", "STATUS"], rows, { y: G.APX_BODY_Y, w: tableW, colW: [3.1, 1.45, 2.75], name: "ROI inputs" });

  const cx = G.MX + tableW + 0.3;
  const cw = G.MX + G.CW - cx;
  kit.panel(slide, { x: cx, y: G.APX_BODY_Y, w: cw, h: 2.9, shadow: true, name: "Sensitivity panel" });
  slide.addChart(
    pres.charts.BAR,
    [{ name: "Net benefit a month, US dollars", labels: SENSITIVITY.map((row) => row.reduction), values: SENSITIVITY.map((row) => row.net) }],
    kit.chartOptions({
      x: cx + 0.1,
      y: G.APX_BODY_Y + 0.08,
      w: cw - 0.2,
      h: 2.74,
      barDir: "col",
      valAxisHidden: true,
      valAxisMinVal: 0,
      valAxisMaxVal: 1400000,
      valGridLine: { style: "none" },
      chartColors: [HEX.amber],
      barGapWidthPct: 60,
      showValue: true,
      dataLabelPosition: "outEnd",
      dataLabelFormatCode: "#,##0",
      showLegend: false,
      showTitle: true,
      title: "Net benefit a month in US dollars, by cut in wasted visits (a scenario)",
      objectName: "Chart: sensitivity of the net benefit",
    }),
  );
  kit.text(
    slide,
    [
      { text: "Base case: ", options: { bold: true } },
      { text: "$975,000 from avoided visits and $120,000 from avoided calls, less $400,000 to run: net $695,000 a month, payback 1.73 months. " },
      { text: "Break-even: ", options: { bold: true } },
      { text: "a 14.4% cut, or 3,653 calls a month. At 1,000 calls a month the net is negative." },
    ],
    { x: cx, y: G.APX_BODY_Y + 3.0, w: cw, h: 1.3, fontSize: 14, objectName: "Base case and break-even" },
  );
  kit.tag(slide, "measured", cx, G.APX_BODY_Y + 4.36, { text: "MEASURED IN SIMULATION" });
  kit.text(slide, "0 of 28 unneeded visits booked; 10 of 10 needed ones. No dollar saving is measured.", { x: cx + 2.2, y: G.APX_BODY_Y + 4.28, w: cw - 2.2, h: 0.5, fontSize: 12, color: S.ink2, valign: "middle", objectName: "What is measured" });
  kit.text(slide, "A scenario, not a forecast: fictional client, synthetic data. The demonstration's cloud cost, $1.90 a day, is kept apart and is in no benefit figure.", {
    x: G.MX,
    y: 5.82,
    w: tableW,
    h: 0.7,
    fontSize: 14,
    color: S.ink2,
    valign: "middle",
    objectName: "How to read the model",
  });
  kit.sourceLine(slide, "Source: presentation/G3_Telco_ROI.xlsx (sheets Inputs, Baseline, Benefits, Costs, Sensitivity, Simulation evidence); the break-even figures are derived on the model's assumptions.");
  sourceNote(slide, "presentation/G3_Telco_ROI.xlsx: Inputs!B9, B10, B15 to B18 (30%, $650, 10,000 calls, 50%, 20%, $60), Costs!B7 and B13 ($1,200,000; $400,000), Benefits!B11, B19, B25, B30, B35, B38 ($975,000; $120,000; zero; zero; net $695,000; payback 1.73 months), Sensitivity!B5:D8 ($207,500; 695,000; 1,182,500), B22, B23 (14.4%; 3,653 calls) and B12:F16 (negative at 1,000 calls), Simulation evidence (0 of 28; 10 of 10), Costs!B23 ($1.90 a day); the team's action plan, p.10, and the discovery report, section 6, which the base case reproduces.");
}

module.exports = { slides: [loadTestSlide, attackSlide, checklistsSlide, roiSlide] };
