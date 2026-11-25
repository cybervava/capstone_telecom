// Segment 3, data and knowledge (R3, 3 minutes): slides 8 to 10.

const { S, HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { shotInfo } = require("../lib/assets");
const { mainSlide, speakerNotes } = require("../lib/deck");

const SEGMENT = "data";
const ROLE = "R3";

const PIPELINES = [
  { source: "Data pack", sourceSub: "nodes · address map · services", pipe: "Reference loader", pipeSub: "a batch, at seeding", table: "Reference tables", tableSub: "who is on which node", event: false },
  { source: "Router guides", sourceSub: "12 versioned files", pipe: "Guide index build", pipeSub: "check, split, embed", table: "Guide index", tableSub: "the only vectors", event: false },
  { source: "Event feed", sourceSub: "numbered, from the NOC", pipe: "Feed consumer", pipeSub: "always on, each event once", table: "Network picture", tableSub: "outages and alarms", event: true },
];

/**
 * A box of the pipeline picture: a name and one line under it.
 * @param {object} slide
 * @param {{x: number, y: number, w: number, h: number, head: string, sub: string, fill?: string, line?: string}} o
 * @returns {void}
 */
function stage(slide, o) {
  kit.panel(slide, { x: o.x, y: o.y, w: o.w, h: o.h, fill: o.fill, line: o.line || HEX.lineStrong, name: `Box ${o.head}` });
  kit.text(
    slide,
    [
      { text: o.head, options: { bold: true, fontSize: 14, breakLine: true } },
      { text: o.sub, options: { fontSize: 12, color: S.ink2 } },
    ],
    { x: o.x + 0.1, y: o.y, w: o.w - 0.2, h: o.h, valign: "middle", objectName: o.head },
  );
}

/** Slide 8: the pipelines, and the join from customer to node. */
async function pipelineSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Three pipelines feed one PostgreSQL database; a line test repairs a bad address map");

  const top = G.BODY_Y;
  const rowH = 0.84;
  const pitch = 1.0;
  const col = { source: G.MX, pipe: 2.95, table: 5.5, w: 1.95, tableW: 1.95 };
  kit.kicker(slide, "SOURCES", { x: col.source, y: top + 0.06, w: 1.9 });
  kit.kicker(slide, "PIPELINES", { x: col.pipe, y: top + 0.06, w: 1.9 });
  kit.panel(slide, { x: col.table - 0.18, y: top, w: col.tableW + 0.36, h: 4.36, fill: HEX.sunken, radius: 0.1, name: "The one database" });
  kit.kicker(slide, "ONE DATABASE", { x: col.table, y: top + 0.06, w: 2.0, color: S.ink });
  PIPELINES.forEach((row, index) => {
    const y = top + 0.4 + index * pitch;
    const style = row.event ? { color: HEX.event, width: 2.25, dash: "dash" } : { color: HEX.ink3, width: 1.75, dash: "solid" };
    stage(slide, { x: col.source, y, w: col.w, h: rowH, head: row.source, sub: row.sourceSub });
    stage(slide, { x: col.pipe, y, w: col.w, h: rowH, head: row.pipe, sub: row.pipeSub, fill: HEX.tealSoft, line: HEX.teal });
    stage(slide, { x: col.table, y, w: col.tableW, h: rowH, head: row.table, sub: row.tableSub });
    kit.line(slide, { x1: col.source + col.w + 0.03, y1: y + rowH / 2, x2: col.pipe - 0.03, y2: y + rowH / 2, ...style, end: "triangle", name: `Into ${row.pipe}` });
    kit.line(slide, { x1: col.pipe + col.w + 0.03, y1: y + rowH / 2, x2: col.table - 0.03, y2: y + rowH / 2, ...style, end: "triangle", name: `Out of ${row.pipe}` });
  });
  const caseY = top + 0.4 + 3 * pitch;
  stage(slide, { x: col.table, y: caseY, w: col.tableW, h: rowH, head: "Case tables", sub: "filled as contacts come" });
  kit.text(slide, "Never opened by a pipeline: customers.csv, the policy files, the quarantine folder. No separate vector database.", {
    x: G.MX,
    y: caseY + 0.02,
    w: 4.3,
    h: rowH,
    fontSize: 14,
    color: S.ink2,
    valign: "middle",
    objectName: "What no pipeline opens",
  });

  // The join, and how a line test repairs it.
  const jx = 8.0;
  const jw = G.MX + G.CW - jx;
  kit.panel(slide, { x: jx, y: top, w: jw, h: 4.36, shadow: true, name: "Join panel" });
  kit.kicker(slide, "THE JOIN: CUSTOMER → SERVICE ADDRESS → NODE", { x: jx + 0.22, y: top + 0.16, w: jw - 0.44 });
  kit.stat(slide, {
    x: jx + 0.22,
    y: top + 0.46,
    w: jw - 0.44,
    value: "96.86%",
    label: "of 180,052 broadband services are placed on a node by the address map alone. Mobile: 96.78% of 327,952.",
    size: 44,
    valueH: 0.74,
    labelH: 0.74,
  });
  const tagW = kit.tag(slide, "measured", jx + 0.22, top + 2.04);
  kit.tag(slide, "standin", jx + 0.22 + tagW + 0.1, top + 2.04, { text: "STAND-IN DATA, DEFECTS INJECTED" });
  kit.panel(slide, { x: jx + 0.14, y: top + 2.5, w: jw - 0.28, h: 1.72, fill: HEX.tealSoft, name: "Repair panel" });
  kit.text(
    slide,
    [
      { text: "The other 5,650 broadband services", options: { bold: true, breakLine: true } },
      { text: "2,622 missing, 1,772 ambiguous, 1,256 stale. A remote line test names the node that really serves the line. Mobile has no such test: the status stays unverified and a person takes over.", options: {} },
    ],
    { x: jx + 0.28, y: top + 2.5, w: jw - 0.56, h: 1.72, fontSize: 14, valign: "middle", objectName: "The repair" },
  );
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 6 (Figure 4); reports/data_profile.md (15 November 2026; 5,650 = 180,052 less 174,402); docs/HOW_IT_WORKS.md, section 5.2.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 60,
    talk: [
      "Three pipelines feed one PostgreSQL database. A batch loads the reference data. A second batch builds the guide index: it checks each of the twelve router guides, splits it and embeds it. The third never stops: the feed consumer applies each numbered network event once. There is no separate vector database.",
      "The join that matters is customer, to service address, to network node. On our stand-in data, with mapping defects injected on purpose, the address map alone places 96.86 per cent of 180,052 broadband services.",
      "For the other 5,650, a remote line test names the node that really serves the line. We do not rewrite the map. Mobile has no such test, so that customer is told the status cannot be verified, and a person takes over.",
    ],
    sources: "presentation/G3_Telco_Architecture.pdf, section 6 and Figure 4 (three pipelines, one database); reports/data_profile.md (180,052 broadband services, 174,402 resolved, 96.86%; missing 2,622, ambiguous 1,772, stale 1,256; mobile 96.78% of 327,952); docs/HOW_IT_WORKS.md, section 5.2 (the line test names the serving node; the map is not rewritten); presentation/G3_Telco_Evaluation.pdf, section 2.1 (defects injected on purpose).",
  });
}

const FUNNEL = [
  { count: "96", w: 2.1, head: "Guide sections in the one vector table", body: "12 router guides × 8 sections", step: null },
  { count: "8", w: 1.6, head: "Filter by router", body: "the model on the customer's service record, never what the message says", step: 1 },
  { count: "4", w: 1.15, head: "Filter by section", body: "the four that are safe to follow alone", step: 2 },
  { count: "1", w: 0.7, head: "Rank, then choose", body: "fault keywords, matching words, closeness of vectors; a language model picks one of the four", step: 3 },
];

const INDEX_FACTS = [
  { value: "1 table", label: "of vectors, in the one database (pgvector)" },
  { value: "256", label: "numbers a section, from a fixed hashing embedder: no learned model" },
  { value: "0", label: "customer details in the vectors: only guide text is embedded" },
];

/** Slide 9: retrieval, by design. */
async function retrievalDesignSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Retrieval filters by the customer's own router first, then quotes word for word");

  const top = G.BODY_Y;
  const rowH = 0.66;
  const pitch = 0.76;
  FUNNEL.forEach((row, index) => {
    const y = top + index * pitch;
    slide.addText(row.count, {
      x: G.MX,
      y,
      w: row.w,
      h: rowH,
      shape: "roundRect",
      rectRadius: 0.06,
      fill: { color: index === 0 ? S.ink : S.teal },
      margin: 0,
      fontFace: FONT.head,
      fontSize: 24,
      bold: true,
      color: HEX.white,
      align: "center",
      valign: "middle",
      objectName: `Funnel ${row.count}`,
    });
    kit.text(
      slide,
      [
        { text: row.step ? `${row.step}  ${row.head}` : row.head, options: { bold: true, breakLine: true } },
        { text: row.body, options: { color: S.ink2 } },
      ],
      { x: G.MX + 2.3, y, w: 4.2, h: rowH, fontSize: 14, valign: "middle", objectName: `Funnel step ${index}` },
    );
  });
  const quoteY = top + 4 * pitch;
  slide.addText("“ ”", {
    x: G.MX,
    y: quoteY,
    w: 0.7,
    h: rowH,
    shape: "roundRect",
    rectRadius: 0.06,
    fill: { color: HEX.tealSoft },
    line: { color: HEX.teal, width: 1 },
    margin: 0,
    fontFace: FONT.head,
    fontSize: 20,
    bold: true,
    color: HEX.tealInk,
    align: "center",
    valign: "middle",
    objectName: "Funnel quote",
  });
  kit.text(
    slide,
    [
      { text: "4  Quote, never rewrite", options: { bold: true, breakLine: true } },
      { text: "word for word, with guide, section and version", options: { color: S.ink2 } },
    ],
    { x: G.MX + 2.3, y: quoteY, w: 4.2, h: rowH, fontSize: 14, valign: "middle", objectName: "Funnel step 4" },
  );

  // A real reply, quoted from the customer's own router guide.
  const shotX = 7.35;
  const shotW = G.MX + G.CW - shotX;
  const placed = await kit.shot(slide, "10-router-guide-steps-with-source.png", { x: shotX, y: top, w: shotW, h: 3.36, crop: { left: 590, top: 712, width: 810, height: 648 }, align: "center" });
  kit.caption(slide, "Part of a real reply: steps from the customer's own router guide (local stack, gpt-5.4-mini choosing the section, 26 November 2026).", { x: shotX, y: placed.y + placed.h + 0.06, w: shotW, h: 0.42 });

  // What the index is.
  const factY = 6.0;
  const factW = (G.CW - 0.4) / 3;
  INDEX_FACTS.forEach((fact, index) => {
    const fx = G.MX + index * (factW + 0.2);
    kit.panel(slide, { x: fx, y: factY, w: factW, h: 0.58, name: `Index fact ${index + 1}` });
    kit.text(slide, fact.value, { x: fx + 0.16, y: factY, w: 1.2, h: 0.58, fontFace: FONT.head, fontSize: 18, bold: true, color: S.teal, valign: "middle", objectName: `Index figure ${index + 1}` });
    kit.text(slide, fact.label, { x: fx + (index === 0 ? 1.25 : 0.82), y: factY, w: factW - (index === 0 ? 1.37 : 0.94), h: 0.58, fontSize: 12, valign: "middle", objectName: `Index label ${index + 1}` });
  });
  kit.sourceLine(slide, "Sources: presentation/G3_Telco_Architecture.pdf, section 6 (\"The one vector search, in full\"); README.md, \"Guide retrieval\"; the screenshot is 10-router-guide-steps-with-source.png of the screenshots manifest.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 50,
    talk: [
      "Retrieval is narrow on purpose. The index holds 96 sections: twelve router guides of eight sections.",
      "We filter before we search. First by the router model on the customer's service record, never by what the message says: eight sections remain. Then the four sections that are safe to follow alone. We rank those, and, since the review, a language model chooses among them and points at the words that show why; code checks that its choice was among them and that the words are really there. Then we quote, word for word, with guide, section and version. On the right is a real reply: the customer said it is crawling, and the model chose Slow speed, where the keyword rules alone chose No internet.",
      "No model writes a step. The embedder is fixed, so the index never varies, and only guide text is embedded. The credit policy is a versioned file applied by code.",
    ],
    sources: "presentation/G3_Telco_Architecture.pdf, section 6 (one database, one vector table guide_chunks, 96 sections as text and 256 numbers, the four steps of the search, no customer details in vectors); README.md, \"Guide retrieval\"; presentation/G3_Telco_Evaluation.pdf, section 7 (the build sends one section; the index holds the guide sections only); presentation/src/assets/screens/manifest.json (image 10).",
  });
}

const RETRIEVAL_SETS = [
  { label: "Transcript phrases, in sample", right: 99.07, constant: 81.48 },
  { label: "Author's paraphrases, in sample", right: 96.67, constant: 50.0 },
  { label: "Independent, as first scored: out of sample", right: 81.25, constant: 47.92 },
  { label: "Independent, on today's rules: no longer out of sample", right: 89.58, constant: 47.92 },
  { label: "Independent, a model choosing: 3 router models", right: 100.0, constant: 47.92 },
];

const DETECTION_SPLIT = [
  { name: "Told at contact: 79.33%", value: 79.33, hex: HEX.teal },
  { name: "Held, then told: 18.13%", value: 18.13, hex: "6FB7BD" },
  { name: "Missed, all mobile: 2.53%", value: 2.53, hex: HEX.red },
];

/** Slide 10: what the retrieval and detection evaluations show. */
async function evaluationSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Detection reaches 97.47% against 95%. Retrieval: 81.25% as first scored, out of sample");

  const top = G.BODY_Y;
  const leftW = 6.75;
  kit.panel(slide, { x: G.MX, y: top, w: leftW, h: 4.36, shadow: true, name: "Retrieval panel" });
  kit.kicker(slide, "RETRIEVAL: THE RIGHT SECTION OF THE CUSTOMER'S OWN GUIDE", { x: G.MX + 0.22, y: top + 0.14, w: leftW - 0.44 });
  slide.addChart(
    pres.charts.BAR,
    [
      { name: "Right section, %", labels: RETRIEVAL_SETS.map((set) => set.label), values: RETRIEVAL_SETS.map((set) => set.right) },
      { name: "Always answering \"No internet\", %", labels: RETRIEVAL_SETS.map((set) => set.label), values: RETRIEVAL_SETS.map((set) => set.constant) },
    ],
    kit.chartOptions({
      x: G.MX + 0.1,
      y: top + 0.42,
      w: leftW - 0.2,
      h: 2.72,
      barDir: "bar",
      barGrouping: "clustered",
      catAxisOrientation: "maxMin",
      catAxisLabelFontSize: 11,
      valAxisHidden: true,
      valAxisMinVal: 0,
      valAxisMaxVal: 115,
      valGridLine: { style: "none" },
      chartColors: [HEX.teal, HEX.lineStrong],
      barGapWidthPct: 45,
      showValue: true,
      dataLabelPosition: "outEnd",
      dataLabelFormatCode: "0.00",
      showLegend: true,
      legendPos: "b",
      showTitle: false,
      objectName: "Chart: retrieval by set",
    }),
  );
  kit.text(
    slide,
    [
      { text: "81.25% is 468 of 576 answers, as first scored, out of sample", options: { bold: true } },
      { text: ": 48 descriptions by a separate model instance that never saw the retriever, 12 router models. Floor: 70%. From another router's guide: 0. With gpt-5.4-mini choosing: 144 of 144.", options: {} },
    ],
    { x: G.MX + 0.22, y: top + 3.2, w: leftW - 0.44, h: 1.08, fontSize: 14, valign: "middle", objectName: "Retrieval reading" },
  );

  const rx = G.MX + leftW + 0.3;
  const rw = G.MX + G.CW - rx;
  kit.panel(slide, { x: rx, y: top, w: rw, h: 4.36, fill: S.ink, shadow: true, name: "Detection panel" });
  kit.kicker(slide, "DETECTION: AFFECTED CUSTOMERS TOLD OF THEIR OUTAGE", { x: rx + 0.22, y: top + 0.14, w: rw - 0.44, color: HEX.darkAccent });
  kit.stat(slide, {
    x: rx + 0.22,
    y: top + 0.44,
    w: rw - 0.44,
    value: "97.47%",
    label: "of affected contacts. Target: at least 95%. 140 contacts replayed on 32 nodes, weighted by the mix of 9,000 labelled outage contacts.",
    size: 44,
    valueH: 0.74,
    labelH: 0.98,
    color: HEX.darkAccent,
    labelColor: HEX.darkText,
  });
  slide.addChart(
    pres.charts.BAR,
    DETECTION_SPLIT.map((part) => ({ name: part.name, labels: ["Affected contacts"], values: [part.value] })),
    kit.chartOptions({
      x: rx + 0.1,
      y: top + 2.2,
      w: rw - 0.2,
      h: 1.28,
      barDir: "bar",
      barGrouping: "percentStacked",
      catAxisHidden: true,
      valAxisHidden: true,
      valGridLine: { style: "none" },
      chartColors: DETECTION_SPLIT.map((part) => part.hex),
      barGapWidthPct: 30,
      showValue: false,
      showLegend: true,
      legendPos: "b",
      legendColor: HEX.darkText,
      showTitle: false,
      objectName: "Chart: how affected contacts were told",
    }),
  );
  kit.text(slide, "Every miss is mobile, which has no remote test here: before the NOC declares, such a customer can be told \"no outage reported\".", { x: rx + 0.22, y: top + 3.5, w: rw - 0.44, h: 0.78, fontSize: 14, color: HEX.darkText, valign: "middle", objectName: "Detection reading" });

  const tagW = kit.tag(slide, "measured", G.MX, G.SRC_Y + 0.005, { text: "MEASURED IN SIMULATION" });
  kit.text(slide, "Stand-in data; keyword rules read the messages, except in the last bar (gpt-5.4-mini). Sources: evals/reports/retrieval.md, retrieval_openai.md, outage_detection.md (25 November 2026).", {
    x: G.MX + tagW + 0.12,
    y: G.SRC_Y,
    w: G.CW - tagW - 0.12,
    h: 0.26,
    fontSize: 10,
    color: S.ink2,
    valign: "middle",
    objectName: "Sources",
  });

  speakerNotes(slide, {
    role: ROLE,
    seconds: 70,
    talk: [
      "Two evaluations, both on synthetic stand-in data, with keyword rules reading the messages, except the last bar.",
      "Detection. Of customers whose node is truly in outage, 97.47 per cent are told of it, against a target of 95. That is 140 contacts replayed on 32 nodes, weighted by the mix of 9,000 labelled contacts. 2.53 per cent are missed, all mobile, which has no remote test here: before the NOC declares, such a customer can be told that no outage is reported.",
      "Retrieval. Trust the third bar. A separate model instance, which never saw the retriever, wrote 48 descriptions. Over twelve router models that is 576 answers, and 468 were the right section: 81.25 per cent, as first scored, out of sample, against our floor of 70. We then reworked the rules and the same set scores 89.58, which is no longer out of sample. The last bar is the model the review asked for: choosing among the sections the search found, it got 144 of 144 on three router models. No answer ever came from another router's guide.",
    ],
    sources: "evals/reports/outage_detection.md (run of 25 November 2026: 97.47%, 140 contacts, 32 nodes, weights from 9,000 contacts); presentation/G3_Telco_Evaluation.pdf, section 3.1 (79.33% told at contact, 18.13% held then told, 2.53% missed, all mobile) and section 7; evals/reports/retrieval_openai.md (25 November 2026: 144 of 144 with gpt-5.4-mini on three router models, 129 of 144 by the rules alone); evals/reports/retrieval.md (run of 25 November 2026: 99.07%, 96.67%, 81.25% = 468 of 576 as first scored, 89.58% on today's rules; constant answer 81.48%, 50.0%, 47.92%; floor 70%; none from another router's guide).",
  });
}

module.exports = { segment: SEGMENT, slides: [pipelineSlide, retrievalDesignSlide, evaluationSlide] };
