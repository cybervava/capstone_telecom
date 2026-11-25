// Segment 1, client problem and value (R1, 3 minutes): slides 1 to 3.

const { S, HEX, FONT, G, SEGMENTS } = require("../lib/theme");
const { LAYOUT } = require("../lib/layouts");
const kit = require("../lib/kit");
const { mainSlide, speakerNotes } = require("../lib/deck");

const SEGMENT = "problem";
const ROLE = "R1";

const ROLES = [
  { code: "R1", name: "FDE Lead" },
  { code: "R2", name: "Architect" },
  { code: "R3", name: "Data & RAG" },
  { code: "R4", name: "Agent Engineer" },
  { code: "R5", name: "Platform & Trust" },
];

/** Slide 1: the title. */
async function titleSlide(pres) {
  const slide = pres.addSlide({ masterName: LAYOUT.title, sectionTitle: SEGMENTS[0].section });
  kit.text(slide, "GROUP 3 · TELCO · CAPSTONE PRESENTATION", { x: 0.8, y: 1.45, w: 7.4, h: 0.3, fontSize: 12, bold: true, charSpacing: 2, color: HEX.darkAccent, valign: "middle", objectName: "Kicker" });
  slide.addText("Care that knows what the network knows", { placeholder: "title" });
  slide.addText(
    [
      { text: "SkyLink Telecom: outage-aware customer care", options: { breakLine: true } },
      { text: "30 November 2026 · fictional client, synthetic data" },
    ],
    { placeholder: "subtitle" },
  );

  // The picture: two worlds meet in the care platform, and one writer acts.
  const centre = { x: 10.0, y: 2.55, d: 1.2 };
  const cx = centre.x + centre.d / 2;
  const cy = centre.y + centre.d / 2;
  slide.addShape("ellipse", { x: centre.x, y: centre.y, w: centre.d, h: centre.d, fill: { color: HEX.darkPanel }, line: { color: HEX.darkAccent, width: 2 }, objectName: "Care platform" });
  kit.text(slide, "Care", { x: centre.x, y: cy - 0.2, w: centre.d, h: 0.4, fontFace: FONT.head, fontSize: 18, bold: true, color: HEX.darkText, align: "center", valign: "middle", objectName: "Care platform label" });

  await kit.iconDot(slide, "LuRadioTower", cx - 0.36, 1.1, { d: 0.72, fill: HEX.darkPanel, iconHex: HEX.event });
  kit.line(slide, { x1: cx, y1: 1.86, x2: cx, y2: centre.y - 0.04, color: HEX.event, width: 2.25, dash: "dash", end: "triangle", name: "Announcements" });
  kit.text(slide, "The network announces", { x: cx + 0.5, y: 1.3, w: 2.0, h: 0.3, fontSize: 13, color: HEX.darkMuted, valign: "middle", objectName: "Label announcements" });

  await kit.iconDot(slide, "LuMessageSquare", 8.5, cy - 0.36, { d: 0.72, fill: HEX.darkPanel, iconHex: "8FB4F0" });
  kit.line(slide, { x1: 9.26, y1: cy, x2: centre.x - 0.04, y2: cy, color: "8FB4F0", width: 2.25, begin: "triangle", end: "triangle", name: "Asks and answers" });
  kit.text(slide, "Customers ask", { x: 8.11, y: cy + 0.42, w: 1.5, h: 0.3, fontSize: 13, color: HEX.darkMuted, align: "center", valign: "middle", objectName: "Label asks" });

  await kit.iconDot(slide, "LuWrench", cx - 0.36, 4.5, { d: 0.72, fill: HEX.darkPanel, iconHex: "4CC9A4" });
  kit.line(slide, { x1: cx, y1: centre.y + centre.d + 0.04, x2: cx, y2: 4.46, color: "4CC9A4", width: 3.5, end: "triangle", name: "Actions" });
  kit.text(slide, "One writer acts, once", { x: cx + 0.5, y: 4.7, w: 2.0, h: 0.3, fontSize: 13, color: HEX.darkMuted, valign: "middle", objectName: "Label actions" });

  // The five roles. The team types each presenter's name under the role.
  const w = 2.2;
  const gap = (G.W - 1.6 - ROLES.length * w) / (ROLES.length - 1);
  ROLES.forEach((role, index) => {
    const x = 0.8 + index * (w + gap);
    kit.panel(slide, { x, y: 5.62, w, h: 1.05, fill: HEX.darkPanel, name: `Presenter card ${role.code}` });
    kit.text(
      slide,
      [
        { text: role.code, options: { fontSize: 11, bold: true, charSpacing: 1, color: HEX.darkAccent, breakLine: true } },
        { text: role.name, options: { fontSize: 16, bold: true, color: HEX.darkText } },
      ],
      { x: x + 0.16, y: 5.74, w: w - 0.32, h: 0.82, objectName: `Presenter ${role.code} (type the name here)` },
    );
  });

  speakerNotes(slide, {
    role: ROLE,
    seconds: 20,
    talk: [
      "Hello. We are Group 3. Our client, SkyLink Telecom, is fictional, and all the data is synthetic.",
      "Its Chief Customer Officer asked for care that knows what the network knows. Here is the problem, what we built, a live demonstration, and what we ask.",
    ],
    sources: "the brief, p.1 (the sponsor's words; the client); README.md, \"Two things that are stand-ins\".",
  });
}

const FIGURES = [
  { w: 2.8, value: "3.5 m + 8 m", label: "broadband and mobile customers" },
  { w: 2.6, value: "30%", label: "of outage calls end in a technician visit nobody needed" },
  { w: 2.4, value: "$650", label: "the cost of each of those visits" },
];

const JOURNEY = [
  { head: "A fault", body: "A fibre is cut or a tower fails." },
  { head: "The network team", body: "The operations centre (NOC) knows within minutes." },
  { head: "Customer care", body: "Thousands call. Agents run the same reboot script." },
  { head: "The field", body: "3 calls in 10 end in a visit to a home where nothing is wrong." },
  { head: "Afterwards", body: "Repeat complaints in 7 days are the top churn signal. The regulator times resolution." },
];

/** Slide 2: the pain, in the client's numbers. */
async function painSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "The network team knows in minutes. Care does not, and it costs a visit in 3 calls of 10");

  const top = G.BODY_Y;
  const cardH = 1.8;
  let x = G.MX;
  for (const figure of FIGURES) {
    kit.panel(slide, { x, y: top, w: figure.w, h: cardH, shadow: true, name: `Figure card ${figure.value}` });
    kit.tag(slide, "fact", x + 0.2, top + 0.16, { text: "CASE FACT · BRIEF P.1" });
    kit.stat(slide, { x: x + 0.2, y: top + 0.46, w: figure.w - 0.4, value: figure.value, label: figure.label, size: 30, valueH: 0.5, labelH: 0.74 });
    x += figure.w + 0.244;
  }
  const wideW = G.MX + G.CW - x;
  kit.panel(slide, { x, y: top, w: wideW, h: cardH, fill: S.ink, shadow: true, name: "Figure card $195,000" });
  kit.tag(slide, "measured", x + 0.2, top + 0.16, { text: "DERIVED FROM THE BRIEF" });
  kit.stat(slide, { x: x + 0.2, y: top + 0.46, w: wideW - 0.4, value: "$195,000", label: "wasted for every 1,000 outage contacts: 1,000 × 30% × $650", size: 30, valueH: 0.5, labelH: 0.56, color: HEX.darkAccent, labelColor: HEX.darkText });

  // The before-picture: today's journey, with the break between the network team and care.
  const rowY = 4.5;
  const boxW = 2.12;
  const gapW = (G.CW - JOURNEY.length * boxW) / (JOURNEY.length - 1);
  kit.kicker(slide, "TODAY, BEFORE", { x: G.MX, y: 4.14, w: 3 });
  JOURNEY.forEach((step, index) => {
    const bx = G.MX + index * (boxW + gapW);
    const wasted = index === 3;
    kit.panel(slide, { x: bx, y: rowY, w: boxW, h: 1.38, fill: wasted ? HEX.redSoft : S.white, line: wasted ? HEX.red : HEX.line, name: `Journey step ${index + 1}` });
    kit.text(
      slide,
      [
        { text: step.head, options: { bold: true, color: wasted ? HEX.redInk : S.teal, breakLine: true } },
        { text: step.body, options: { color: S.ink } },
      ],
      { x: bx + 0.14, y: rowY + 0.12, w: boxW - 0.28, h: 1.16, fontSize: 14, objectName: `Journey text ${index + 1}` },
    );
    if (index === JOURNEY.length - 1) return;
    const ax = bx + boxW;
    const broken = index === 1;
    kit.line(slide, { x1: ax + 0.05, y1: rowY + 0.69, x2: ax + gapW - 0.05, y2: rowY + 0.69, color: broken ? HEX.red : HEX.ink3, width: broken ? 2.25 : 1.5, dash: broken ? "sysDash" : "solid", end: broken ? "none" : "triangle", name: broken ? "The break" : "Then" });
    if (broken) kit.dot(slide, "×", ax + gapW / 2 - 0.15, rowY + 0.54, { d: 0.3, fill: S.red, fontSize: 14 });
  });
  const breakX = G.MX + 2 * boxW + gapW + gapW / 2;
  kit.text(slide, "Care is not told", { x: breakX - 1.0, y: rowY + 1.44, w: 2.0, h: 0.26, fontSize: 12, bold: true, color: S.red, align: "center", valign: "middle", objectName: "The break, named" });

  kit.text(
    slide,
    [
      { text: "The sponsor's ask: ", options: { bold: true } },
      { text: "care that “knows what the network knows”, outage contacts resolved automatically, fair service credits offered consistently." },
    ],
    { x: G.MX, y: 6.26, w: G.CW, h: 0.3, fontSize: 14, valign: "middle", objectName: "The sponsor's ask" },
  );
  kit.sourceLine(slide, "Sources: the brief, p.1. $195,000 is derived from its two figures (discovery workshop report, section 1). SkyLink's contact volume is not supplied, so no annual figure is given here.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 80,
    talk: [
      "SkyLink has 3.5 million broadband and 8 million mobile customers. When a fibre is cut or a tower fails, the network operations centre, the NOC, knows within minutes. Customer care is not told.",
      "So thousands of customers call, and agents take each one through the same reboot script. Thirty per cent of those calls end with a technician sent to a home where nothing is wrong, at 650 dollars a visit. That is 195,000 dollars wasted for every thousand outage contacts. The first three figures are from your brief; the fourth is arithmetic on them.",
      "Two more things make it urgent. A repeat complaint within seven days is your top churn signal, and the regulator tracks how long complaints take to resolve.",
      "One caution. SkyLink has not given us its contact volume, so every annual figure you see later is a scenario.",
      "The sponsor's ask is on the last line: care that knows what the network knows, outage contacts resolved automatically, and fair credits offered consistently.",
    ],
    sources: "the brief, p.1 (customers, 30%, $650, churn signal, regulator, the sponsor's ask); discovery workshop report of 22 November 2026, section 1 ($195,000 per 1,000 contacts, derived; annual savings cannot be established without volumes); presentation/G3_Telco_Problem_Statement.pdf, section 1.",
  });
}

const STAKEHOLDERS = [
  { icon: "LuTarget", role: "Chief Customer Officer, sponsor", need: "Outcomes proven, more than a chatbot" },
  { icon: "LuRadioTower", role: "NOC Manager", need: "Authority on outages, freshness and ETA" },
  { icon: "LuHeadphones", role: "Contact Centre Head", need: "Agent adoption and handling time" },
  { icon: "LuTruck", role: "Field Operations Lead", need: "No waste, and no needed visit blocked" },
  { icon: "LuScale", role: "Regulatory Affairs Manager", need: "A reference in 2 minutes; auditable claims" },
  { icon: "LuCable", role: "OSS/BSS Integration Architect", need: "Cross-system identity; API contracts" },
];

const TARGETS = [
  { value: "≥ 95%", metric: "outage detected for affected customers" },
  { value: "≥ 50%", metric: "unnecessary truck rolls cut, in simulation" },
  { value: "100%", metric: "credits match policy" },
  { value: "Zero", metric: "duplicate tickets per node" },
  { value: "10x", metric: "surge: the agent stays available" },
  { value: "2 min", metric: "complaint reference issued" },
];

const SCOPE = [
  { kind: "built", label: "IN", body: "Broadband first: the brief's six capabilities, on mock APIs" },
  { kind: "assumption", label: "THIN", body: "Mobile: a tower outage is told, ticketed and credited" },
  { kind: "notbuilt", label: "OUT", body: "Live telephony, real SMS, production writes, a churn model" },
];

/** Slide 3: who has to be satisfied, and what success means. */
async function stakeholdersSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Six stakeholders to satisfy, six targets to meet, and broadband goes first");

  const top = G.BODY_Y;
  const leftW = 7.55;
  kit.kicker(slide, "WHO HAS TO BE SATISFIED", { x: G.MX, y: top, w: 3.2 });
  kit.tag(slide, "standin", G.MX + 2.62, top, { text: "SIMULATED WORKSHOP: POSITIONS TO TEST, NOT QUOTATIONS" });
  const cellW = (leftW - 0.25) / 2;
  const cellH = 0.98;
  for (const [index, person] of STAKEHOLDERS.entries()) {
    const cx = G.MX + (index % 2) * (cellW + 0.25);
    const cy = top + 0.4 + Math.floor(index / 2) * (cellH + 0.1);
    kit.panel(slide, { x: cx, y: cy, w: cellW, h: cellH, name: `Stakeholder card ${index + 1}` });
    await kit.iconDot(slide, person.icon, cx + 0.14, cy + 0.22, { d: 0.54 });
    kit.text(
      slide,
      [
        { text: person.role, options: { bold: true, breakLine: true } },
        { text: person.need, options: { color: S.ink2 } },
      ],
      { x: cx + 0.82, y: cy + 0.1, w: cellW - 0.92, h: cellH - 0.2, fontSize: 14, valign: "middle", objectName: `Stakeholder ${index + 1}` },
    );
  }

  const rightX = G.MX + leftW + 0.33;
  const rightW = G.MX + G.CW - rightX;
  kit.panel(slide, { x: rightX, y: top, w: rightW, h: 3.74, fill: S.ink, shadow: true, name: "Targets panel" });
  kit.kicker(slide, "WHAT SUCCESS MEANS: THE BRIEF'S SIX TARGETS", { x: rightX + 0.22, y: top + 0.16, w: rightW - 0.44, color: HEX.darkAccent });
  TARGETS.forEach((target, index) => {
    const ty = top + 0.52 + index * 0.53;
    kit.text(slide, target.value, { x: rightX + 0.22, y: ty, w: 0.95, h: 0.5, fontFace: FONT.head, fontSize: 18, bold: true, color: HEX.darkAccent, valign: "middle", objectName: `Target ${index + 1}` });
    kit.text(slide, target.metric, { x: rightX + 1.2, y: ty, w: rightW - 1.4, h: 0.5, fontSize: 14, color: HEX.darkText, valign: "middle", objectName: `Target metric ${index + 1}` });
  });

  // Scope, in one line.
  const scopeY = 5.96;
  const scopeW = (G.CW - 0.5) / 3;
  kit.panel(slide, { x: G.MX, y: scopeY, w: G.CW, h: 0.62, name: "Scope panel" });
  SCOPE.forEach((item, index) => {
    const sx = G.MX + 0.18 + index * (scopeW + 0.07);
    const tagW = kit.tag(slide, item.kind, sx, scopeY + 0.185, { text: item.label });
    kit.text(slide, item.body, { x: sx + tagW + 0.1, y: scopeY + 0.04, w: scopeW - tagW - 0.2, h: 0.54, fontSize: 14, valign: "middle", objectName: `Scope ${item.label}` });
  });
  kit.sourceLine(slide, "Sources: the brief, p.1-2 (stakeholders, targets); discovery workshop report, sections 3 and 10 (needs; the workshop was simulated); README.md, \"Works today\" and \"Not built yet\" (scope).");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 80,
    talk: [
      "Six people have to be satisfied, and today you are playing them. The Chief Customer Officer, our sponsor, wants outcomes proven, more than a chatbot. The NOC Manager owns the truth about outages. The Contact Centre Head needs agents to adopt it. Field Operations wants no waste and no needed visit blocked. Regulatory Affairs needs a reference in two minutes and claims that can be audited. The integration architect cares about identity across systems and the API contracts.",
      "We framed these needs in a simulated workshop. Nobody at SkyLink was interviewed, so they are positions to test with you.",
      "Success is the brief's six targets, on the right. Results come later, with the evidence.",
      "On scope: broadband goes first, because it carries the field cost we can measure. Mobile gets a thin path: a tower outage is told, ticketed and credited. Live telephony, real text messages, production writes and a churn model are out.",
    ],
    sources: "the brief, p.1 (stakeholders) and p.2 (success metrics); discovery workshop report, sections 3 and 10 (simulated concerns and accountabilities) and decision D2 of its register (broadband first); presentation/G3_Telco_Problem_Statement.pdf, sections 2 and 3; README.md, \"Works today\" and \"Not built yet\".",
  });
}

module.exports = { segment: SEGMENT, slides: [titleSlide, painSlide, stakeholdersSlide] };
