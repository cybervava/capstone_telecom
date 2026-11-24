// Pictures for the deck: icons drawn from react-icons, and crops of the real screenshots.
//
// Screenshots are never edited. A crop is a rectangle of the real image, scaled down to the
// size it is shown at; nothing is drawn on it. Captions come from the screenshots' manifest.

const path = require("path");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const lucide = require("react-icons/lu");

const ASSETS_DIR = path.resolve(__dirname, "..", "..", "assets");
const SCREENS_DIR = path.join(ASSETS_DIR, "screens");
const manifest = require(path.join(SCREENS_DIR, "manifest.json"));

const ICON_PX = 256;
const MAX_SHOT_PX = 1800; // widest a crop is stored at: twice a full-width slide at 72 dpi

const iconCache = new Map();
const shotCache = new Map();

/**
 * @param {string} name a Lucide icon of react-icons, such as "LuLock"
 * @param {string} hex six hex digits
 * @returns {Promise<string>} the icon as a data URI for pptxgenjs
 */
async function iconData(name, hex) {
  const key = `${name}:${hex}`;
  if (iconCache.has(key)) return iconCache.get(key);
  const Icon = lucide[name];
  if (!Icon) throw new Error(`Unknown icon ${name}`);
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(Icon, { color: `#${hex}`, size: ICON_PX }));
  const png = await sharp(Buffer.from(svg)).png().toBuffer();
  const data = `image/png;base64,${png.toString("base64")}`;
  iconCache.set(key, data);
  return data;
}

/**
 * @param {string} file a screenshot's file name in assets/screens
 * @returns {{title: string, shows: string, pixels: string}} its manifest entry
 */
function shotInfo(file) {
  const entry = manifest.shots.find((shot) => shot.file === file);
  if (!entry) throw new Error(`${file} is not in the screenshots manifest`);
  return entry;
}

/**
 * @param {string} file a screenshot's file name
 * @param {{left: number, top: number, width: number, height: number}} [crop] in the image's own pixels
 * @returns {Promise<{data: string, width: number, height: number}>} the crop, and its size in pixels before scaling
 */
async function shotData(file, crop) {
  const key = `${file}:${crop ? [crop.left, crop.top, crop.width, crop.height].join(",") : "all"}`;
  if (shotCache.has(key)) return shotCache.get(key);
  const source = sharp(path.join(SCREENS_DIR, file));
  const meta = await source.metadata();
  const region = crop || { left: 0, top: 0, width: meta.width, height: meta.height };
  if (region.left + region.width > meta.width || region.top + region.height > meta.height) {
    throw new Error(`Crop of ${file} runs outside the image (${meta.width} x ${meta.height})`);
  }
  const png = await source
    .extract(region)
    .resize({ width: Math.min(region.width, MAX_SHOT_PX), withoutEnlargement: true })
    .png({ compressionLevel: 9 })
    .toBuffer();
  const result = { data: `image/png;base64,${png.toString("base64")}`, width: region.width, height: region.height };
  shotCache.set(key, result);
  return result;
}

/** @returns {string} the path of the architecture diagram of the architecture document */
function architecturePng() {
  return path.join(ASSETS_DIR, "architecture.png");
}

module.exports = { iconData, shotInfo, shotData, architecturePng, manifest };
