// The slide layouts ("masters" in pptxgenjs). A slide picks one by name and fills its
// placeholders; the tracker, the footer and the slide number live here, never on a slide.
//
//   Title                      the opening slide (dark)
//   Section opener             a divider (dark); used before the appendix
//   Content · <Segment>        a main slide of one of the six segments, with the segment
//                              tracker and the presenting role (six layouts, one per segment;
//                              "Content · Close" is the dark one)
//   Appendix                   a reference slide: smaller title, no tracker

const { S, HEX, G, PT, SEGMENTS, FOOTER } = require("./theme");

const LAYOUT = {
  title: "Title",
  section: "Section opener",
  appendix: "Appendix",
  /** @param {string} key a segment key from SEGMENTS @returns {string} */
  content: (key) => `Content · ${key[0].toUpperCase()}${key.slice(1)}`,
};

const TRACK = { dot: 0.13, pitch: 1.28, labelPad: 0.2 };

/**
 * The segment tracker: six labelled marks, the current one filled, and the presenting role.
 * @param {object} pres
 * @param {string} currentKey
 * @param {boolean} dark
 * @returns {object[]} layout objects
 */
function trackerObjects(pres, currentKey, dark) {
  const objects = [];
  const muted = dark ? HEX.darkMuted : HEX.ink3;
  const strong = dark ? HEX.darkText : S.ink;
  const accent = dark ? HEX.darkAccent : S.teal;
  let x = G.MX;
  for (const segment of SEGMENTS) {
    const current = segment.key === currentKey;
    const labelW = TRACK.pitch - TRACK.labelPad - 0.06;
    objects.push({
      text: {
        text: "",
        options: {
          x,
          y: G.TRACK_Y + 0.045,
          w: TRACK.dot,
          h: TRACK.dot,
          shape: pres.ShapeType.ellipse,
          fill: { color: current ? accent : dark ? HEX.ink : HEX.ground },
          line: { color: current ? accent : muted, width: 1 },
          objectName: `Tracker mark ${segment.label}`,
        },
      },
    });
    objects.push({
      text: {
        text: segment.label,
        options: {
          x: x + TRACK.labelPad,
          y: G.TRACK_Y,
          w: labelW,
          h: 0.22,
          margin: 0,
          fontSize: 10,
          bold: current,
          charSpacing: 1,
          color: current ? strong : muted,
          valign: "middle",
          isTextBox: true,
          objectName: `Tracker label ${segment.label}`,
        },
      },
    });
    x += TRACK.pitch;
  }
  const role = SEGMENTS.find((segment) => segment.key === currentKey).role;
  objects.push({
    text: {
      text: role,
      options: {
        x: G.W - G.MX - 3.4,
        y: G.TRACK_Y,
        w: 3.4,
        h: 0.22,
        margin: 0,
        fontSize: 10,
        bold: true,
        charSpacing: 1,
        align: "right",
        valign: "middle",
        color: accent,
        isTextBox: true,
        objectName: "Presenting role",
      },
    },
  });
  return objects;
}

/** @param {boolean} dark @returns {object} the footer text object */
function footerObject(dark) {
  return {
    text: {
      text: FOOTER,
      options: {
        x: G.MX,
        y: G.FOOT_Y,
        w: 6,
        h: 0.24,
        margin: 0,
        fontSize: 10,
        color: dark ? HEX.darkMuted : S.ink2,
        valign: "middle",
        isTextBox: true,
        objectName: "Footer",
      },
    },
  };
}

/** @param {boolean} dark @returns {object} slide-number options */
function slideNumber(dark) {
  return { x: G.W - G.MX - 0.8, y: G.FOOT_Y, w: 0.8, h: 0.24, fontSize: 10, align: "right", color: dark ? HEX.darkMuted : S.ink2 };
}

/**
 * Defines every layout on the presentation.
 * @param {object} pres a pptxgenjs presentation
 * @returns {void}
 */
function defineLayouts(pres) {
  pres.defineSlideMaster({
    title: LAYOUT.title,
    background: { color: S.ink },
    objects: [
      {
        placeholder: {
          options: { name: "title", type: "title", x: 0.8, y: 1.95, w: 7.9, h: 1.75, margin: 0, fontSize: 46, bold: true, color: HEX.darkText, align: "left", valign: "top" },
          text: "Deck title",
        },
      },
      {
        placeholder: {
          options: { name: "subtitle", type: "body", x: 0.8, y: 3.95, w: 7.4, h: 0.9, margin: 0, fontSize: 20, color: HEX.darkMuted, align: "left", valign: "top" },
          text: "Subtitle",
        },
      },
      footerObject(true),
    ],
  });

  pres.defineSlideMaster({
    title: LAYOUT.section,
    background: { color: S.ink },
    objects: [
      {
        placeholder: {
          options: { name: "title", type: "title", x: 0.8, y: 1.3, w: 4.6, h: 1.2, margin: 0, fontSize: 48, bold: true, color: HEX.darkText, align: "left", valign: "top" },
          text: "Section title",
        },
      },
      {
        placeholder: {
          options: { name: "body", type: "body", x: 0.8, y: 2.6, w: 4.4, h: 3.4, margin: 0, fontSize: 16, color: HEX.darkMuted, align: "left", valign: "top" },
          text: "What this section holds",
        },
      },
      footerObject(true),
    ],
    slideNumber: slideNumber(true),
  });

  for (const segment of SEGMENTS) {
    const dark = segment.key === "close";
    pres.defineSlideMaster({
      title: LAYOUT.content(segment.key),
      background: { color: dark ? S.ink : S.ground },
      objects: [
        ...trackerObjects(pres, segment.key, dark),
        {
          placeholder: {
            options: {
              name: "title",
              type: "title",
              x: G.MX,
              y: G.TITLE_Y,
              w: G.CW,
              h: G.TITLE_H,
              margin: 0,
              fontSize: PT.title,
              bold: true,
              color: dark ? HEX.darkText : S.ink,
              align: "left",
              valign: "top",
              lineSpacingMultiple: 0.92,
            },
            text: "The slide's message, as a statement",
          },
        },
        footerObject(dark),
      ],
      slideNumber: slideNumber(dark),
    });
  }

  pres.defineSlideMaster({
    title: LAYOUT.appendix,
    background: { color: S.ground },
    objects: [
      {
        text: {
          text: "APPENDIX",
          options: { x: G.MX, y: G.TRACK_Y, w: 3, h: 0.22, margin: 0, fontSize: 10, bold: true, charSpacing: 1, color: S.teal, valign: "middle", isTextBox: true, objectName: "Appendix mark" },
        },
      },
      {
        placeholder: {
          options: { name: "title", type: "title", x: G.MX, y: G.APX_TITLE_Y, w: G.CW, h: 0.62, margin: 0, fontSize: PT.apxTitle, bold: true, color: S.ink, align: "left", valign: "top" },
          text: "What this reference slide holds",
        },
      },
      footerObject(false),
    ],
    slideNumber: slideNumber(false),
  });
}

module.exports = { LAYOUT, defineLayouts };
