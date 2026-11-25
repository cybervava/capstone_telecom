// The architecture, simplified for a slide and drawn with native shapes so it can be edited.
//
// Same parts, names and four kinds of traffic as Figure 1 of the architecture document
// (presentation/src/assets/architecture.png, shown whole in the appendix), with far fewer
// labels. If a part is renamed there, rename it here.

const { S, HEX, FONT } = require("../../lib/theme");
const kit = require("../../lib/kit");

const ASK = { color: HEX.ask, width: 2, dash: "solid" };
const EVENT = { color: HEX.event, width: 2.25, dash: "dash" };
const ACT = { color: HEX.act, width: 3.5, dash: "solid" };
const NOTE = { color: HEX.ink3, width: 1.5, dash: "sysDot" };

// Rows of the care platform, top to bottom.
const ROW1 = { y: 2.46, h: 1.2 }; // API, orchestrator
const DB = { y: 3.98, h: 0.7 };
const ROW3 = { y: 5.02, h: 0.95 }; // staff desk, executor, feed consumer
const COL = { api: 2.93, mid: 5.1, right: 7.37, w: 1.9, end: 9.27 };
const GATEWAY = { x: 10.3, y: 3.5, w: 0.34, h: 2.72 };
const SYSTEMS = ["Network Monitoring", "CRM", "Billing", "Field Service", "Incident Management", "Text messages"];

/**
 * A component box with its name and one or two lines under it.
 * @param {object} slide
 * @param {{x: number, y: number, w: number, h: number, name: string, lines: object[], line?: string, lineWidth?: number}} o
 * @returns {void}
 */
function component(slide, o) {
  kit.panel(slide, { x: o.x, y: o.y, w: o.w, h: o.h, line: o.line || HEX.lineStrong, lineWidth: o.lineWidth || 1, name: `Box ${o.name}` });
  kit.text(slide, [{ text: o.name, options: { fontFace: FONT.head, fontSize: 15, bold: true, breakLine: true } }, ...o.lines], {
    x: o.x + 0.12,
    y: o.y + 0.08,
    w: o.w - 0.24,
    h: o.h - 0.14,
    fontSize: 12,
    objectName: o.name,
  });
}

/**
 * A user of the platform, as a pill.
 * @param {object} slide
 * @param {{y: number, h: number, name: string, sub: string}} o
 * @returns {void}
 */
function user(slide, o) {
  kit.panel(slide, { x: 0.6, y: o.y, w: 1.75, h: o.h, radius: 0.22, line: HEX.lineStrong, name: `User ${o.name}` });
  kit.text(
    slide,
    [
      { text: o.name, options: { fontFace: FONT.head, fontSize: 14, bold: true, breakLine: true } },
      { text: o.sub, options: { fontSize: 11, color: S.ink2 } },
    ],
    { x: 0.66, y: o.y, w: 1.63, h: o.h, align: "center", valign: "middle", objectName: o.name },
  );
}

/**
 * A two-headed dotted link between a part and the database: a note left in the database.
 * @param {object} slide
 * @param {number} x
 * @param {number} y1
 * @param {number} y2
 * @returns {void}
 */
function note(slide, x, y1, y2) {
  kit.line(slide, { x1: x, y1: y1 + 0.02, x2: x, y2: y2 - 0.02, ...NOTE, begin: "triangle", end: "triangle", name: "Notes in the database" });
}

/**
 * Draws the simplified architecture on a main slide's content area.
 * @param {object} slide
 * @returns {void}
 */
function architectureDiagram(slide) {
  // Zones.
  kit.panel(slide, { x: 2.75, y: 2.1, w: 6.7, h: 4.2, fill: HEX.sunken, radius: 0.1, name: "Zone: care platform" });
  kit.text(slide, "CARE PLATFORM · CLOUD (AWS, MUMBAI)", { x: 2.93, y: 2.16, w: 4.5, h: 0.24, fontSize: 10, bold: true, charSpacing: 1, color: S.ink2, valign: "middle", objectName: "Zone label: care platform" });
  kit.panel(slide, { x: 10.2, y: 2.86, w: 2.53, h: 3.44, fill: HEX.sunken, radius: 0.1, line: HEX.ink3, dash: "dash", name: "Zone: SkyLink" });
  kit.text(slide, "SKYLINK · ON-PREMISES", { x: 10.32, y: 2.92, w: 2.3, h: 0.22, fontSize: 10, bold: true, charSpacing: 1, color: S.ink2, valign: "middle", objectName: "Zone label: SkyLink" });
  const tagW = kit.tag(slide, "standin", 10.32, 3.17);
  kit.text(slide, "a simulator here", { x: 10.32 + tagW + 0.08, y: 3.17, w: 1.4, h: 0.25, fontSize: 11, color: S.ink2, valign: "middle", objectName: "Stand-in note" });

  // Users.
  user(slide, { y: 2.46, h: 0.54, name: "Customer", sub: "web chat" });
  user(slide, { y: 3.12, h: 0.54, name: "Contact centre", sub: "call transcripts" });
  user(slide, { y: 5.07, h: 0.86, name: "Staff", sub: "agent · supervisor · operations" });

  // The care platform's parts.
  component(slide, {
    x: COL.api,
    y: ROW1.y,
    w: COL.w,
    h: ROW1.h,
    name: "API",
    lines: [
      { text: "front desk", options: { color: S.ink2, breakLine: true } },
      { text: "reference first, in one database write", options: { bold: true, color: S.teal } },
    ],
  });
  component(slide, {
    x: COL.mid,
    y: ROW1.y,
    w: COL.end - COL.mid,
    h: ROW1.h,
    name: "Orchestrator",
    lines: [
      { text: "coordinator: plain code picks every next step", options: { color: S.ink2, breakLine: true } },
      { text: "Five specialist agents, read-only: triage, diagnostics, troubleshooting (RAG), resolution, dispatch. They propose.", options: {} },
    ],
  });
  component(slide, {
    x: COL.api,
    y: DB.y,
    w: COL.end - COL.api,
    h: DB.h,
    name: "Database",
    lines: [{ text: "case files · PostgreSQL 17 + pgvector · also the queue between the parts", options: { color: S.ink2 } }],
  });
  component(slide, {
    x: COL.api,
    y: ROW3.y,
    w: COL.w,
    h: ROW3.h,
    name: "Staff desk",
    line: S.purple,
    lineWidth: 2.25,
    lines: [
      { text: "the human gate", options: { bold: true, color: S.purple, breakLine: true } },
      { text: "credit above $500", options: { color: S.ink2 } },
    ],
  });
  component(slide, {
    x: COL.mid,
    y: ROW3.y,
    w: COL.w,
    h: ROW3.h,
    name: "Executor",
    line: S.ink,
    lineWidth: 2.5,
    lines: [
      { text: "the hands", options: { color: S.ink2, breakLine: true } },
      { text: "THE ONLY WRITER", options: { bold: true, color: HEX.act } },
    ],
  });
  component(slide, {
    x: COL.right,
    y: ROW3.y,
    w: COL.w,
    h: ROW3.h,
    name: "Feed consumer",
    lines: [
      { text: "the radio", options: { color: S.ink2, breakLine: true } },
      { text: "each event, once", options: { bold: true, color: HEX.event } },
    ],
  });

  // SkyLink's side: the gateway and the six systems behind it.
  slide.addText("API GATEWAY", {
    x: GATEWAY.x,
    y: GATEWAY.y,
    w: GATEWAY.w,
    h: GATEWAY.h,
    shape: "rect",
    fill: { color: S.ink },
    margin: 0,
    fontSize: 10,
    bold: true,
    charSpacing: 2,
    color: HEX.white,
    align: "center",
    valign: "middle",
    vert: "vert270",
    objectName: "API gateway",
  });
  const pitch = GATEWAY.h / SYSTEMS.length;
  SYSTEMS.forEach((system, index) => {
    const sy = GATEWAY.y + index * pitch;
    kit.panel(slide, { x: 10.74, y: sy + 0.03, w: 1.89, h: pitch - 0.06, line: HEX.lineStrong, name: `System ${system}` });
    kit.text(slide, system, { x: 10.8, y: sy + 0.03, w: 1.77, h: pitch - 0.06, fontSize: 12, bold: true, valign: "middle", objectName: system });
  });

  // The language model, outside both.
  kit.panel(slide, { x: 10.2, y: 2.1, w: 2.53, h: 0.62, line: HEX.ink3, dash: "dash", name: "Box language model" });
  kit.text(
    slide,
    [
      { text: "Language model · off by default", options: { bold: true, fontSize: 12, breakLine: true } },
      { text: "masked text in, fields out, no tools", options: { fontSize: 10.5, color: S.ink2 } },
    ],
    { x: 10.28, y: 2.12, w: 2.4, h: 0.58, valign: "middle", objectName: "Language model" },
  );

  // Asks and answers (request-response).
  kit.line(slide, { x1: 2.37, y1: 2.73, x2: COL.api - 0.02, y2: 2.73, ...ASK, begin: "triangle", end: "triangle", name: "Ask: customer" });
  kit.line(slide, { x1: 2.37, y1: 3.39, x2: COL.api - 0.02, y2: 3.39, ...ASK, begin: "triangle", end: "triangle", name: "Ask: contact centre" });
  kit.line(slide, { x1: 2.37, y1: 5.5, x2: COL.api - 0.02, y2: 5.5, ...ASK, begin: "triangle", end: "triangle", name: "Ask: staff" });
  kit.line(slide, { x1: COL.end + 0.02, y1: 2.58, x2: 10.18, y2: 2.58, ...ASK, begin: "triangle", end: "triangle", name: "Ask: language model" });
  kit.line(slide, { x1: COL.end + 0.02, y1: 3.58, x2: GATEWAY.x - 0.02, y2: 3.58, ...ASK, begin: "triangle", end: "triangle", name: "Ask: reads" });
  kit.text(slide, "reads only", { x: COL.end + 0.02, y: 3.3, w: 1.0, h: 0.24, fontSize: 11, bold: true, color: HEX.ask, align: "center", valign: "middle", objectName: "Label: reads only" });

  // Announcements (the event stream).
  kit.line(slide, { x1: GATEWAY.x - 0.02, y1: 5.3, x2: COL.end + 0.03, y2: 5.3, ...EVENT, end: "triangle", name: "Announcements" });
  kit.text(slide, "numbered events", { x: COL.end + 0.02, y: 4.86, w: 1.0, h: 0.4, fontSize: 11, bold: true, color: HEX.event, align: "center", valign: "bottom", objectName: "Label: events" });

  // Actions (the only writes).
  const writeX = COL.mid + COL.w / 2;
  kit.line(slide, { x1: writeX, y1: ROW3.y + ROW3.h, x2: writeX, y2: 6.14, ...ACT, name: "Action: down" });
  kit.line(slide, { x1: writeX - 0.02, y1: 6.14, x2: GATEWAY.x - 0.02, y2: 6.14, ...ACT, end: "triangle", name: "Action: the only writes" });
  kit.text(slide, "the only writes", { x: COL.end + 0.02, y: 5.74, w: 1.0, h: 0.36, fontSize: 11, bold: true, color: HEX.act, align: "center", valign: "bottom", objectName: "Label: writes" });

  // Notes left in the database.
  note(slide, COL.api + COL.w / 2, ROW1.y + ROW1.h, DB.y);
  note(slide, 7.18, ROW1.y + ROW1.h, DB.y);
  note(slide, COL.api + COL.w / 2, DB.y + DB.h, ROW3.y);
  note(slide, writeX, DB.y + DB.h, ROW3.y);
  note(slide, COL.right + COL.w / 2, DB.y + DB.h, ROW3.y);

  // The four things the presenter points at.
  kit.dot(slide, 1, COL.api - 0.13, ROW1.y - 0.13, { d: 0.3, fontSize: 12 });
  kit.dot(slide, 2, COL.right - 0.13, ROW3.y - 0.13, { d: 0.3, fontSize: 12 });
  kit.dot(slide, 3, COL.mid - 0.13, ROW3.y - 0.13, { d: 0.3, fontSize: 12 });
  kit.dot(slide, 4, COL.api - 0.13, ROW3.y - 0.13, { d: 0.3, fontSize: 12 });

  // The legend: four kinds of traffic.
  const legend = [
    { style: ASK, label: "Asks and answers", w: 2.05 },
    { style: EVENT, label: "Announcements, an event stream", w: 3.05 },
    { style: ACT, label: "Actions, the only writes", w: 2.5 },
    { style: NOTE, label: "Notes left in the database", w: 2.6 },
  ];
  let lx = 2.75;
  for (const item of legend) {
    kit.line(slide, { x1: lx, y1: 6.47, x2: lx + 0.5, y2: 6.47, ...item.style, end: "triangle", name: `Legend ${item.label}` });
    kit.text(slide, item.label, { x: lx + 0.6, y: 6.35, w: item.w - 0.7, h: 0.24, fontSize: 11, color: S.ink2, valign: "middle", objectName: `Legend label ${item.label}` });
    lx += item.w;
  }
}

module.exports = { architectureDiagram };
