// Figures that more than one slide quotes, each with the file it comes from, so that a
// change is made once. Every other figure sits beside its slide, with its source in that
// slide's speaker notes.

const fs = require("fs");
const path = require("path");

// The brief's four checklists, as counted in presentation/G3_Telco_Checklists.pdf, section 0
// (state at commit b9c70ef, 24 November 2026).
const CHECKLISTS = [
  { name: "Guardrails", items: 10, ticked: 10, part: 0, open: 0 },
  { name: "Security", items: 8, ticked: 5, part: 3, open: 0 },
  { name: "Compliance and responsible AI", items: 8, ticked: 4, part: 4, open: 0 },
  { name: "Operations readiness", items: 8, ticked: 3, part: 3, open: 2 },
];

/** @returns {{items: number, ticked: number, part: number, open: number}} the four checklists together */
function checklistTotals() {
  return CHECKLISTS.reduce(
    (sum, list) => ({ items: sum.items + list.items, ticked: sum.ticked + list.ticked, part: sum.part + list.part, open: sum.open + list.open }),
    { items: 0, ticked: 0, part: 0, open: 0 },
  );
}

/**
 * Stops the build if the checklists document no longer says what the deck says.
 * @returns {void}
 */
function checkAgainstChecklistsDocument() {
  const source = path.resolve(__dirname, "..", "..", "..", "pdf", "G3_Telco_Checklists.html");
  if (!fs.existsSync(source)) return;
  const html = fs.readFileSync(source, "utf8");
  const read = (label) => {
    const match = html.match(new RegExp(`<em>${label}</em><b>(\\d+)</b>`));
    return match ? Number(match[1]) : null;
  };
  const totals = checklistTotals();
  const printed = { ticked: read("Ticked"), part: read("Met in part"), open: read("Not ticked") };
  for (const key of Object.keys(printed)) {
    if (printed[key] !== null && printed[key] !== totals[key]) {
      throw new Error(`Checklist counts differ: the deck says ${totals[key]} for "${key}", ${source} says ${printed[key]}. Update slides/parts/facts.js.`);
    }
  }
}

// The test suite, as run by GitLab pipeline 3106633060, job test, commit b9c70ef, on
// 24 November 2026 (presentation/G3_Telco_Evaluation.pdf, section 2.3).
const TESTS = { count: "2,689", failed: 0, coverage: "96.0%", gate: "80%", pipeline: "3106633060", commit: "b9c70ef", date: "24 November 2026" };

module.exports = { CHECKLISTS, checklistTotals, checkAgainstChecklistsDocument, TESTS };
