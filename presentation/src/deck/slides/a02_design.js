// Appendix, part 2: the decision records, failure handling, the sixteen steps, the data.

const { HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { appendixSlide, sourceNote } = require("../lib/deck");

const RECORD_HEAD = ["RECORD", "CHOICE", "WHY", "TRADE-OFF"];
const RECORD_COLS = [2.2, 3.5, 3.3, 3.133];

const RECORDS_A = [
  [
    "1  Reference first\ndesign rule 1",
    "The API files the case and replies at once: the case, its reference and the follow-up work are one transaction.",
    "The reference depends on the database alone. Slowest in the surge: 0.253 s, against a limit of 120 s.",
    "The answer arrives separately. The reference is not yet confirmed as the official complaint number (D5, open).",
  ],
  [
    "2  Unknown, never clear\nADR 0001, the curveball",
    "A third answer: outage, confirmed clear or unknown. Only a fresh live reading proves there is no outage.",
    "An old \"no outage\" is most wrong in an outage's first minutes. 8 of 8 playings: no false all-clear, no visit on old information.",
    "With the API down, a customer on a healthy node gets no router help. A long failure fills the staff queue (D11, open).",
  ],
  [
    "3  One writer\ndesign rule 5",
    "Specialists hold read-only keys and write proposals. The executor holds the only write key and checks each one again.",
    "One key to guard, and one place where \"exactly once\" is enforced. A write whose answer was lost is replayed into one ticket.",
    "The customer hears the outcome in a second message. Inside the orchestrator the specialists share its four read-only keys.",
  ],
  [
    "4  Database as the queue\nexecution plan, section 4",
    "Notes for the orchestrator and proposals for the executor are rows in the same PostgreSQL database as the cases.",
    "A note and the thing it is about are one write. 13,941 of 13,941 contacts accepted at up to ten a second, on one laptop.",
    "A single point of failure and a ceiling on volume. A managed queue, SQS FIFO, is on paper.",
  ],
];

const RECORDS_B = [
  [
    "5  The model fills in a form\ndesign rule 2",
    "The model describes a message in nine fields. Rules decide, and pre-written messages speak. It has no tools.",
    "Its worst mistake is a wrong tick on a form. 58 of 58 attack runs pass (every credit under review), 18 with a scripted model that obeys the attacker.",
    "Conversations are less flexible: 35 fixed conversation messages cannot answer everything, and unusual requests go to a person.",
  ],
  [
    "6  AWS, in Mumbai\nclient decision D4",
    "Amazon Web Services, decided on 22 November 2026. The demonstration runs there for about USD 1.90 a day.",
    "The team had access to AWS: the shortest safe route to a running demonstration. Nothing is tied to one cloud.",
    "One server and one database, each in a single zone. A release stopped the site for 24 s on 22 November.",
  ],
  [
    "7  No churn model\nassumption A10",
    "One open rule: two or more contacts in seven days raise a case in the staff queue. It never changes what is owed.",
    "No record of who left to learn from, and the brief rules out unfair offers by segment. Segments end alike in 8 of 8 situations.",
    "No early warning of churn and no measured churn benefit. No test checks the queue raise yet.",
  ],
];

/**
 * @param {string[][]} records rows of record, choice, why, trade-off
 * @returns {(string|object)[][]} table rows, with the record's name in bold over its origin
 */
function recordRows(records) {
  return records.map(([name, choice, why, cost]) => {
    const [title, origin] = name.split("\n");
    return [
      { text: [{ text: title, options: { bold: true, breakLine: true } }, { text: origin, options: { fontSize: 11, color: HEX.ink2 } }], options: {} },
      choice,
      why,
      cost,
    ];
  });
}

/** A4: decision records 1 to 4. */
async function recordsOneSlide(pres) {
  const slide = appendixSlide(pres, "A4  Decision records 1 to 4: choice, reason and trade-off");
  kit.table(slide, RECORD_HEAD, recordRows(RECORDS_A), { y: G.APX_BODY_Y, colW: RECORD_COLS, name: "Decision records 1 to 4" });
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 9 (each record with its options). Record 2 is a file, docs/adr/0001; the other six are still to be written as files.");
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, section 9, records 1 to 4; docs/adr/0001-degraded-mode-when-the-network-api-fails.md (accepted 16 November 2026); docs/EXECUTION_PLAN.md, sections 2 to 4 and 12 (D5, D11); loadtest/reports/surge-brief-before-fix.md; evals/reports/curveball.md.");
}

const OPEN_DECISIONS = [
  { id: "D3", body: "only customers who made contact are credited" },
  { id: "D5", body: "our reference is the official complaint number" },
  { id: "D9", body: "a customer ID and four digits of the phone identify a visitor" },
  { id: "D10", body: "every waiting line is tested before an outage's end is told" },
  { id: "D11", body: "five minutes before a person takes a contact when Network Monitoring is down" },
];

/** A5: decision records 5 to 7, and what is still with the client. */
async function recordsTwoSlide(pres) {
  const slide = appendixSlide(pres, "A5  Decision records 5 to 7, and what is still with the client");
  kit.table(slide, RECORD_HEAD, recordRows(RECORDS_B), { y: G.APX_BODY_Y, colW: RECORD_COLS, name: "Decision records 5 to 7" });
  const y = 5.02;
  kit.panel(slide, { x: G.MX, y, w: G.CW, h: G.BODY_B - y, fill: HEX.amberSoft, name: "Open decisions panel" });
  kit.text(
    slide,
    [
      { text: "STILL WITH THE CLIENT, AND WHAT IS IN FORCE UNTIL IT IS DECIDED  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.amberInk, breakLine: true } },
      ...OPEN_DECISIONS.flatMap((decision, index) => [
        { text: `${decision.id} `, options: { bold: true } },
        { text: `${decision.body}${index < OPEN_DECISIONS.length - 1 ? " · " : ". D1, D7 and D8 of the execution plan are open too." }` },
      ]),
    ],
    { x: G.MX + 0.2, y: y + 0.08, w: G.CW - 0.4, h: G.BODY_B - y - 0.16, fontSize: 14, valign: "middle", objectName: "Open client decisions" },
  );
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, sections 9 and 10; docs/EXECUTION_PLAN.md, section 12 (the client decisions D1 to D11; the workshop's register numbers its decisions differently).");
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, section 9, records 5 to 7, and section 10 (open questions D3, D5, D9, D10, D11); docs/EXECUTION_PLAN.md, section 12 and assumption A10; evals/reports/attack_suite.md; evals/reports/segment_parity.md; README.md, \"The demo on AWS\" (about $1.90 a day; 24 s of downtime on 22 November 2026); src/skylink_care/guardrails/claims.py (35 templates).");
}

const FAILURES = [
  ["Network Monitoring: status and line tests", "The answer is unknown, never clear. The feed can still prove an outage. No steps and no visit; a person after five looks.", "The reference at once, then: the status cannot be verified right now."],
  ["Network Monitoring: the event feed", "Silent for 90 s, or a number skipped: the feed stops counting as evidence and live readings decide.", "Nothing different. News of an outage's end can be 15 minutes late."],
  ["CRM", "Nobody's account is guessed. The case goes to a person. Outage texts are held, because consent cannot be read.", "The reference, then: a person will take over."],
  ["Billing", "A failed read: no credit is proposed. A write: the identical request is replayed under the same key, so it is applied once.", "The outcome, or that the credit could not be confirmed."],
  ["Field Service", "An unreadable schedule: nothing is written blind. A slot that filled leads to a fresh offer. One open visit for a service.", "A person arranges the repair, or new slots are offered."],
  ["Incident Management", "The reference never depended on it. Registration and the ticket are replayed under the same key until it answers.", "Nothing different."],
  ["Text messages", "Texts are delayed, not lost and not repeated. A text more than 30 minutes old is not sent.", "A notice arrives late or, once stale, not at all."],
  ["Language model provider", "After 8 seconds, or an answer that does not fit the nine fields: keyword rules read the message, and the audit log says so.", "Nothing different. Unusual wording is read more crudely."],
];

/** A6: failure handling for each external dependency. */
async function failuresSlide(pres) {
  const slide = appendixSlide(pres, "A6  Failure handling for each external dependency");
  const rows = FAILURES.map(([dependency, action, seen]) => [{ text: dependency, options: { bold: true } }, action, seen]);
  kit.table(slide, ["IF THIS FAILS", "WHAT THE PLATFORM DOES", "WHAT THE CUSTOMER SEES"], rows, { y: G.APX_BODY_Y, colW: [2.55, 5.95, 3.633], name: "Failure handling" });
  kit.sourceLine(slide, "Calls have deadlines (1 s to connect; 9 s in all for most, 10 s for a line test, 8 s for the model); a failed call is never \"nothing found\". Source: presentation/G3_Telco_Architecture.pdf, section 11.");
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, section 11 (detected by, what the platform does, what the customer sees, and the test or drill for each); docs/HOW_IT_WORKS.md, section 8; evals/reports/curveball.md; loadtest/reports/surge-simulated-model.md. The database is internal: if it is unreachable everything stops, references included, and that was not played as a drill.");
}

const STEPS = [
  ["1", "Take the contact, give a reference · API", "Rules"],
  ["2", "Who is this customer? · Triage", "Rules"],
  ["3", "What do they want? · Triage", "Language model; rules behind it"],
  ["4", "Which network node serves them? · Diagnostics", "Rules"],
  ["5", "Is there an outage for them? · Diagnostics", "Rules"],
  ["6", "Is an undeclared outage starting? · Diagnostics", "Statistics"],
  ["7", "What may the customer be told? · Orchestrator", "Rules"],
  ["8", "Which router steps fit this fault? · Troubleshooting", "Retrieval (RAG)"],
  ["9", "Is a credit owed, and how much? · Resolution", "Rules"],
  ["10", "Pay a credit above INR 500? · Supervisor, executor", "A person"],
  ["11", "Does a technician need to come? · Dispatch, executor", "Rules"],
  ["12", "One repair ticket for each fault · Executor", "Rules"],
  ["13", "Tell customers before they call · Feed consumer, executor", "Rules"],
  ["14", "Sum up a case for staff · Orchestrator", "Rules; one sentence by the model"],
  ["15", "What happens next? · Orchestrator", "Rules"],
  ["16", "Who is likely to leave? · Nobody", "Machine learning: not built"],
];

/** @param {string} tool @returns {object} the colour of a tool's name */
function toolStyle(tool) {
  if (tool.startsWith("A person")) return { bold: true, color: HEX.purpleInk };
  if (tool.startsWith("Machine")) return { bold: true, color: HEX.redInk };
  if (tool.startsWith("Rules")) return { bold: true, color: HEX.tealInk };
  return { bold: true, color: HEX.amberInk };
}

/** A7: the sixteen steps, and the tool that leads each. */
async function stepsSlide(pres) {
  const slide = appendixSlide(pres, "A7  The sixteen steps of a contact, and what leads each");
  const half = STEPS.length / 2;
  const tableW = (G.CW - 0.3) / 2;
  [STEPS.slice(0, half), STEPS.slice(half)].forEach((part, index) => {
    const rows = part.map(([number, step, tool]) => [{ text: number, options: { bold: true, color: HEX.ink2 } }, step, { text: tool, options: toolStyle(tool) }]);
    kit.table(slide, ["", "STEP, AND WHO DOES IT", "LED BY"], rows, { x: G.MX + index * (tableW + 0.3), y: G.APX_BODY_Y, w: tableW, colW: [0.5, 3.3, tableW - 3.8], name: `Steps ${index + 1}` });
  });
  kit.text(slide, "Rules lead eleven steps. Retrieval, the language model, statistics and a person lead one each. The sixteenth is led by nothing: no machine-learning model is trained or served.", {
    x: G.MX,
    y: 6.02,
    w: G.CW,
    h: 0.5,
    fontSize: 14,
    bold: true,
    valign: "middle",
    objectName: "The tally",
  });
  kit.sourceLine(slide, "Source: presentation/G3_Telco_Architecture.pdf, section 3, which also gives, for each step, why this tool, what was rejected, and the evidence or the price.");
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, section 3 (the sixteen steps; rules lead eleven; RAG, a language model, statistics and a person lead one each; machine learning leads none); docs/architecture_design.html (Tools); docs/EXECUTION_PLAN.md, A10 and A17.");
}

const STORES = [
  {
    head: "In the one PostgreSQL 17 database",
    icon: "LuDatabase",
    lines: ["Cases and messages, with the customer's own words", "The phone line a contact centre states", "The work queues: notes, proposals, approvals", "An audit log that can be added to and never changed", "The network picture, reference identifiers, the guide index"],
  },
  {
    head: "The one vector table: guide_chunks",
    icon: "LuSearch",
    lines: ["96 router-guide sections, 12 guides × 8", "Each as text and as 256 numbers (pgvector)", "A fixed hashing embedder: no learned model", "Filtered by role, router model and section in SQL", "No customer data is embedded"],
  },
  {
    head: "Not stored: read through the gateway",
    icon: "LuCable",
    lines: ["The CRM profile: name, registered mobile, email, consent", "Billing's credits on an account", "Read when needed, by the agent whose job it is"],
    extraHead: "In versioned files",
    extra: ["The credit policy and the twelve guides", "The index is rebuilt from them"],
  },
];

/** A8: what is kept where. */
async function storesSlide(pres) {
  const slide = appendixSlide(pres, "A8  What is kept where: one SQL database, one table of vectors");
  const gap = 0.25;
  const colW = (G.CW - 2 * gap) / 3;
  const colH = 3.92;
  for (const [index, store] of STORES.entries()) {
    const x = G.MX + index * (colW + gap);
    kit.panel(slide, { x, y: G.APX_BODY_Y, w: colW, h: colH, shadow: true, name: `Store card ${index + 1}` });
    await kit.iconDot(slide, store.icon, x + 0.22, G.APX_BODY_Y + 0.2, { d: 0.52 });
    kit.text(slide, store.head, { x: x + 0.9, y: G.APX_BODY_Y + 0.14, w: colW - 1.1, h: 0.64, fontFace: FONT.head, fontSize: 16, bold: true, valign: "middle", objectName: store.head });
    kit.text(slide, kit.bulletRuns(store.lines), { x: x + 0.22, y: G.APX_BODY_Y + 0.98, w: colW - 0.44, h: store.extra ? 1.6 : colH - 1.1, fontSize: 14, objectName: `${store.head}: list` });
    if (!store.extra) continue;
    kit.text(slide, store.extraHead, { x: x + 0.22, y: G.APX_BODY_Y + 2.62, w: colW - 0.44, h: 0.36, fontFace: FONT.head, fontSize: 16, bold: true, valign: "middle", objectName: store.extraHead });
    kit.text(slide, kit.bulletRuns(store.extra), { x: x + 0.22, y: G.APX_BODY_Y + 3.04, w: colW - 0.44, h: 0.8, fontSize: 14, objectName: `${store.extraHead}: list` });
  }
  const stripY = G.APX_BODY_Y + colH + 0.18;
  kit.panel(slide, { x: G.MX, y: stripY, w: G.CW, h: G.BODY_B - stripY, fill: HEX.tealSoft, name: "SQL and vectors together" });
  kit.text(
    slide,
    [
      { text: "SQL first, then vectors. ", options: { bold: true } },
      { text: "The query filters by role, router model and safe section. Closeness of vectors then helps rank at most four rows, so each is compared exactly." },
    ],
    { x: G.MX + 0.22, y: stripY + 0.06, w: G.CW - 0.44, h: G.BODY_B - stripY - 0.12, fontSize: 14, valign: "middle", objectName: "SQL first, then vectors" },
  );
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 6; docs/data_inventory.md; README.md, \"Guide retrieval\". On AWS the database is Amazon RDS, encrypted; there is no separate vector database.");
  sourceNote(slide, "presentation/G3_Telco_Architecture.pdf, section 6 (one database, 21 tables and the workflow library's own; one vector table, guide_chunks, 96 sections as text and 256 numbers; what is kept where); docs/data_inventory.md; presentation/G3_Telco_Checklists.pdf, item 2.4 (the search filters by role, router model and section in the query itself).");
}

module.exports = { slides: [recordsOneSlide, recordsTwoSlide, failuresSlide, stepsSlide, storesSlide] };
