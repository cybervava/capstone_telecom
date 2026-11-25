// What both desks share: talking to the API and drawing a conversation and an audit trail.
// All text from the server is written with textContent, never as HTML.

export async function api(path, { method = "GET", body, token } = {}) {
  const response = await fetch(path, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({ success: false, error: { message: "unreadable response" } }));
  if (!payload.success) {
    const error = new Error(payload.error?.message ?? `request failed (${response.status})`);
    error.payload = payload.error;
    error.status = response.status;
    throw error;
  }
  return payload.data;
}

export function node(tag, { className, text, dataset } = {}, ...children) {
  const made = document.createElement(tag);
  if (className) made.className = className;
  if (text !== undefined) made.textContent = text;
  Object.assign(made.dataset, dataset ?? {});
  made.append(...children);
  return made;
}

export function messageItem({ direction, author, body }, customerLabel = "You") {
  const who = direction === "inbound" ? customerLabel : author.replace("agent:", "assistant · ");
  return node("li", { dataset: { direction } }, node("span", { className: "who", text: who }), node("p", { text: body }));
}

// "guide-al-ax18 §wifi v1.1" → "§wifi": enough to tell the sections of one guide apart.
const sectionOf = (citation) => String(citation ?? "").split(" ")[1] ?? String(citation ?? "");
const QUOTATION = { QUOTED: "quoted", NONE: "none offered", NOT_USED: "not used" };
const quotation = (outcome) =>
  QUOTATION[outcome] ?? `left out (${String(outcome ?? "").toLowerCase().replaceAll("_", " ")})`;

// How an answer from a guide was put together, in a line: what the search found, with its
// ranking, and who chose among it. The rest of every entry is one click further.
function inShort({ step, outputs }) {
  const out = outputs ?? {};
  if (step === "troubleshooting.retrieve" && Array.isArray(out.found) && out.found.length) {
    return `found ${out.found.map((each) => `${sectionOf(each.citation)} ${each.score}`).join(" · ")}`;
  }
  if (step === "troubleshooting.answer" && out.section) {
    const byModel = out.chosen_by === "model";
    const rules = byModel && out.rules_chose !== out.section ? ` (the rules alone: ${sectionOf(out.rules_chose)})` : "";
    const chosen = byModel ? `the model chose ${sectionOf(out.section)}${rules}` : `the rules' choice stands: ${sectionOf(out.section)}`;
    return `${chosen} · customer's words ${quotation(out.customer_words)} · guide's words ${quotation(out.guide_words)}`;
  }
  return null;
}

function shown(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) {
    const lines = value.map((each) => (each !== null && typeof each === "object" ? Object.values(each).join(" · ") : String(each)));
    return lines.join("\n") || "—";
  }
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function recorded(title, values) {
  const entries = Object.entries(values ?? {});
  if (!entries.length) return [];
  const rows = entries.map(([name, value]) => node("div", {}, node("dt", { text: name.replaceAll("_", " ") }), node("dd", { text: shown(value) })));
  return [node("p", { className: "trail__kind", text: title }), node("dl", { className: "trail__facts" }, ...rows)];
}

export function trailItem(record) {
  const versions = [record.model, record.prompt_version, record.policy_version].filter(Boolean).join(" · ");
  const took = record.duration_ms === null ? "" : ` · ${record.duration_ms} ms`;
  const meta = node("span", { className: "meta", text: `${record.actor}${took}${versions ? ` · ${versions}` : ""}` });
  // What went in and what came out, as it was recorded: already redacted by the server. The
  // toggle sits at the end of the actor's line, so that a closed entry is no taller for it.
  const details = [...recorded("Went in", record.inputs), ...recorded("Came out", record.outputs)];
  const parts = [
    node("span", { className: "step", text: `${record.step} → ${record.outcome}` }),
    details.length
      ? node("div", { className: "trail__line" }, meta, node("details", { className: "trail__detail" }, node("summary", { text: "details" }), ...details))
      : meta,
  ];
  const short = inShort(record);
  if (short) parts.push(node("span", { className: "note", text: short }));
  return node("li", { dataset: { outcome: record.outcome } }, ...parts);
}
