# Submission package: Group 3, Telco

Everything for the capstone submission of 29 November 2026 and the presentation of 30 November,
named as the team's action plan names it (`G3_Telco_<Item>`). SkyLink Telecom is fictional and all
data is synthetic.

## The nine items

| # | Item in the brief | File | State |
|---|---|---|---|
| 1 | Presentation deck, max 18 slides + appendix | `G3_Telco_Presentation.pptx`, with a PDF copy | Built: 18 main slides with timed speaker notes (20 minutes), then 22 appendix slides (A21 and A22: the project review's changes of 25 November). Add the members' names to the title slide |
| 2 | Problem statement, 1 to 2 pages | `G3_Telco_Problem_Statement.pdf` | Built, 2 pages |
| 3 | Architecture document | `G3_Telco_Architecture.pdf` | Built, 14 pages |
| 4 | Code repository with README | this repository, branch `dev` | In place |
| 5 | Live demo and a 5-minute backup video | `G3_Telco_Demo_Backup.mp4` | **To record.** Not in this folder |
| 6 | Evaluation report | `G3_Telco_Evaluation.pdf` | Built, 13 pages |
| 7 | Signed checklists | `G3_Telco_Checklists.pdf` | Built, 10 pages. **To sign** (page 10) |
| 8 | ROI model | `G3_Telco_ROI.xlsx` | Built, 8 sheets, live formulas |
| 9 | Contribution matrix, signed by all five | `G3_Telco_Contributions.pdf` | A form. **To complete and sign** |

## Before submitting

- Push `dev` so that the AWS demo host runs the changes of 25 November (amounts in dollars, a
  model beside the guide search, the MCP server), and run the smoke journeys there. Until then the
  live demonstration shows rupees and the guide scene's "crawling" line gets the "No internet"
  steps from the keyword rules alone. The MCP server stays closed on the host (no key is set).
- Write the five members' names: the deck's title slide, the contribution matrix and the
  checklists' signature page. Nothing is pre-filled and nothing is pre-signed.
- Each member completes their row of the contribution matrix in their own words, then all sign.
- Record the backup video after the release freeze, on the same code and mock APIs.
- Open the workbook once in Microsoft Excel and the deck once in PowerPoint. Both were checked in
  LibreOffice only, where Liberation Sans and Carlito stand in for the deck's Arial and Calibri; the
  PDF copy of the deck embeds those stand-ins.
- Read the six stakeholder answers on appendix slides A17 and A18 before the questions: they are
  drafted from the repository's documents, not by the team.
- Rehearse path 2 of the demonstration on the AWS host. There the platform looks again every 10
  seconds and hands the contact to a person after five looks, so the Network Monitoring API must
  come back within about 40 seconds of Send for the follow-up to show (slide 12's notes).
- If anything of milestone M5 lands (metrics, a dashboard, alerts, a runbook), update checklist
  items 4.3, 4.4, 4.5 and 4.8 and the counts on pages 1 and 10 of the checklists, slide 15 of the
  deck, and section 9 of the evaluation report, before signing.
- The supervisor example reads $520.73 only if the outage is resolved within the minute it was
  declared; later it bills a 51st hour and reads $531.14. Either way a supervisor decides.

## Where the numbers come from

Every figure in these documents is taken from the repository's own reports and documents
(`README.md`, `docs/`, `evals/reports/`, `loadtest/reports/`) or from the brief, and each document
names its sources. Measured results are measured in simulation on the synthetic stand-in data;
assumptions and things not built are labelled as such. The test-suite figure (2,902 tests, none
failed, 96% coverage with branches counted) is a run on one laptop on commit `772467c`, 25 November
2026; the last pipeline, GitLab 3106633060 on commit `b9c70ef` of 24 November, ran the 2,689 tests
there were before the project review's changes.

## Rebuilding after an edit

The sources are under `src/`. Edit the source, then rebuild; do not edit a PDF.

| What | Source | Rebuild |
|---|---|---|
| The five PDFs | `src/pdf/<name>.html`, one shared stylesheet `src/pdf/base.css` | `node presentation/src/build_pdf.mjs G3_Telco_Architecture` (or `--all`). Needs Chrome; set `CHROME` if it is not found |
| The deck | `src/deck/` | `cd presentation/src/deck && npm install && node build_deck.js`, then `presentation/src/office_to_pdf.sh presentation/G3_Telco_Presentation.pptx` for the PDF copy. The build stops if the checklist counts on the slides differ from the checklists' source |
| The workbook | `src/roi/` | `uv run --no-project --with openpyxl python presentation/src/roi/build_roi.py`, then `check_roi.py` |
| The architecture diagram used on a slide | Figure 1 of `src/pdf/G3_Telco_Architecture.html` | `node presentation/src/export_architecture.mjs` |
| The screenshots | the running local demo (`make demo`) | `node presentation/src/capture_screens.mjs [scene ...]`. Needs Chrome and `playwright-core`; set `PLAYWRIGHT_CORE` to its folder if Node does not find it |

After rebuilding a PDF, check its page count: the problem statement must stay at 2 pages, and
several pages of the architecture document are full to within a few millimetres.

The PDF copy of the deck and the stored results in the workbook are made with LibreOffice in a
container (`src/libreoffice/Dockerfile`, `src/office_to_pdf.sh`). PowerPoint's own "Save as PDF"
does the same for the deck.

The screenshots in `src/assets/screens/` are real captures of the local demonstration, taken on
26 November 2026 (Indian time) with the offline keyword rules reading the messages, except the
guide scene (images 10 and 10b), retaken with `gpt-5.4-mini` choosing the guide section;
`manifest.json` says what each one shows and, in `read_by`, who read the messages. The fonts in `src/fonts/` are under the SIL Open Font License (the licence files
are beside them).
