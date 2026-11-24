// Writes the deck's own colours into the theme of a .pptx that pptxgenjs wrote.
//
// pptxgenjs sets the theme's two fonts but always writes Office's stock palette, so every
// scheme colour (tx1, accent1, ...) would show as Office blue and orange. This rewrites
// ppt/theme/theme1.xml with THEME.colors, checks for three faults PowerPoint would offer to
// repair, and drops the folder entries pptxgenjs leaves in the zip. It does what the pptx
// skill's apply_theme.js does, kept here so the deck can be rebuilt on any machine.

const fs = require("fs");
// jszip comes with pptxgenjs, so it is loaded from beside it and needs no entry of its own in package.json.
const JSZip = require(require.resolve("jszip", { paths: [require.resolve("pptxgenjs")] }));

const SLOTS = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"];
const DASHES = ["solid", "dot", "dash", "lgDash", "dashDot", "lgDashDot", "lgDashDotDot", "sysDash", "sysDot", "sysDashDot", "sysDashDotDot"];
const THEME_PART = "ppt/theme/theme1.xml";

/** @param {string} value @returns {string} */
function escapeAttribute(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * @param {string} xml the theme part pptxgenjs wrote
 * @param {{name: string, colors: Record<string, string>}} theme
 * @returns {string} the theme part with the deck's colour scheme and names
 */
function themeXml(xml, theme) {
  for (const slot of SLOTS) {
    if (!/^[0-9A-Fa-f]{6}$/.test(String(theme.colors[slot]))) {
      throw new Error(`theme.colors.${slot} must be six hex digits, got ${JSON.stringify(theme.colors[slot])}`);
    }
  }
  const name = escapeAttribute(theme.name);
  const scheme =
    `<a:clrScheme name="${name}">` +
    SLOTS.map((slot) => `<a:${slot}><a:srgbClr val="${theme.colors[slot].toUpperCase()}"/></a:${slot}>`).join("") +
    "</a:clrScheme>";
  if (!/<a:clrScheme\b[\s\S]*?<\/a:clrScheme>/.test(xml)) throw new Error(`${THEME_PART} has no colour scheme to replace`);
  return xml
    .replace(/<a:clrScheme\b[\s\S]*?<\/a:clrScheme>/, () => scheme)
    .replace(/(<a:(?:theme|fontScheme)\b[^>]*?\bname=")[^"]*"/g, (_, head) => `${head}${name}"`);
}

/**
 * @param {string} partName
 * @param {string} xml
 * @returns {string} the part, mended where it can be; throws where the generator must change
 */
function checkedPart(partName, xml) {
  const badColour = xml.match(/<a:srgbClr val="((?![0-9A-Fa-f]{6}")[^"]*)"/);
  if (badColour) {
    throw new Error(`${partName}: "${badColour[1]}" is not a hex colour. A scheme colour was passed to an option that takes hex only (grid lines, shadows, chartColors).`);
  }
  const badDash = [...xml.matchAll(/<a:prstDash val="([^"]*)"/g)].find((m) => !DASHES.includes(m[1].trim()));
  if (badDash) throw new Error(`${partName}: "${badDash[1]}" is not a dash style (${DASHES.join(", ")}).`);
  // pptxgenjs writes valAxisCrossesAt: 0 as a word where a number belongs.
  return xml.replaceAll('<c:crossesAt val="autoZero"/>', '<c:crosses val="autoZero"/>');
}

/**
 * @param {string} deckPath the .pptx to rewrite in place
 * @param {{name: string, colors: Record<string, string>}} theme
 * @returns {Promise<void>}
 */
async function applyTheme(deckPath, theme) {
  const zip = await JSZip.loadAsync(fs.readFileSync(deckPath));
  const themePart = zip.file(THEME_PART);
  if (!themePart) throw new Error(`${deckPath} has no ${THEME_PART}`);
  zip.file(THEME_PART, themeXml(await themePart.async("string"), theme));

  for (const partName of Object.keys(zip.files)) {
    if (!partName.endsWith(".xml") || zip.files[partName].dir) continue;
    const xml = await zip.file(partName).async("string");
    const checked = checkedPart(partName, xml);
    if (checked !== xml) zip.file(partName, checked);
  }
  // A zip entry for a folder is not a part of the package; validators flag them.
  for (const partName of Object.keys(zip.files)) if (zip.files[partName].dir) delete zip.files[partName];

  fs.writeFileSync(deckPath, await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" }));
}

module.exports = { applyTheme };
