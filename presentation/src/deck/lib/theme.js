// The deck's theme, palette and grid. One place to restyle the whole deck.
//
// THEME is written into the .pptx (fonts by pptxgenjs, colours by lib/apply_theme.js), so in
// PowerPoint "Design > Variants > Colors/Fonts" restyles every slide. Colours that are theme
// slots are used through S (scheme names); the tints and the three traffic colours of the
// diagrams have no slot and are plain hex from HEX.

const THEME = {
  name: "SkyLink Care",
  headFontFace: "Arial",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "12233A", // ink
    lt1: "FFFFFF", // white
    dk2: "43556B", // secondary text
    lt2: "EDF1F4", // ground
    accent1: "0B7A83", // teal, the accent
    accent2: "1E8752", // met, ok
    accent3: "B06D00", // assumption
    accent4: "BE362D", // stop, not built
    accent5: "5B45B0", // a person decides
    accent6: "2F6FD6", // asks and answers
    hlink: "0B7A83",
    folHlink: "5B45B0",
  },
};

// Scheme names, as pptxgenjs writes them (pres.SchemeColor).
const S = {
  ink: "tx1",
  white: "bg1",
  ink2: "tx2",
  ground: "bg2",
  teal: "accent1",
  ok: "accent2",
  amber: "accent3",
  red: "accent4",
  purple: "accent5",
  ask: "accent6",
};

// Plain hex: the same palette as presentation/src/pdf/base.css.
const HEX = {
  ink: "12233A",
  ink2: "43556B",
  ink3: "5A6B7E",
  white: "FFFFFF",
  ground: "EDF1F4",
  sunken: "E2E8ED",
  line: "CCD6DF",
  lineStrong: "A3B2C0",
  teal: "0B7A83",
  tealInk: "086169",
  tealSoft: "D7EEF0",
  ok: "1E8752",
  okInk: "14633B",
  okSoft: "DAF1E4",
  amber: "B06D00",
  amberInk: "7F4D00",
  amberSoft: "FBECCF",
  red: "BE362D",
  redInk: "9A2720",
  redSoft: "FBE3E0",
  purple: "5B45B0",
  purpleInk: "4A3796",
  purpleSoft: "E8E3F8",
  ask: "2F6FD6", // asks and answers, solid
  event: "D65F18", // announcements, dashed
  act: "128A6B", // actions, heavy
  darkText: "F1F5F8",
  darkMuted: "A9B8C6",
  darkAccent: "7ADAE1",
  darkPanel: "1C3150",
};

// Heading font for text that is not a title placeholder (big figures, card heads).
// "+mj-lt" is the theme's heading font, "+mn-lt" its body font.
const FONT = { head: "+mj-lt", body: "+mn-lt" };

// The grid, in inches, on a 13.333 x 7.5 slide.
const G = {
  W: 13.333,
  H: 7.5,
  MX: 0.6, // side margin
  CW: 12.133, // content width
  TRACK_Y: 0.36,
  TITLE_Y: 0.72,
  TITLE_H: 1.22,
  BODY_Y: 2.1, // top of the content area on a main slide
  BODY_B: 6.58, // bottom of the content area
  SRC_Y: 6.66, // the source line
  FOOT_Y: 7.04,
  APX_TITLE_Y: 0.78,
  APX_BODY_Y: 1.62, // top of the content area on an appendix slide
};

// Type sizes, in points. Body never goes under 14, captions never under 10.
const PT = { title: 36, apxTitle: 28, head: 18, body: 14, small: 12, cap: 10.5, tag: 10 };

// The six segments of the talk (brief p.3-4): tracker label, section name, presenter.
const SEGMENTS = [
  { key: "problem", label: "PROBLEM", section: "1 Client problem and value", role: "R1 · FDE LEAD" },
  { key: "approach", label: "APPROACH", section: "2 Approach and architecture", role: "R2 · ARCHITECT" },
  { key: "data", label: "DATA", section: "3 Data and knowledge", role: "R3 · DATA & RAG" },
  { key: "demo", label: "DEMO", section: "4 Live demo", role: "R4 · AGENT ENGINEER" },
  { key: "trust", label: "TRUST", section: "5 Trust and operations", role: "R5 · PLATFORM & TRUST" },
  { key: "close", label: "CLOSE", section: "6 Curveball and ROI close", role: "R1 · FDE LEAD" },
];
const APPENDIX_SECTION = "Appendix";
const FOOTER = "Group 3 · Telco · synthetic data";

module.exports = { THEME, S, HEX, FONT, G, PT, SEGMENTS, APPENDIX_SECTION, FOOTER };
