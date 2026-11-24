// Builds presentation/G3_Telco_Presentation.pptx.
//
//   cd presentation/src/deck && node build_deck.js
//
// The deck is a structured one: a theme (lib/theme.js), named layouts (lib/layouts.js), one
// section per segment of the talk, and slides that fill the layouts' placeholders. Each file
// under slides/ holds the slides of one segment, in order. To change a slide, edit its file
// and run this again; to restyle the deck, change lib/theme.js.

const path = require("path");
const PptxGenJS = require("pptxgenjs");
const { THEME, SEGMENTS, APPENDIX_SECTION } = require("./lib/theme");
const { defineLayouts } = require("./lib/layouts");
const { applyTheme } = require("./lib/apply_theme");
const { checkAgainstChecklistsDocument } = require("./slides/parts/facts");

const OUT = path.resolve(__dirname, "..", "..", "G3_Telco_Presentation.pptx");
const MAX_MAIN_SLIDES = 18; // the brief, p.3

const MAIN = [
  require("./slides/s01_problem"),
  require("./slides/s02_approach"),
  require("./slides/s03_data"),
  require("./slides/s04_demo"),
  require("./slides/s05_trust"),
  require("./slides/s06_close"),
];
const APPENDIX = [
  require("./slides/a01_evidence"),
  require("./slides/a02_design"),
  require("./slides/a03_trust"),
  require("./slides/a04_demo_backup"),
  require("./slides/a05_questions"),
];

/** @returns {Promise<{main: number, appendix: number}>} how many slides were written */
async function build() {
  const pres = new PptxGenJS();
  pres.layout = "LAYOUT_WIDE";
  pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
  pres.title = "Care that knows what the network knows";
  pres.subject = "Group 3, Telco: outage-aware customer care agent. Capstone presentation, 30 November 2026";
  pres.author = "Group 3";
  pres.company = "Group 3, Telco capstone";
  checkAgainstChecklistsDocument();
  defineLayouts(pres);

  let main = 0;
  for (const module of MAIN) {
    const segment = SEGMENTS.find((candidate) => candidate.key === module.segment);
    pres.addSection({ title: segment.section });
    for (const slide of module.slides) {
      await slide(pres);
      main += 1;
    }
  }
  if (main > MAX_MAIN_SLIDES) throw new Error(`${main} main slides: the brief allows ${MAX_MAIN_SLIDES}`);

  let appendix = 0;
  pres.addSection({ title: APPENDIX_SECTION });
  for (const module of APPENDIX) {
    for (const slide of module.slides) {
      await slide(pres);
      appendix += 1;
    }
  }

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  return { main, appendix };
}

build().then(
  (count) => process.stdout.write(`${OUT}\n${count.main} main slides, ${count.appendix} appendix slides (the divider included)\n`),
  (error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exit(1);
  },
);
