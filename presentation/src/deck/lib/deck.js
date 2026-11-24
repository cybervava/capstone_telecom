// How a slide is started and how its speaker notes are written, the same way on every slide.

const { SEGMENTS, APPENDIX_SECTION } = require("./theme");
const { LAYOUT } = require("./layouts");

/** @param {string} key @returns {{key: string, label: string, section: string, role: string}} */
function segmentOf(key) {
  const segment = SEGMENTS.find((candidate) => candidate.key === key);
  if (!segment) throw new Error(`Unknown segment ${key}`);
  return segment;
}

/**
 * A main slide of one segment: the tracker and the role come from its layout.
 * @param {object} pres
 * @param {string} segmentKey
 * @param {string} title the slide's message, as a statement
 * @returns {object} the slide
 */
function mainSlide(pres, segmentKey, title) {
  const slide = pres.addSlide({ masterName: LAYOUT.content(segmentKey), sectionTitle: segmentOf(segmentKey).section });
  slide.addText(title, { placeholder: "title" });
  return slide;
}

/**
 * An appendix slide.
 * @param {object} pres
 * @param {string} title
 * @returns {object} the slide
 */
function appendixSlide(pres, title) {
  const slide = pres.addSlide({ masterName: LAYOUT.appendix, sectionTitle: APPENDIX_SECTION });
  slide.addText(title, { placeholder: "title" });
  return slide;
}

/**
 * Speaker notes of a main slide: who speaks and for how long, what to say, and the sources.
 * @param {object} slide
 * @param {{role: string, seconds: number, talk: string[], sources: string}} o
 * @returns {void}
 */
function speakerNotes(slide, o) {
  slide.addNotes([`${o.role} · ${o.seconds} s`, ...o.talk, `Sources: ${o.sources}`].join("\n\n"));
}

/**
 * The one-line note of an appendix slide.
 * @param {object} slide
 * @param {string} sources
 * @returns {void}
 */
function sourceNote(slide, sources) {
  slide.addNotes(`Sources: ${sources}`);
}

module.exports = { mainSlide, appendixSlide, speakerNotes, sourceNote, segmentOf };
