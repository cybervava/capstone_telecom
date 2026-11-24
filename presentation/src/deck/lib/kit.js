// Building blocks every slide uses: text, panels, evidence tags, big figures, arrows,
// screenshots, tables and charts, all in the deck's palette. Each call builds fresh option
// objects, because pptxgenjs changes the ones it is given.

const { S, HEX, FONT, G, PT } = require("./theme");
const { iconData, shotData, shotInfo } = require("./assets");

// Evidence tags, as in the documents (presentation/src/pdf/base.css, .tag--*).
const TAGS = {
  measured: { text: "MEASURED", fill: HEX.tealSoft, color: HEX.tealInk },
  met: { text: "MET", fill: HEX.okSoft, color: HEX.okInk },
  built: { text: "BUILT", fill: HEX.okSoft, color: HEX.okInk },
  assumption: { text: "ASSUMPTION", fill: HEX.amberSoft, color: HEX.amberInk },
  notbuilt: { text: "NOT BUILT", fill: HEX.redSoft, color: HEX.redInk },
  human: { text: "HUMAN GATE", fill: HEX.purpleSoft, color: HEX.purpleInk },
  standin: { text: "STAND-IN", fill: HEX.white, color: HEX.ink2, dashed: true },
  fact: { text: "CASE FACT", fill: HEX.white, color: HEX.ink2, outlined: true },
};

/**
 * A text box. Left-aligned, top-anchored, no inner padding unless asked.
 * @param {object} slide
 * @param {string|object[]} content a string, or pptxgenjs text runs
 * @param {object} options x, y, w, h and any pptxgenjs text option
 * @returns {void}
 */
function text(slide, content, options) {
  slide.addText(content, { margin: 0, fontSize: PT.body, color: S.ink, valign: "top", isTextBox: true, ...options });
}

/**
 * A panel: a plain rectangle, white unless told otherwise.
 * @param {object} slide
 * @param {{x: number, y: number, w: number, h: number, fill?: string, line?: string, lineWidth?: number, dash?: string, radius?: number, shadow?: boolean, name?: string}} o
 * @returns {void}
 */
function panel(slide, o) {
  const shape = {
    x: o.x,
    y: o.y,
    w: o.w,
    h: o.h,
    rectRadius: o.radius === undefined ? 0.06 : o.radius,
    fill: o.fill === "none" ? { type: "none" } : { color: o.fill || S.white },
    objectName: o.name || "Panel",
  };
  if (o.line) shape.line = { color: o.line, width: o.lineWidth || 1, dashType: o.dash || "solid" };
  if (o.shadow) shape.shadow = { type: "outer", color: HEX.ink, opacity: 0.1, blur: 5, offset: 1.5, angle: 90 };
  slide.addShape("roundRect", shape);
}

/** @param {string} label @returns {number} the width of a tag with that label, in inches */
function tagWidth(label) {
  return label.length * 0.087 + 0.26;
}

/**
 * An evidence tag: MEASURED, ASSUMPTION, NOT BUILT, STAND-IN, HUMAN GATE and their kin.
 * @param {object} slide
 * @param {keyof TAGS} kind
 * @param {number} x
 * @param {number} y
 * @param {{text?: string}} [o] another label in the same colours
 * @returns {number} the tag's width, in inches
 */
function tag(slide, kind, x, y, o = {}) {
  const style = TAGS[kind];
  const label = o.text || style.text;
  const w = tagWidth(label);
  const shape = {
    x,
    y,
    w,
    h: 0.25,
    shape: "roundRect",
    rectRadius: 0.05,
    fill: { color: style.fill },
    margin: 0,
    fontSize: PT.tag,
    bold: true,
    charSpacing: 0.5,
    color: style.color,
    align: "center",
    valign: "middle",
    objectName: `Tag ${label}`,
  };
  if (style.dashed) shape.line = { color: HEX.ink3, width: 0.75, dashType: "dash" };
  if (style.outlined) shape.line = { color: HEX.lineStrong, width: 0.75 };
  slide.addText(label, shape);
  return w;
}

/**
 * A big figure with the words that qualify it underneath.
 * @param {object} slide
 * @param {{x: number, y: number, w: number, value: string, label: string|object[], size?: number, color?: string, labelSize?: number, valueH?: number, labelH?: number, labelColor?: string}} o
 * @returns {void}
 */
function stat(slide, o) {
  const size = o.size || 44;
  const valueH = o.valueH || (size * 1.25) / 72;
  text(slide, o.value, { x: o.x, y: o.y, w: o.w, h: valueH, fontFace: FONT.head, fontSize: size, bold: true, color: o.color || S.teal, valign: "middle", objectName: `Figure ${o.value}` });
  text(slide, o.label, { x: o.x, y: o.y + valueH + 0.04, w: o.w, h: o.labelH || 0.75, fontSize: o.labelSize || PT.body, color: o.labelColor || S.ink, objectName: `Figure label ${o.value}` });
}

/**
 * The source line at the foot of a slide's content.
 * @param {object} slide
 * @param {string} sources
 * @param {{dark?: boolean}} [o]
 * @returns {void}
 */
function sourceLine(slide, sources, o = {}) {
  text(slide, sources, { x: G.MX, y: G.SRC_Y, w: G.CW, h: 0.26, fontSize: 10, color: o.dark ? HEX.darkMuted : S.ink2, valign: "middle", objectName: "Sources" });
}

/**
 * A straight line from one point to another, with arrowheads if asked.
 * @param {object} slide
 * @param {{x1: number, y1: number, x2: number, y2: number, color?: string, width?: number, dash?: string, begin?: string, end?: string, name?: string}} o
 * @returns {void}
 */
function line(slide, o) {
  slide.addShape("line", {
    x: Math.min(o.x1, o.x2),
    y: Math.min(o.y1, o.y2),
    w: Math.abs(o.x2 - o.x1),
    h: Math.abs(o.y2 - o.y1),
    flipH: o.x2 < o.x1,
    flipV: o.y2 < o.y1,
    line: { color: o.color || HEX.ink3, width: o.width || 1.5, dashType: o.dash || "solid", beginArrowType: o.begin || "none", endArrowType: o.end || "none" },
    objectName: o.name || "Line",
  });
}

/**
 * A filled circle with a number or a short mark in it.
 * @param {object} slide
 * @param {string|number} mark
 * @param {number} x
 * @param {number} y
 * @param {{d?: number, fill?: string, color?: string, fontSize?: number, line?: string}} [o]
 * @returns {void}
 */
function dot(slide, mark, x, y, o = {}) {
  const d = o.d || 0.36;
  const shape = {
    x,
    y,
    w: d,
    h: d,
    shape: "ellipse",
    fill: { color: o.fill || S.teal },
    margin: 0,
    fontFace: FONT.head,
    fontSize: o.fontSize || 13,
    bold: true,
    color: o.color || HEX.white,
    align: "center",
    valign: "middle",
    objectName: `Mark ${mark}`,
  };
  if (o.line) shape.line = { color: o.line, width: 1 };
  slide.addText(String(mark), shape);
}

/**
 * An icon in a coloured circle.
 * @param {object} slide
 * @param {string} name a Lucide icon name
 * @param {number} x
 * @param {number} y
 * @param {{d?: number, fill?: string, iconHex?: string}} [o]
 * @returns {Promise<void>}
 */
async function iconDot(slide, name, x, y, o = {}) {
  const d = o.d || 0.5;
  slide.addShape("ellipse", { x, y, w: d, h: d, fill: { color: o.fill || HEX.tealSoft }, objectName: `Icon ground ${name}` });
  const inset = d * 0.24;
  slide.addImage({ data: await iconData(name, o.iconHex || HEX.tealInk), x: x + inset, y: y + inset, w: d - 2 * inset, h: d - 2 * inset, objectName: `Icon ${name}` });
}

/**
 * A real screenshot (or a crop of one) in a thin frame, fitted inside a box.
 * @param {object} slide
 * @param {string} file the screenshot's file name
 * @param {{x: number, y: number, w: number, h?: number, crop?: {left: number, top: number, width: number, height: number}, align?: "left"|"center"}} o
 * @returns {Promise<{x: number, y: number, w: number, h: number}>} where the picture was placed
 */
async function shot(slide, file, o) {
  const picture = await shotData(file, o.crop);
  const ratio = picture.height / picture.width;
  let w = o.w;
  let h = w * ratio;
  if (o.h && h > o.h) {
    h = o.h;
    w = h / ratio;
  }
  const x = o.align === "center" ? o.x + (o.w - w) / 2 : o.x;
  slide.addImage({ data: picture.data, x, y: o.y, w, h, altText: shotInfo(file).title, objectName: `Screenshot ${file}` });
  slide.addShape("rect", { x, y: o.y, w, h, fill: { type: "none" }, line: { color: HEX.lineStrong, width: 0.75 }, objectName: `Frame ${file}` });
  return { x, y: o.y, w, h };
}

/**
 * A caption under a picture or beside a figure.
 * @param {object} slide
 * @param {string|object[]} content
 * @param {{x: number, y: number, w: number, h?: number, color?: string}} o
 * @returns {void}
 */
function caption(slide, content, o) {
  text(slide, content, { x: o.x, y: o.y, w: o.w, h: o.h || 0.4, fontSize: PT.cap, color: o.color || S.ink2, objectName: "Caption" });
}

/**
 * Bulleted lines from plain strings.
 * @param {string[]} items
 * @param {object} [runOptions] options for every line
 * @returns {object[]} pptxgenjs text runs
 */
function bulletRuns(items, runOptions = {}) {
  return items.map((item, index) => ({
    text: item,
    options: { bullet: { indent: 14 }, paraSpaceAfter: 5, breakLine: index < items.length - 1, ...runOptions },
  }));
}

/**
 * A small caps label, used above panels and columns.
 * @param {object} slide
 * @param {string} label
 * @param {{x: number, y: number, w: number, color?: string}} o
 * @returns {void}
 */
function kicker(slide, label, o) {
  text(slide, label, { x: o.x, y: o.y, w: o.w, h: 0.24, fontSize: 10.5, bold: true, charSpacing: 1, color: o.color || S.teal, valign: "middle", objectName: `Label ${label}` });
}

const CELL_BORDER = { type: "solid", pt: 0.75, color: HEX.line };
const NO_BORDER = { type: "none" };

/**
 * A native table in the deck's style: a dark header row, hairlines between rows.
 * @param {object} slide
 * @param {string[]} head column heads
 * @param {(string|object)[][]} rows cells as strings, or {text, options} for a styled cell
 * @param {{x?: number, y: number, w?: number, colW: number[], fontSize?: number, name?: string}} o
 * @returns {void}
 */
function table(slide, head, rows, o) {
  const headRow = head.map((label) => ({
    text: label,
    options: { bold: true, fontSize: 11, color: HEX.white, fill: { color: S.ink }, valign: "middle", border: [NO_BORDER, NO_BORDER, NO_BORDER, NO_BORDER] },
  }));
  const bodyRows = rows.map((row) =>
    row.map((cell) => {
      const base = { fontSize: o.fontSize || PT.body, color: S.ink, fill: { color: S.white }, valign: "top", border: [NO_BORDER, NO_BORDER, { ...CELL_BORDER }, NO_BORDER] };
      return typeof cell === "string" ? { text: cell, options: base } : { text: cell.text, options: { ...base, ...cell.options } };
    }),
  );
  slide.addTable([headRow, ...bodyRows], {
    x: o.x === undefined ? G.MX : o.x,
    y: o.y,
    w: o.w || G.CW,
    colW: o.colW,
    margin: [4, 7, 4, 7],
    objectName: o.name || "Table",
  });
}

/**
 * Options every chart in the deck shares, so axis and data labels look the same on each.
 * @param {object} extra the chart's own options
 * @returns {object}
 */
function chartOptions(extra) {
  return {
    catAxisLineColor: HEX.lineStrong,
    valAxisLineColor: HEX.lineStrong,
    catAxisLabelColor: HEX.ink,
    catAxisLabelFontFace: FONT.body,
    catAxisLabelFontSize: 12,
    valAxisLabelColor: HEX.ink2,
    valAxisLabelFontFace: FONT.body,
    valAxisLabelFontSize: 11,
    valGridLine: { color: HEX.line, size: 0.5 },
    catGridLine: { style: "none" },
    dataLabelColor: HEX.ink,
    dataLabelFontFace: FONT.body,
    dataLabelFontSize: 12,
    dataLabelFontBold: true,
    legendFontFace: FONT.body,
    legendFontSize: 11,
    legendColor: HEX.ink2,
    titleFontFace: FONT.body,
    titleFontSize: 12,
    titleColor: HEX.ink2,
    ...extra,
  };
}

module.exports = { TAGS, text, panel, tag, tagWidth, stat, sourceLine, line, dot, iconDot, shot, caption, bulletRuns, kicker, table, chartOptions };
