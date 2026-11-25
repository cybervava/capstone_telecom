// Segment 4, the live demonstration (R4, 6 minutes): slides 11 to 13.
//
// The live demonstration runs on AWS with OpenAI's gpt-5.4-mini reading the messages. The
// screenshots on these slides are of the local stack with the offline keyword rules, taken on
// 26 November 2026 (presentation/src/assets/screens/manifest.json). Say which is which.

const { S, HEX, FONT, G } = require("../lib/theme");
const kit = require("../lib/kit");
const { mainSlide, speakerNotes } = require("../lib/deck");

const SEGMENT = "demo";
const ROLE = "R4";

const PATHS = [
  {
    label: "HAPPY PATH",
    scene: "A known outage: told before they call",
    watch: ["Notices go only to customers who agreed", "A complaint reference in milliseconds", "The reply names the outage, its cause and the estimate", "No reboot script, no technician"],
    fill: S.teal,
  },
  {
    label: "EXCEPTION · A FAILURE HANDLED",
    scene: "The Network Monitoring API goes down",
    watch: ["The reference still arrives at once", "The status cannot be verified: no all-clear", "No router steps, no technician", "It looks again by itself and follows up"],
    fill: S.red,
  },
  {
    label: "HUMAN APPROVAL",
    scene: "A credit above $500 waits for a supervisor",
    watch: ["$520.73 in our run, worked out in code", "An agent is not offered the decision", "A supervisor approves the frozen request", "Applied once, with its credit reference"],
    fill: S.purple,
  },
];

/** Slide 11: what the panel is about to see. */
async function previewSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Live, on the mock APIs: a happy path, a failure handled and a human approval");

  const top = G.BODY_Y;
  const gap = 0.25;
  const cardW = (G.CW - 2 * gap) / 3;
  const cardH = 3.12;
  PATHS.forEach((path, index) => {
    const x = G.MX + index * (cardW + gap);
    kit.panel(slide, { x, y: top, w: cardW, h: cardH, shadow: true, name: `Path card ${index + 1}` });
    kit.dot(slide, index + 1, x + 0.22, top + 0.2, { d: 0.5, fill: path.fill, fontSize: 18 });
    kit.kicker(slide, path.label, { x: x + 0.86, y: top + 0.18, w: cardW - 1.0, color: path.fill });
    kit.text(slide, path.scene, { x: x + 0.86, y: top + 0.42, w: cardW - 1.04, h: 0.62, fontFace: FONT.head, fontSize: 16, bold: true, objectName: `Path ${index + 1}` });
    kit.text(slide, kit.bulletRuns(path.watch), { x: x + 0.22, y: top + 1.22, w: cardW - 0.44, h: cardH - 1.34, fontSize: 14, objectName: `Watch for ${index + 1}` });
  });

  // The invitation, and which system is which.
  const bandY = top + cardH + 0.2;
  const bandH = G.BODY_B - bandY;
  const inviteW = 7.0;
  kit.panel(slide, { x: G.MX, y: bandY, w: inviteW, h: bandH, fill: S.ink, name: "Invitation panel" });
  kit.text(
    slide,
    [
      { text: "CHANGE AN INPUT", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.darkAccent, breakLine: true } },
      { text: "Shorten the outage of path 3 to 300 minutes: the credit falls under $500 and is applied without review. Or name another customer, node or router model.", options: { color: HEX.darkText } },
    ],
    { x: G.MX + 0.24, y: bandY + 0.1, w: inviteW - 0.48, h: bandH - 0.2, fontSize: 14, valign: "middle", objectName: "The invitation" },
  );
  const noteX = G.MX + inviteW + 0.25;
  const noteW = G.MX + G.CW - noteX;
  kit.panel(slide, { x: noteX, y: bandY, w: noteW, h: bandH, name: "Which is which panel" });
  kit.text(
    slide,
    [
      { text: "Live: ", options: { bold: true } },
      { text: "AWS, with OpenAI's gpt-5.4-mini reading the messages.", options: { breakLine: true } },
      { text: "Screenshots in this deck: ", options: { bold: true } },
      { text: "the local stack, offline keyword rules, 26 November 2026." },
    ],
    { x: noteX + 0.2, y: bandY + 0.08, w: noteW - 0.4, h: bandH - 0.16, fontSize: 14, valign: "middle", objectName: "Which is which" },
  );
  kit.sourceLine(slide, "Sources: README.md, \"The demo console\" (seven scenes) and \"The demo on AWS\"; src/mock_oss/console/scenes.js; presentation/src/assets/screens/manifest.json. SkyLink's systems are simulated.");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 40,
    talk: [
      "Now the live demonstration. It runs on AWS against our simulator of SkyLink's six systems: mock APIs, nothing hard-coded. OpenAI's gpt-5.4-mini reads the messages.",
      "Three paths. A happy path: the network team declares an outage and a customer on that node writes in. A failure: the Network Monitoring API goes down. And a human approval: a credit above 500 dollars waits for a supervisor.",
      "You may change an input: shorten the third outage to 300 minutes, and the credit is applied without review.",
      "For the presenter, not to be read out: the console has four more scenes in reserve (\"It's my router\", router steps from the guide, a failing line and a booked visit, a prompt injection). The screenshots in this deck are from the local stack with the offline keyword rules.",
    ],
    sources: "README.md, \"The demo console\" (seven scenes, free play, a mentor can name another customer) and \"The demo on AWS\" (gpt-5.4-mini reads the messages); src/mock_oss/console/scenes.js; docs/EXECUTION_PLAN.md, section 12, client decision D3 (a credit up to $500 is applied without review); the brief, p.3 and p.4 (the demo rules).",
  });
}

const SCREENS = [
  {
    name: "CUSTOMER DESK",
    file: "02w-happy-path-outage-reply-wide.png",
    crop: { left: 30, top: 200, width: 1580, height: 900 },
    watch: "The complaint reference and the milliseconds it took, the network chip, and the reply with the source of its estimate.",
    caption: "Image 02w: the happy-path reply naming the outage.",
  },
  {
    name: "STAFF DESK",
    file: "05-supervisor-credit-slip.png",
    crop: { left: 0, top: 560, width: 2160, height: 1230 },
    watch: "Credits awaiting a decision, cases with a person, the operations queue, and the line that says the assistant is keeping up.",
    caption: "Image 05: signed in as a supervisor, the $520.73 slip.",
  },
  {
    name: "DEMO CONSOLE",
    file: "01-console-happy-path-start.png",
    crop: { left: 0, top: 120, width: 2160, height: 1230 },
    watch: "The scene's cues, one button at a time, and on the right what SkyLink's simulated systems hold after each.",
    caption: "Image 01: the console at the start of the happy path.",
  },
];

/** Slide 12: the holding slide of the live demonstration. */
async function screensSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "The live demonstration uses three screens: here is what to watch for on each");

  const top = G.BODY_Y;
  const gap = 0.25;
  const colW = (G.CW - 2 * gap) / 3;
  for (const [index, screen] of SCREENS.entries()) {
    const x = G.MX + index * (colW + gap);
    kit.dot(slide, index + 1, x, top - 0.02, { d: 0.3, fontSize: 12 });
    kit.kicker(slide, screen.name, { x: x + 0.4, y: top, w: colW - 0.4 });
    const placed = await kit.shot(slide, screen.file, { x, y: top + 0.36, w: colW, crop: screen.crop });
    kit.caption(slide, screen.caption, { x, y: placed.y + placed.h + 0.05, w: colW, h: 0.24 });
    const watchY = placed.y + placed.h + 0.38;
    kit.panel(slide, { x, y: watchY, w: colW, h: 1.46, name: `Watch panel ${index + 1}` });
    kit.text(
      slide,
      [
        { text: "WATCH FOR  ", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: S.teal } },
        { text: screen.watch },
      ],
      { x: x + 0.2, y: watchY + 0.08, w: colW - 0.4, h: 1.3, fontSize: 16, valign: "middle", objectName: `Watch for ${screen.name}` },
    );
  }
  kit.sourceLine(slide, "The three images: the local stack, offline keyword rules, 26 November 2026, unedited (screenshots manifest). The live demonstration: AWS, gpt-5.4-mini (README.md, \"The demo on AWS\").");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 270,
    talk: [
      "This slide stays up behind the browser. Before you start: the console is unlocked, \"End everything in force\" has been pressed, and three tabs are open: the demo console, the customer desk and the staff desk.",
      "Path 1, the happy path, about 80 seconds. Console, scene 1. Press Go to declare a fibre cut. Say: the network team's feed announces it, and on the right you see the notices: sent to the customers on the node who agreed, and to none who said no. Open the customer desk as the cast customer and press Send. Point at the complaint reference and the milliseconds it took. Then the reply: the outage, its cause, the estimate and where the estimate comes from, and \"You don't need to restart or reset your router and no technician visit is needed\". Resolve the outage on the console: the customer is told without asking.",
      "Path 2, a failure handled, about 70 seconds. Scene 5. Take the Network Monitoring API down. Open the customer desk and press Send. Point: the reference still arrives at once. The reply says the live status cannot be verified. There is no all-clear, no router steps and no technician. Bring the API back within about 40 seconds of pressing Send: the platform looks again by itself and follows up in the same conversation. On the demo host it looks every 10 seconds and hands the contact to a person after five looks, about 50 seconds; the product default is a minute between looks. If you are late, show the contact on the staff desk under \"With a person\": that is the designed outcome.",
      "Path 3, a human approval, about 100 seconds. Scene 6. Declare the backdated outage, press Send, then Resolve. The message carries a credit that has been sent for approval. On the staff desk, signed in as an agent, the credit is not offered. Signed in as a supervisor, open \"How it was worked out\" on the slip and approve. The customer is told, and the console shows one credit on the billing system with who approved it.",
      "For the presenter, about the amount: the slip reads $520.73 only if you resolve the outage within the same minute you declared it. Outage minutes are rounded up to whole hours, so a slower run bills a 51st hour and the slip reads $531.14. That is still above $500 and still the supervisor's. The amount also follows the monthly fee of the customer the scene casts: the slip shows the working.",
      "If the panel changes an input: in scene 6 set the minutes box to 300 and play it again. The credit is small and is applied without review, as the hint under the minutes box says (client decision D3). Or cast another customer by ID, or choose another router model in scene 3.",
      "If the live demonstration fails: go to the appendix, \"Demo backup\", four slides built from real screenshots, or play the recorded backup video.",
    ],
    sources: "src/mock_oss/console/scenes.js (the cues and what to expect at each); README.md, \"The demo console\" and \"The demo on AWS\"; src/skylink_care/domain/credit.py:289 (hours are a ceiling division: 4,999 × 51 × 1.5 / 720 = $531.14); presentation/src/assets/screens/manifest.json (images 01, 02w, 05).",
  });
}

const TRAIL_FILE = "12-audit-trail-close-crop.png";
const TRAIL_READING = [
  { head: "Specialist agents read and decide by rule", body: "each call they make to SkyLink's systems is an entry" },
  { head: "The executor is the only writer", body: "complaint registered, linked to the incident, credit applied" },
  { head: "A person approved the credit", body: "approval.decided → APPROVED, before it was applied", human: true },
  { head: "Versions, where a step used one", body: "what read the message, the prompt, the policy" },
];

/** Slide 13: what just happened, in the audit trail. */
async function auditSlide(pres) {
  const slide = mainSlide(pres, SEGMENT, "Every step is on the audit trail: who or what did it, and under which versions");

  const top = G.BODY_Y;
  const partW = 3.6;
  // The two parts end between entries: seven on the left, eight on the right.
  const first = await kit.shot(slide, TRAIL_FILE, { x: G.MX, y: top, w: partW, h: 3.95, crop: { left: 0, top: 222, width: 687, height: 693 } });
  const second = await kit.shot(slide, TRAIL_FILE, { x: G.MX + partW + 0.2, y: top, w: partW, h: 3.95, crop: { left: 0, top: 915, width: 687, height: 759 } });
  kit.caption(slide, "One contact's trail, in two parts (left, then right): from the complaint to the credit a supervisor approved. A crop of the customer desk opened as staff; local stack, offline keyword rules, 26 November 2026.", {
    x: G.MX,
    y: Math.max(first.y + first.h, second.y + second.h) + 0.06,
    w: 2 * partW + 0.2,
    h: 0.42,
  });

  const rx = G.MX + 2 * partW + 0.5;
  const rw = G.MX + G.CW - rx;
  kit.panel(slide, { x: rx, y: top, w: rw, h: 0.72, fill: S.ink, name: "How to read an entry" });
  kit.text(
    slide,
    [
      { text: "HOW TO READ AN ENTRY", options: { bold: true, fontSize: 10.5, charSpacing: 1, color: HEX.darkAccent, breakLine: true } },
      { text: "step → outcome, then actor · duration · versions", options: { color: HEX.darkText } },
    ],
    { x: rx + 0.18, y: top + 0.06, w: rw - 0.36, h: 0.6, fontSize: 14, valign: "middle", objectName: "How to read an entry" },
  );
  TRAIL_READING.forEach((item, index) => {
    const y = top + 0.86 + index * 0.8;
    kit.panel(slide, { x: rx, y, w: rw, h: 0.72, name: `Reading card ${index + 1}` });
    kit.text(
      slide,
      [
        { text: item.head, options: { bold: true, breakLine: true } },
        { text: item.body, options: { fontSize: 12, color: S.ink2 } },
      ],
      { x: rx + 0.18, y: y + 0.03, w: rw - 0.36, h: 0.66, fontSize: 14, valign: "middle", objectName: `Reading ${index + 1}` },
    );
    if (item.human) kit.tag(slide, "human", rx + rw - 0.14 - kit.tagWidth("HUMAN GATE"), y + 0.1);
  });
  const tagY = top + 0.86 + 4 * 0.8 + 0.06;
  const tagW = kit.tag(slide, "built", rx, tagY);
  kit.text(slide, "The database refuses to change or delete an entry. Limit: one shared database role.", { x: rx + tagW + 0.1, y: tagY - 0.1, w: rw - tagW - 0.1, h: 0.46, fontSize: 12, valign: "middle", objectName: "Append-only" });
  kit.sourceLine(slide, "Sources: screenshots manifest, image 12; presentation/G3_Telco_Checklists.pdf, item 3.6 (audit_log: time, actor, step, outcome, inputs, outputs, model, prompt and policy version, duration).");

  speakerNotes(slide, {
    role: ROLE,
    seconds: 50,
    talk: [
      "What you just saw is recorded. This is the audit trail of a contact whose credit a supervisor approved. It is from our local stack, where keyword rules read the message.",
      "Each entry gives the step and its outcome, then who or what did it, the time taken and, where a step used one, the version. Agents read and decide by rule. Then a person: the supervisor's approval. Only then does the executor apply the credit, once.",
      "The database refuses to change or delete an entry. One limit: our programs share one database role, which could switch that rule off.",
    ],
    sources: "presentation/src/assets/screens/manifest.json, image 12 (the fifteen entries, actors and versions); presentation/G3_Telco_Checklists.pdf, item 3.6 (the audit_log table; triggers refuse update, delete and truncate; the limit of one database role); presentation/G3_Telco_Architecture.pdf, section 8.",
  });
}

module.exports = { segment: SEGMENT, slides: [previewSlide, screensSlide, auditSlide] };
