// Segment 2, approach and architecture (R2, 4 minutes): slides 4 to 7.

const { S, HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { mainSlide, speakerNotes } = require("../lib/deck");
const { architectureDiagram } = require("./parts/architecture_diagram");

const SEGMENT = "approach";
const ROLE = "R2";

// Which tool leads each of the sixteen steps of a contact (architecture document, section 3).
const TALLY = [
  { tool: "Rules in code", steps: 11, hex: HEX.teal },
  { tool: "Retrieval (RAG)", steps: 1, hex: HEX.teal },
  { tool: "Language model", steps: 1, hex: HEX.teal },
  { tool: "Statistics", steps: 1, hex: HEX.teal },
  { tool: "A person", steps: 1, hex: HEX.purple },
  { tool: "Machine learning", steps: 0, hex: HEX.red },
];

const WHY_NOT = [
  {
    icon: "LuCalculator",
    head: "No model computes a credit",
    body: "Money must come out the same for everyone in the same situation: a formula in code, from a versioned policy file.",
    tag: { kind: "measured", text: "MEASURED · 4,050 OF 4,050 CASES MATCH" },
  },
  {
    icon: "LuBot",
    head: "The language model has no tools",
    body: "It reads a message with personal details masked and fills in nine fields. A conversation uses 35 pre-written messages.",
    tag: { kind: "built", text: "BUILT · KEYWORD RULES ARE THE FALLBACK" },
  },
  {
    icon: "LuUsers",
    head: "No churn model",
    body: "No record of who left to learn from, and a risk of unfair offers by segment. One open rule stands in its place.",
    tag: { kind: "notbuilt", text: "NOT BUILT, ON PURPOSE" },
  },
];

/** Slide 4: the split between the language model, rules, the writer and a person. */
async function splitSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "The model reads, rules decide, one program writes, and a person approves the large sums");

  const top = G.BODY_Y;
  const chartW = 5.55;
  kit.panel(slide, { x: G.MX, y: top, w: chartW, h: 4.36, shadow: true, name: "Tally panel" });
  slide.addChart(
    pres.charts.BAR,
    [{ name: "Steps led", labels: TALLY.map((row) => row.tool), values: TALLY.map((row) => row.steps) }],
    kit.chartOptions({
      x: G.MX + 0.1,
      y: top + 0.1,
      w: chartW - 0.2,
      h: 3.62,
      barDir: "bar",
      catAxisOrientation: "maxMin",
      valAxisHidden: true,
      valAxisMinVal: 0,
      valAxisMaxVal: 12,
      valGridLine: { style: "none" },
      chartColors: TALLY.map((row) => row.hex),
      barGapWidthPct: 55,
      showValue: true,
      dataLabelPosition: "outEnd",
      dataLabelFormatCode: "0",
      showLegend: false,
      showTitle: true,
      title: "Which tool leads each of the 16 steps of a contact",
      objectName: "Chart: steps led by each tool",
    }),
  );
  kit.text(slide, "Step 16, who is likely to leave, is led by nothing: no machine-learning model is trained or served.", {
    x: G.MX + 0.25,
    y: top + 3.74,
    w: chartW - 0.5,
    h: 0.52,
    fontSize: 14,
    color: S.ink2,
    valign: "middle",
    objectName: "Tally note",
  });

  const cardX = G.MX + chartW + 0.3;
  const cardW = G.MX + G.CW - cardX;
  const cardH = 1.36;
  for (const [index, why] of WHY_NOT.entries()) {
    const cy = top + index * (cardH + 0.14);
    kit.panel(slide, { x: cardX, y: cy, w: cardW, h: cardH, shadow: true, name: `Why card ${index + 1}` });
    await kit.iconDot(slide, why.icon, cardX + 0.2, cy + 0.2, { d: 0.56 });
    kit.text(slide, why.head, { x: cardX + 0.95, y: cy + 0.13, w: cardW - 1.15, h: 0.32, fontFace: FONT.head, fontSize: 16, bold: true, valign: "middle", objectName: `Why head ${index + 1}` });
    kit.text(slide, why.body, { x: cardX + 0.95, y: cy + 0.47, w: cardW - 1.15, h: 0.52, fontSize: 14, objectName: `Why body ${index + 1}` });
    kit.tag(slide, why.tag.kind, cardX + 0.95, cy + 1.02, { text: why.tag.text });
  }
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 3 (the sixteen steps, tool by tool); evals/reports/credit_policy.md; docs/EXECUTION_PLAN.md, assumption A10.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 60,
    talk: [
      "Your brief sets the principle: the language model reasons and talks, rules compute amounts and eligibility, and only the Action agent writes, after a human gate for high-impact actions. We enforce that in code.",
      "A contact has sixteen steps. Fixed rules lead eleven. Retrieval, the language model, one statistical test and a person lead one each. The sixteenth, churn scoring, is not built.",
      "Three choices to defend. No model computes a credit: a formula in code matched an independent calculation in 4,050 of 4,050 cases. The language model has no tools: it fills in nine fields, and a conversation uses 35 pre-written messages. And no churn model: the data has no record of who left, and treating customers by predicted churn risks unfair offers by segment.",
    ],
    sources: "the brief, p.5 (the principle of the reference architecture); presentation/G3_Telco_Architecture.pdf, sections 1 and 3 (sixteen steps: rules 11, RAG, language model, statistics and a person 1 each, machine learning none; nine fields) and decision record 7; src/skylink_care/guardrails/claims.py (35 message templates); evals/reports/credit_policy.md (4,050 of 4,050); docs/EXECUTION_PLAN.md, A10.",
  });
}

/** Slide 5: the architecture, simplified for the room. The full diagram is in the appendix. */
async function architectureSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Two worlds meet in one database: contacts ask and wait, the network announces");
  architectureDiagram(slide);
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, Figure 1 (the full diagram is in the appendix); docs/EXECUTION_PLAN.md, section 3; docker-compose.yml.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 80,
    talk: [
      "This is the system as built. On the left, customers, the contact centre and staff. In the middle, our care platform in the cloud. On the right, SkyLink's six systems behind a gateway, simulated here because the supplied mock APIs were not received.",
      "Two worlds meet. Customer contacts are request and response, the blue lines. The network speaks as a stream of numbered events, the orange dashes. They meet in one database, and every hand-over between the parts is a row in it.",
      "Four things to notice. One: the API gives the complaint reference after one database write, before any model or SkyLink system is called. Two: the feed consumer applies each network event once. Three: the executor is the only writer; the specialist agents hold read-only keys and can only propose. Four: the staff desk is the human gate for a credit above 500 rupees.",
      "The language model sits outside. It gets masked text, returns nine fields, and is off by default.",
    ],
    sources: "presentation/G3_Telco_Architecture.pdf, sections 1, 2 and 4 and Figure 1; docs/EXECUTION_PLAN.md, section 3; README.md, \"Two things that are stand-ins\" and \"How it is put together\".",
  });
}

const TODAY = [
  { head: "One EC2 host, t3.medium", body: "API · orchestrator · feed consumer · executor, and mock-oss, the stand-in for SkyLink's six systems" },
  { head: "Amazon RDS, PostgreSQL 17", body: "db.t4g.micro, encrypted, reached over TLS; the one vector table is in it" },
  { head: "Around them", body: "Secrets Manager · ECR · HTTPS with one shared sign-in · a pipeline with no stored cloud key" },
];

const PILOT = [
  { head: "ECS on Fargate, two zones", body: "two copies of the API, the orchestrator and the executor behind a load balancer; one feed consumer" },
  { head: "RDS with a standby", body: "plus a managed queue (SQS FIFO) and a stream for the network's events" },
  { head: "A private link to SkyLink", body: "Direct Connect or a VPN to SkyLink's own gateway and six real systems" },
];

/** Slide 6: where it runs, and why. */
async function cloudSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "On AWS in Mumbai today. SkyLink's on-premises systems are simulated; the link is on paper");

  const top = G.BODY_Y;
  const colW = (G.CW - 0.3) / 2;
  const panelH = 3.02;
  const columns = [
    { x: G.MX, label: "AS DEMONSTRATED TODAY", tag: { kind: "built", text: "BUILT · ONE SERVER, ONE DATABASE" }, rows: TODAY, dashed: false },
    { x: G.MX + colW + 0.3, label: "TARGET FOR A PILOT", tag: { kind: "assumption", text: "ON PAPER · NOT BUILT OR TESTED" }, rows: PILOT, dashed: true },
  ];
  for (const column of columns) {
    kit.panel(slide, { x: column.x, y: top, w: colW, h: panelH, line: column.dashed ? HEX.amber : undefined, lineWidth: 1.5, dash: column.dashed ? "dash" : undefined, shadow: !column.dashed, name: `Panel ${column.label}` });
    kit.kicker(slide, column.label, { x: column.x + 0.22, y: top + 0.16, w: 2.6, color: column.dashed ? HEX.amberInk : S.teal });
    kit.tag(slide, column.tag.kind, column.x + colW - 0.22 - kit.tagWidth(column.tag.text), top + 0.155, { text: column.tag.text });
    column.rows.forEach((row, index) => {
      const ry = top + 0.56 + index * 0.8;
      kit.dot(slide, index + 1, column.x + 0.22, ry + 0.06, { d: 0.34, fill: column.dashed ? HEX.amber : S.teal });
      kit.text(
        slide,
        [
          { text: row.head, options: { bold: true, breakLine: true } },
          { text: row.body, options: { color: S.ink2 } },
        ],
        { x: column.x + 0.72, y: ry, w: colW - 0.94, h: 0.76, fontSize: 14, objectName: `${column.label} ${index + 1}` },
      );
    });
  }

  // The choice, and the cost.
  const factY = top + panelH + 0.2;
  const factH = G.BODY_B - factY;
  const facts = [
    { w: 5.4, value: "AWS or Azure", label: "Nothing is tied to one cloud: containers and PostgreSQL with pgvector run the same on Azure. The team had access to AWS: the shortest safe route (client decision D4).", size: 20 },
    { w: 3.2, value: "USD 1.90 a day", label: "the demonstration, at list prices in Mumbai; the model's fees are not in it", size: 20 },
    { w: 0, value: "Pilot cost", label: "not established: it needs SkyLink's real volumes", size: 20 },
  ];
  let fx = G.MX;
  facts.forEach((fact, index) => {
    const fw = fact.w || G.MX + G.CW - fx;
    kit.panel(slide, { x: fx, y: factY, w: fw, h: factH, name: `Fact card ${index + 1}` });
    kit.text(slide, fact.value, { x: fx + 0.2, y: factY + 0.1, w: fw - 0.4, h: 0.34, fontFace: FONT.head, fontSize: fact.size, bold: true, color: index === 2 ? S.red : S.teal, valign: "middle", objectName: `Fact ${index + 1}` });
    kit.text(slide, fact.label, { x: fx + 0.2, y: factY + 0.46, w: fw - 0.4, h: factH - 0.52, fontSize: 14, objectName: `Fact label ${index + 1}` });
    fx += fw + 0.2;
  });
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 7 and decision record 6; README.md, \"The demo on AWS\"; deploy/aws/terraform/; docs/EXECUTION_PLAN.md, section 12 (D4, 22 November 2026).");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 45,
    talk: [
      "Today the demonstration runs on AWS in Mumbai: one server and a managed PostgreSQL database, each in a single zone. For a pilot the design becomes more than one copy of our programs across two zones, a standby database, a managed queue, and a private link to SkyLink's own gateway, because your core systems stay on your premises. That side is on paper only.",
      "Nothing is tied to one cloud; we had access to AWS, so it was our shortest safe route. The demonstration costs about one dollar ninety a day. A pilot cost is not established.",
    ],
    sources: "presentation/G3_Telco_Architecture.pdf, section 7 (Figure 5, AWS or Azure, cost estimate) and decision record 6; README.md, \"The demo on AWS\" (t3.medium, db.t4g.micro, about $1.90 a day); docs/EXECUTION_PLAN.md, section 12, client decision D4 (22 November 2026).",
  });
}

const DECISIONS = [
  {
    record: "RECORD 1 · DESIGN RULE 1",
    icon: "LuTimer",
    name: "Reference first",
    choice: "The API files the case and replies at once, before any model or SkyLink system is called.",
    proof: "Slowest reference in the tenfold surge: 0.253 s. The limit is 120 s.",
    cost: "The answer arrives in a second message. The reference is not yet confirmed as the official complaint number (D5, open).",
  },
  {
    record: "RECORD 3 · DESIGN RULE 5",
    icon: "LuKeyRound",
    name: "One writer",
    choice: "Specialist agents hold read-only keys and propose. The executor holds the only write key, checks again, and acts once.",
    proof: "All 15 attempts to misuse a read-only key are refused.",
    cost: "Actions land a moment after the turn, so the customer hears the outcome in a second message.",
  },
  {
    record: "RECORD 4 · EXECUTION PLAN",
    icon: "LuDatabase",
    name: "Database as the queue",
    choice: "The notes the parts leave each other are rows in the same database as the cases: a note and its case are one write.",
    proof: "13,941 of 13,941 contacts accepted at up to ten a second, on one laptop.",
    cost: "A single point of failure and a ceiling on volume. A managed queue is on paper only.",
  },
];

/** Slide 7: three decisions, and what each cost. */
async function decisionsSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Three design decisions you are likely to question, and what each one cost us");

  const top = G.BODY_Y;
  const gap = 0.25;
  const cardW = (G.CW - 2 * gap) / 3;
  const cardH = 3.96;
  for (const [index, decision] of DECISIONS.entries()) {
    const x = G.MX + index * (cardW + gap);
    kit.panel(slide, { x, y: top, w: cardW, h: cardH, shadow: true, name: `Decision card ${index + 1}` });
    await kit.iconDot(slide, decision.icon, x + 0.22, top + 0.2, { d: 0.56 });
    kit.kicker(slide, decision.record, { x: x + 0.94, y: top + 0.2, w: cardW - 1.1 });
    kit.text(slide, decision.name, { x: x + 0.94, y: top + 0.44, w: cardW - 1.1, h: 0.36, fontFace: FONT.head, fontSize: 18, bold: true, valign: "middle", objectName: `Decision ${index + 1}` });
    kit.text(slide, decision.choice, { x: x + 0.22, y: top + 0.94, w: cardW - 0.44, h: 0.78, fontSize: 14, objectName: `Choice ${index + 1}` });
    kit.tag(slide, "measured", x + 0.22, top + 1.76);
    kit.text(slide, decision.proof, { x: x + 0.22, y: top + 2.05, w: cardW - 0.44, h: 0.5, fontSize: 14, color: S.ink2, objectName: `Proof ${index + 1}` });
    kit.panel(slide, { x: x + 0.14, y: top + 2.64, w: cardW - 0.28, h: 1.18, fill: HEX.amberSoft, name: `Cost panel ${index + 1}` });
    kit.text(
      slide,
      [
        { text: "THE COST  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.amberInk } },
        { text: decision.cost, options: { color: S.ink } },
      ],
      { x: x + 0.26, y: top + 2.72, w: cardW - 0.52, h: 1.02, fontSize: 14, objectName: `Cost ${index + 1}` },
    );
  }
  kit.text(
    slide,
    "The other four records are on slides 4, 6 and 17. All seven are in the appendix, each with its options and its reasons.",
    { x: G.MX, y: top + cardH + 0.14, w: G.CW, h: 0.3, fontSize: 14, color: S.ink2, valign: "middle", objectName: "The other records" },
  );
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 9 (records 1, 3 and 4); loadtest/reports/surge-brief-before-fix.md; evals/reports/attack_suite.md, attack I2. Measured in simulation, on stand-in data.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 55,
    talk: [
      "We recorded seven decisions. Here are three you are likely to question, each with its cost.",
      "Reference first. The API files the case and replies at once, so the two-minute rule depends on our database alone. Slowest reference in the surge: 0.253 seconds. The cost: the answer arrives separately, and the reference is not yet confirmed as the official complaint number.",
      "One writer. Specialists propose and the executor acts, so one key is guarded and 'exactly once' is enforced in one place. The cost: the outcome comes in a second message.",
      "The database is the queue. A note and its case are one write. The cost: a single point of failure and a ceiling on volume.",
    ],
    sources: "presentation/G3_Telco_Architecture.pdf, section 9, records 1, 3 and 4 (decision, options, choice, why, trade-off); loadtest/reports/surge-brief-before-fix.md (0.253 s; 13,941 of 13,941); evals/reports/attack_suite.md, attack I2 (15 key misuses refused); docs/EXECUTION_PLAN.md, section 12, D5 (open).",
  });
}

module.exports = { segment: SEGMENT, slides: [splitSlide, architectureSlide, cloudSlide, decisionsSlide] };
