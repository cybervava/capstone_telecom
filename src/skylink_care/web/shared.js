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

export function trailItem(record) {
  const versions = [record.model, record.prompt_version, record.policy_version].filter(Boolean).join(" · ");
  const took = record.duration_ms === null ? "" : ` · ${record.duration_ms} ms`;
  return node(
    "li",
    { dataset: { outcome: record.outcome } },
    node("span", { className: "step", text: `${record.step} → ${record.outcome}` }),
    node("span", { className: "meta", text: `${record.actor}${took}${versions ? ` · ${versions}` : ""}` }),
  );
}
