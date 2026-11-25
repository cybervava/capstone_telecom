// Segment 5, trust and operations (R5, 3 minutes): slides 14 to 17.

const { S, HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { mainSlide, speakerNotes } = require("../lib/deck");
const { CHECKLISTS, checklistTotals } = require("./parts/facts");

const SEGMENT = "trust";
const ROLE = "R5";

const LOCKS = [
  { icon: "LuEyeOff", name: "Input", body: "Personal details are masked before a model reads a message; wording aimed at the assistant is flagged." },
  { icon: "LuBot", name: "Model", body: "No tools. Nine fields or a guide choice come back; nothing it writes reaches a customer." },
  { icon: "LuQuote", name: "Output", body: "35 pre-written conversation messages, steps quoted with their source, a reply's claim about the network checked." },
  { icon: "LuKeyRound", name: "Action", body: "One key for each agent identity. Only the executor writes. Amounts and limits are in code." },
  { icon: "LuUserCheck", name: "Human gate", body: "A supervisor approves the exact frozen credit above $500.", purple: true },
  { icon: "LuRepeat", name: "Loop", body: "At most 12 decisions a run and 30 seconds a step, then a person." },
];

/** Slide 14: the guardrails, and the attack suite. */
async function guardrailsSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "The guardrails are in code, and 61 of 61 attack runs failed to get past them");

  const top = G.BODY_Y;
  const leftW = 7.05;
  kit.panel(slide, { x: G.MX, y: top, w: leftW, h: 4.36, shadow: true, name: "Locks panel" });
  kit.kicker(slide, "THE LOCKS, EACH ENFORCED IN CODE", { x: G.MX + 0.22, y: top + 0.14, w: 4 });
  for (const [index, lock] of LOCKS.entries()) {
    const y = top + 0.5 + index * 0.63;
    await kit.iconDot(slide, lock.icon, G.MX + 0.22, y + 0.04, { d: 0.48, fill: lock.purple ? HEX.purpleSoft : HEX.tealSoft, iconHex: lock.purple ? HEX.purpleInk : HEX.tealInk });
    kit.text(slide, lock.name, { x: G.MX + 0.86, y, w: 1.25, h: 0.56, fontSize: 14, bold: true, color: lock.purple ? S.purple : S.ink, valign: "middle", objectName: `Lock ${lock.name}` });
    kit.text(slide, lock.body, { x: G.MX + 2.1, y, w: leftW - 2.3, h: 0.56, fontSize: 14, valign: "middle", objectName: `Lock detail ${lock.name}` });
  }

  const rx = G.MX + leftW + 0.3;
  const rw = G.MX + G.CW - rx;
  kit.panel(slide, { x: rx, y: top, w: rw, h: 4.36, fill: S.ink, shadow: true, name: "Attack suite panel" });
  kit.kicker(slide, "THEN WE ATTACKED IT", { x: rx + 0.24, y: top + 0.14, w: rw - 0.48, color: HEX.darkAccent });
  kit.stat(slide, {
    x: rx + 0.24,
    y: top + 0.42,
    w: rw - 0.48,
    value: "61 of 61",
    label: "attack runs passed, with no critical failure. 43 attacks; the 18 made of words are played twice, once read by a scripted model that obeys the attacker.",
    size: 48,
    valueH: 0.82,
    labelH: 1.0,
    color: HEX.darkAccent,
    labelColor: HEX.darkText,
  });
  kit.text(
    slide,
    kit.bulletRuns(
      ["First run: 50 of 56. Five gaps found, all closed.", "31 checks remove a control to prove the suite can fail.", "Live model, gpt-5.4-mini: 61 of 61, run again on 25 November."],
      { color: HEX.darkText },
    ),
    { x: rx + 0.24, y: top + 2.36, w: rw - 0.48, h: 1.5, fontSize: 14, objectName: "Attack suite facts" },
  );
  const tagW = kit.tag(slide, "measured", rx + 0.24, top + 3.96);
  kit.text(slide, "run with every credit under review", { x: rx + 0.24 + tagW + 0.1, y: top + 3.96, w: rw - 0.6 - tagW, h: 0.25, fontSize: 12, color: HEX.darkMuted, valign: "middle", objectName: "Attack suite setting" });
  kit.sourceLine(slide, "Sources: evals/reports/attack_suite.md and attack_suite_openai.md (25 November 2026); presentation/G3_Telco_Checklists.pdf, checklist 1. The suite shows that these 43 attacks fail, no more.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 45,
    talk: [
      "Your checklist says limits must be enforced in code, never by a prompt. These are the locks: details masked before a model reads, a model with no tools, pre-written messages with checked claims, one key per agent with one writer, a supervisor for credits above 500 dollars, and a run that stops after twelve decisions.",
      "Then we attacked it. Forty-three attacks, 61 runs, eighteen of them read by a scripted model that obeys the attacker. All 61 pass, with no critical failure. A suite shows these attacks fail. It cannot show that nothing would succeed.",
      "If the panel asks which setting it ran on: the suite ran with every credit under review, which is the code's default. On the setting we ship, a credit up to 500 dollars is applied without a person, and the suite has not been re-cut or run for that setting.",
    ],
    sources: "evals/reports/attack_suite.md (run of 25 November 2026: 43 attacks, 61 runs, 61 passed, 0 critical; every credit under review); evals/reports/attack_suite_openai.md (25 November 2026: 61 of 61 with gpt-5.4-mini, 29 of 29 readings its own; 58 of 58 on 17 November); presentation/G3_Telco_Evaluation.pdf, sections 6.1 to 6.3 (50 of 56 first, five gaps closed, 31 checks); presentation/G3_Telco_Checklists.pdf, items 1.1 to 1.10; src/skylink_care/guardrails/claims.py (35 templates).",
  });
}

const CONTROLS = [
  { icon: "LuKeyRound", head: "Identities and keys", body: "One key per agent identity at the gateway. All 15 misuses of a read-only key refused." },
  { icon: "LuEyeOff", head: "Masking", body: "Personal details are replaced before a model reads a message. Logs are redacted." },
  { icon: "LuScrollText", head: "Audit trail", body: "Every step and its actor, with versions where they apply. The database refuses to rewrite it." },
  { icon: "LuScale", head: "Fairness", body: "Consumer and small-business customers end alike in 8 of 8 situations, 80 customers." },
];

/** Slide 15: security and compliance, with the checklist status. */
async function complianceSlide(pres) {
  const totals = checklistTotals();
  const slide = mainSlide(pres, SEGMENT, `${totals.ticked} of ${totals.items} checklist items are ticked with evidence, ${totals.part} are met in part, ${totals.open} are not ticked`);

  const top = G.BODY_Y;
  const leftW = 6.5;
  kit.panel(slide, { x: G.MX, y: top, w: leftW, h: 3.5, shadow: true, name: "Checklist panel" });
  kit.kicker(slide, "THE BRIEF'S FOUR CHECKLISTS, ITEM BY ITEM", { x: G.MX + 0.22, y: top + 0.14, w: leftW - 0.44 });
  const series = [
    { name: "Ticked", key: "ticked", hex: HEX.ok },
    { name: "Met in part", key: "part", hex: HEX.amber },
    { name: "Not ticked", key: "open", hex: HEX.red },
  ];
  slide.addChart(
    pres.charts.BAR,
    series.map((part) => ({ name: part.name, labels: CHECKLISTS.map((list) => `${list.name} (${list.items})`), values: CHECKLISTS.map((list) => list[part.key]) })),
    kit.chartOptions({
      x: G.MX + 0.1,
      y: top + 0.42,
      w: leftW - 0.2,
      h: 3.0,
      barDir: "bar",
      barGrouping: "stacked",
      catAxisOrientation: "maxMin",
      valAxisHidden: true,
      valAxisMinVal: 0,
      valAxisMaxVal: 10,
      valGridLine: { style: "none" },
      chartColors: series.map((part) => part.hex),
      barGapWidthPct: 55,
      showValue: true,
      dataLabelPosition: "ctr",
      dataLabelColor: HEX.white,
      dataLabelFormatCode: "0;;",
      showLegend: true,
      legendPos: "b",
      showTitle: false,
      objectName: "Chart: checklist status",
    }),
  );

  const rx = G.MX + leftW + 0.3;
  const rw = G.MX + G.CW - rx;
  for (const [index, control] of CONTROLS.entries()) {
    const y = top + index * 0.89;
    kit.panel(slide, { x: rx, y, w: rw, h: 0.8, name: `Control card ${index + 1}` });
    await kit.iconDot(slide, control.icon, rx + 0.16, y + 0.15, { d: 0.5 });
    kit.text(
      slide,
      [
        { text: `${control.head}. `, options: { bold: true } },
        { text: control.body },
      ],
      { x: rx + 0.82, y: y + 0.04, w: rw - 0.98, h: 0.72, fontSize: 14, valign: "middle", objectName: `Control ${control.head}` },
    );
  }

  // The open items, named.
  const openY = top + 3.66;
  const openH = G.BODY_B - openY;
  kit.panel(slide, { x: G.MX, y: openY, w: G.CW, h: openH, name: "Open items panel" });
  const redW = kit.tag(slide, "notbuilt", G.MX + 0.2, openY + 0.12, { text: "NOT TICKED" });
  kit.text(slide, "Alerts (4.4) and the runbook (4.8).", { x: G.MX + 0.3 + redW, y: openY + 0.06, w: 3.6, h: 0.36, fontSize: 14, bold: true, valign: "middle", objectName: "Not ticked" });
  const amberW = kit.tag(slide, "assumption", G.MX + 0.2, openY + 0.47, { text: "MET IN PART" });
  kit.text(slide, "Among the ten: no rotation of the platform's secrets, one database role for every program, retention not enforced, no dashboard.", {
    x: G.MX + 0.3 + amberW,
    y: openY + 0.4,
    w: G.CW - 0.5 - amberW,
    h: 0.38,
    fontSize: 14,
    valign: "middle",
    objectName: "Met in part",
  });
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Checklists.pdf (sections 0 and 6; state at commit 772467c, 25 November 2026); evals/reports/attack_suite.md, attack I2; evals/reports/segment_parity.md.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 40,
    talk: [
      `Your brief gives four checklists, 34 items. ${totals.ticked} are ticked with evidence, ${totals.part} are met in part, and ${totals.open} are not ticked. All ten guardrail items are ticked. Operations readiness is our weakest.`,
      "Four controls carry most of it: a key for each agent identity, masking before any model call, an audit trail the database refuses to rewrite, and a fairness check where consumer and small-business customers end alike in 8 of 8 situations.",
      "What is open is named: alerts and the runbook.",
    ],
    sources: "presentation/G3_Telco_Checklists.pdf, section 0 (34 items: 22 ticked, 10 met in part, 2 not ticked; by checklist 10/0/0, 5/3/0, 4/4/0, 3/3/2) and section 6 (the twelve items not fully ticked); evals/reports/attack_suite.md, attack I2 (15 key misuses refused); evals/reports/segment_parity.md (8 of 8 situations, 80 customers).",
  });
}

const SURGE_MINUTES = ["0", "5", "10", "15", "20", "25", "30"];
const SURGE_RATE = [1, 4, 7, 10, 10, 10, 10]; // contacts a second, as scheduled (loadtest/README.md)

const VISIBLE = ["An audit trail for every contact", "Logs, with contact details masked", "Optional traces, contents hidden", "Backlog line, operations queue"];
const MISSING = ["Metrics", "A dashboard", "Alerts", "A runbook"];

/** Slide 16: the surge, and what can be observed. */
async function surgeSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "It holds in a tenfold surge. Every contact is traceable; there is no dashboard yet");

  const top = G.BODY_Y;
  const leftW = 6.05;
  kit.panel(slide, { x: G.MX, y: top, w: leftW, h: 4.36, shadow: true, name: "Surge panel" });
  kit.kicker(slide, "TEN TIMES THE CONTACTS WITHIN 15 MINUTES: OUR PROFILE", { x: G.MX + 0.22, y: top + 0.14, w: leftW - 0.44 });
  slide.addChart(
    pres.charts.LINE,
    [{ name: "Contacts a second, as scheduled", labels: SURGE_MINUTES, values: SURGE_RATE }],
    kit.chartOptions({
      x: G.MX + 0.1,
      y: top + 0.4,
      w: leftW - 0.2,
      h: 1.9,
      chartColors: [HEX.teal],
      lineSize: 3,
      lineDataSymbol: "none",
      valAxisMinVal: 0,
      valAxisMaxVal: 12,
      valAxisMajorUnit: 4,
      showValue: false,
      showLegend: false,
      showTitle: true,
      title: "Contacts a second offered, by minute of the run",
      objectName: "Chart: the surge profile",
    }),
  );
  kit.stat(slide, { x: G.MX + 0.24, y: top + 2.36, w: 3.1, value: "13,941 of 13,941", label: "contacts accepted in the half hour; none turned away", size: 22, valueH: 0.42, labelH: 0.52 });
  kit.stat(slide, { x: G.MX + 3.6, y: top + 2.36, w: 2.3, value: "0.253 s", label: "slowest complaint reference; the limit is 120 s", size: 22, valueH: 0.42, labelH: 0.52 });
  kit.panel(slide, { x: G.MX + 0.14, y: top + 3.46, w: leftW - 0.28, h: 0.78, fill: HEX.amberSoft, name: "Surge caveat panel" });
  kit.text(
    slide,
    [
      { text: "CAVEAT  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.amberInk } },
      { text: "One laptop, a simulator, no language model, the code before one later fix. After that fix, the same day, a two-minute profile: 1,891 of 1,891." },
    ],
    { x: G.MX + 0.28, y: top + 3.5, w: leftW - 0.56, h: 0.7, fontSize: 14, valign: "middle", objectName: "Surge caveat" },
  );

  const rx = G.MX + leftW + 0.3;
  const rw = G.MX + G.CW - rx;
  const placed = await kit.shot(slide, "13-operations-queue.png", { x: rx, y: top, w: rw, crop: { left: 1250, top: 590, width: 910, height: 370 } });
  kit.caption(slide, "The staff desk signed in as operations: the backlog line and the operations queue (image 13; local stack, 26 November 2026).", { x: rx, y: placed.y + placed.h + 0.05, w: rw, h: 0.4 });
  const listY = placed.y + placed.h + 0.52;
  const listH = G.BODY_B - listY;
  const builtW = 3.5;
  kit.panel(slide, { x: rx, y: listY, w: builtW, h: listH, name: "Built panel" });
  kit.tag(slide, "built", rx + 0.18, listY + 0.12, { text: "BUILT: WHAT WE CAN SEE" });
  kit.text(slide, kit.bulletRuns(VISIBLE, { paraSpaceAfter: 2 }), { x: rx + 0.18, y: listY + 0.44, w: builtW - 0.3, h: listH - 0.5, fontSize: 14, objectName: "What exists" });
  const missX = rx + builtW + 0.18;
  const missW = rx + rw - missX;
  kit.panel(slide, { x: missX, y: listY, w: missW, h: listH, fill: HEX.redSoft, name: "Not built panel" });
  kit.tag(slide, "notbuilt", missX + 0.16, listY + 0.12);
  kit.text(slide, kit.bulletRuns(MISSING, { paraSpaceAfter: 2 }), { x: missX + 0.16, y: listY + 0.44, w: missW - 0.28, h: listH - 0.5, fontSize: 14, objectName: "What does not exist" });
  kit.sourceLine(slide, "Sources: loadtest/reports/surge-brief-before-fix.md (16 November 2026), surge-quick.md; presentation/G3_Telco_Evaluation.pdf, sections 4 and 9; README.md, \"Not built yet\" (milestone M5).");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 45,
    talk: [
      "The brief asks for ten times the contacts in fifteen minutes. Our profile: one contact a second rising to ten, then held. All 13,941 contacts were accepted, and the slowest complaint reference took 0.253 seconds against a limit of 120.",
      "The caveat: one laptop, a simulator, no language model, on the code before one later fix. After it, a two-minute version: 1,891 of 1,891. It is no capacity figure.",
      "On observability, plainly: every contact has an audit trail, and staff have a backlog line and an operations queue. Metrics, a dashboard, alerts and a runbook are not built.",
    ],
    sources: "loadtest/reports/surge-brief-before-fix.md (run of 16 November 2026: 13,941 of 13,941 accepted, 0 turned away, slowest reference 0.253 s; one machine, offline keyword rules); loadtest/README.md (the profile; 99.5% is the team's own pass line, the brief says \"stays available\"); loadtest/reports/surge-quick.md (1,891 of 1,891 after the fix); presentation/G3_Telco_Evaluation.pdf, section 9 (what exists; metrics, dashboard, alerts and runbook not built); screenshots manifest, image 13.",
  });
}

const DEGRADED_RULES = [
  "The reference waits for nothing.",
  "A status that cannot be read is unknown, never clear.",
  "The feed can still prove an outage. Only a live read proves there is none.",
  "No router steps and no visit on a status nobody could read.",
];

/** Slide 17: the failure drill. */
async function drillSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "When Network Monitoring fails mid-outage, unknown is never reported as clear");

  const top = G.BODY_Y;
  const file = "07-curveball-status-not-verified.png";
  const reply = await kit.shot(slide, file, { x: G.MX, y: top, w: 4.4, crop: { left: 596, top: 330, width: 800, height: 480 } });
  const docket = await kit.shot(slide, file, { x: G.MX + 4.6, y: top, w: 2.75, crop: { left: 42, top: 566, width: 470, height: 304 } });
  kit.text(slide, "The reference still came at once. The network chip says: not verified.", { x: docket.x, y: docket.y + docket.h + 0.1, w: 2.75, h: 0.76, fontSize: 14, objectName: "Docket reading" });
  kit.caption(slide, "Image 07, two crops: the Network Monitoring API is refusing every call. Local stack, 26 November 2026.", { x: G.MX, y: reply.y + reply.h + 0.05, w: 7.35, h: 0.26 });

  const lowY = reply.y + reply.h + 0.42;
  const lowH = G.BODY_B - lowY;
  kit.panel(slide, { x: G.MX, y: lowY, w: 3.55, h: lowH, shadow: true, name: "Surge with the API down" });
  kit.stat(slide, { x: G.MX + 0.2, y: lowY + 0.08, w: 3.2, value: "14,041 of 14,041", label: "contacts accepted in the surge with the API down. No message said the network is clear.", size: 20, valueH: 0.38, labelH: 0.72 });
  kit.panel(slide, { x: G.MX + 3.75, y: lowY, w: 3.6, h: lowH, fill: HEX.amberSoft, name: "Cost panel" });
  kit.text(
    slide,
    [
      { text: "THE COST  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.amberInk } },
      { text: "A long failure fills the staff queue: 2,693 of 3,361 contacts on other nodes reached a person in that half hour." },
    ],
    { x: G.MX + 3.91, y: lowY + 0.06, w: 3.28, h: lowH - 0.12, fontSize: 14, valign: "middle", objectName: "The cost" },
  );

  const rx = 8.25;
  const rw = G.MX + G.CW - rx;
  kit.panel(slide, { x: rx, y: top, w: rw, h: 2.56, fill: S.ink, shadow: true, name: "Rules panel" });
  kit.kicker(slide, "DEGRADED MODE · ADR 0001", { x: rx + 0.22, y: top + 0.14, w: rw - 0.44, color: HEX.darkAccent });
  DEGRADED_RULES.forEach((rule, index) => {
    const y = top + 0.46 + index * 0.51;
    kit.dot(slide, index + 1, rx + 0.22, y + 0.07, { d: 0.32, fill: HEX.darkAccent, color: HEX.ink, fontSize: 12 });
    kit.text(slide, rule, { x: rx + 0.68, y, w: rw - 0.88, h: 0.48, fontSize: 14, color: HEX.darkText, valign: "middle", objectName: `Rule ${index + 1}` });
  });
  const proofY = top + 2.7;
  const proofH = G.BODY_B - proofY;
  kit.panel(slide, { x: rx, y: proofY, w: rw, h: proofH, shadow: true, name: "Proof panel" });
  kit.tag(slide, "measured", rx + 0.22, proofY + 0.14, { text: "MEASURED IN SIMULATION" });
  kit.text(slide, "8 of 8 playings", { x: rx + 0.22, y: proofY + 0.42, w: rw - 0.44, h: 0.44, fontFace: FONT.head, fontSize: 24, bold: true, color: S.teal, valign: "middle", objectName: "Figure 8 of 8" });
  kit.text(slide, "72 contacts. Nobody in an outage was told the network is clear, and nobody at all while the status could not be read. No visit without a status read.", {
    x: rx + 0.22,
    y: proofY + 0.88,
    w: rw - 0.44,
    h: proofH - 0.94,
    fontSize: 14,
    objectName: "Proof reading",
  });
  kit.sourceLine(slide, "Sources: docs/adr/0001-degraded-mode-when-the-network-api-fails.md (16 November 2026); evals/reports/curveball.md (25 November 2026); loadtest/reports/surge-brief-before-fix.md.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 50,
    talk: [
      "The failure drill is your curveball: Network Monitoring goes down in the middle of a live outage. You saw it live.",
      "Four rules. The reference waits for nothing. A status that cannot be read is unknown, never clear. The event feed can still prove an outage; only a live read proves there is none. And nothing is done on a status nobody could read.",
      "The proof: eight playings, 72 contacts. Nobody in an outage was told the network is clear, and nobody at all while the status could not be read. Under load, all 14,041 contacts were accepted.",
      "The cost: a long failure fills the staff queue.",
    ],
    sources: "docs/adr/0001-degraded-mode-when-the-network-api-fails.md (accepted 16 November 2026: the rules, the trade-off); evals/reports/curveball.md (run of 25 November 2026: 8 playings, 72 contacts, no reference missing after 2 minutes, nobody in an outage told clear, no visit without a status read); loadtest/reports/surge-brief-before-fix.md (14,041 of 14,041; 0 clear messages; 2,693 of 3,361 handed to a person); docs/EXECUTION_PLAN.md, section 12, D11 (open); screenshots manifest, image 07.",
  });
}

module.exports = { segment: SEGMENT, slides: [guardrailsSlide, complianceSlide, surgeSlide, drillSlide] };
