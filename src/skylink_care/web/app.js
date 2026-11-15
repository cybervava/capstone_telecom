// SkyLink Care Desk — demonstration console.
// All text from the server is written with textContent, never as HTML.

const el = (id) => document.getElementById(id);
const state = { token: null, staffToken: null, contactId: null, seen: 0, timer: null };

const newId = () => crypto.randomUUID().replaceAll("-", "");

async function api(path, { method = "GET", body, token = state.token } = {}) {
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
    throw error;
  }
  return payload.data;
}

async function devToken(role, customerId) {
  const data = await api("/v1/dev/token", {
    method: "POST",
    body: customerId ? { role, customer_id: customerId } : { role },
    token: null,
  });
  return data.token;
}

function addMessage({ direction, author, body }) {
  const item = document.createElement("li");
  item.dataset.direction = direction;
  const who = document.createElement("span");
  who.className = "who";
  who.textContent = direction === "inbound" ? "You" : author.replace("agent:", "assistant · ");
  const text = document.createElement("p");
  text.textContent = body;
  item.append(who, text);
  el("thread").append(item);
  item.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function showDocket(contact, latencyMs) {
  el("docket").hidden = false;
  el("docket-ref").textContent = contact.complaint_ref;
  if (latencyMs !== undefined) el("docket-latency").textContent = `${latencyMs} ms`;
  el("docket-status").textContent = contact.status;
}

function showNetwork(contact) {
  const chip = document.createElement("span");
  chip.className = "chip";
  chip.dataset.state = contact.network_state ?? "none";
  const labels = { ACTIVE: "outage confirmed", CONFIRMED_CLEAR: "none reported", UNKNOWN: "not verified" };
  chip.textContent = labels[contact.network_state] ?? "not checked";
  const parts = [chip];
  if (contact.outage_id) {
    const outage = document.createElement("span");
    outage.className = "outage-id";
    outage.textContent = contact.outage_id;
    parts.push(outage);
  }
  el("docket-network").replaceChildren(...parts);
}

async function refresh() {
  if (!state.contactId) return;
  try {
    const contact = await api(`/v1/contacts/${state.contactId}`);
    el("docket-status").textContent = contact.status;
    showNetwork(contact);
    for (const message of contact.messages.filter((m) => m.message_id > state.seen)) {
      if (message.direction === "outbound") addMessage(message);
      state.seen = Math.max(state.seen, message.message_id);
    }
  } catch (error) {
    el("persona-status").textContent = `Could not refresh: ${error.message}`;
  }
}

async function refreshTrail() {
  if (!state.contactId || !state.staffToken) return;
  const trail = await api(`/v1/contacts/${state.contactId}/audit`, { token: state.staffToken });
  const items = trail.map((record) => {
    const item = document.createElement("li");
    item.dataset.outcome = record.outcome;
    const step = document.createElement("span");
    step.className = "step";
    step.textContent = `${record.step} → ${record.outcome}`;
    const meta = document.createElement("span");
    meta.className = "meta";
    const versions = [record.model, record.prompt_version, record.policy_version].filter(Boolean).join(" · ");
    const took = record.duration_ms === null ? "" : ` · ${record.duration_ms} ms`;
    meta.textContent = `${record.actor}${took}${versions ? ` · ${versions}` : ""}`;
    item.append(step, meta);
    return item;
  });
  el("trail").replaceChildren(...items);
}

el("persona-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const customerId = el("customer-id").value.trim().toUpperCase();
  try {
    state.token = await devToken(customerId ? "customer" : "guest", customerId || undefined);
    Object.assign(state, { contactId: null, seen: 0 });
    el("thread").replaceChildren();
    el("trail").replaceChildren();
    el("docket").hidden = true;
    el("message").disabled = false;
    el("send").disabled = false;
    el("message").placeholder = "Describe the problem, for example: my internet has been down since morning";
    el("persona-status").textContent = customerId
      ? `Signed in as ${customerId} (identity verified by the channel).`
      : "Arrived as a guest (identity not known).";
    el("message").focus();
  } catch (error) {
    el("persona-status").textContent = `Could not start a session: ${error.message}`;
  }
});

el("message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = el("message").value.trim();
  if (!text || !state.token) return;
  el("message").value = "";
  addMessage({ direction: "inbound", author: "customer", body: text });
  try {
    if (!state.contactId) {
      const started = performance.now();
      const contact = await api("/v1/contacts", {
        method: "POST",
        body: { channel: "chat", client_request_id: newId(), text },
      });
      state.contactId = contact.contact_id;
      showDocket(contact, Math.round(performance.now() - started));
      el("trail-refresh").disabled = false;
      clearInterval(state.timer);
      state.timer = setInterval(() => { refresh(); refreshTrail().catch(() => {}); }, 1200);
    } else {
      await api(`/v1/contacts/${state.contactId}/messages`, {
        method: "POST",
        body: { client_message_id: newId(), text },
      });
    }
  } catch (error) {
    addMessage({ direction: "outbound", author: "system", body: `Could not send: ${error.message}` });
  }
});

// The audit trail is a staff view: it opens with the staff key, asked for when someone opens it.
el("trail-refresh").addEventListener("click", async () => {
  if (!state.staffToken) {
    el("trail-form").hidden = false;
    el("trail-key").focus();
    return;
  }
  await refreshTrail().catch((error) => {
    el("persona-status").textContent = `Could not refresh the staff view: ${error.message}`;
  });
});

el("trail-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const signed = await api("/v1/dev/token", {
      method: "POST",
      body: { role: "ops", staff_key: el("trail-key").value },
      token: null,
    });
    state.staffToken = signed.token;
    el("trail-key").value = "";
    el("trail-form").hidden = true;
    el("trail-refresh").textContent = "Refresh";
    await refreshTrail();
  } catch (error) {
    el("persona-status").textContent = `Could not open the staff view: ${error.message}`;
  }
});
