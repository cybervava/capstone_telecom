// SkyLink Care Desk — demonstration console.
// All text from the server is written with textContent, never as HTML.

const el = (id) => document.getElementById(id);
const state = { token: null, staffToken: null, contactId: null, seen: 0, timer: null, awaiting: false, delayed: false };
const POLL_MS = 1200;
// While the assistant is behind, asking it every second gains nothing and adds to its load.
const POLL_WHEN_DELAYED_MS = 5000;

const newId = () => crypto.randomUUID().replaceAll("-", "");

// The demo console can open the desk as a customer, with what they will say:
//   /?customer=C0000005&say=first line&say=second line
// The session starts by itself. Each line waits in the message box, the next one once the
// assistant has answered; nothing is sent without a click, and a line can be changed first.
const scripted = (() => {
  const params = new URLSearchParams(location.search);
  const customer = (params.get("customer") ?? "").trim().toUpperCase();
  return {
    customer: /^C\d{7}$/.test(customer) ? customer : null,
    lines: params.getAll("say").map((line) => line.trim().slice(0, 4000)).filter(Boolean),
    next: 0,
    offered: false,
    on: false,
  };
})();

function offerNextLine() {
  const box = el("message");
  if (!scripted.on || scripted.offered || box.disabled || box.value.trim()) return;
  if (scripted.next >= scripted.lines.length) return;
  box.value = scripted.lines[scripted.next];
  scripted.offered = true;
}

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
  if (contact.network_checked_at) {
    // The server takes "none reported" back when the check is no longer recent; the time says
    // how old the rest is.
    // Indian time, and said to be: the same clock the assistant's messages name.
    const time = new Date(contact.network_checked_at).toLocaleTimeString("en-IN", {
      hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata",
    });
    chip.title = `Last checked at ${time} IST`;
    chip.textContent += ` · ${time} IST`;
  }
  const parts = [chip];
  if (contact.outage_id) {
    const outage = document.createElement("span");
    outage.className = "outage-id";
    outage.textContent = contact.outage_id;
    parts.push(outage);
  }
  el("docket-network").replaceChildren(...parts);
}

function showDelay(delayed) {
  state.delayed = Boolean(delayed);
  // Said only while a reply is awaited: once it has come there is nothing left to wait for.
  el("docket-delay").hidden = !(state.delayed && state.awaiting);
}

function poll() {
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    await refresh();
    refreshTrail().catch(() => {});
    poll();
  }, state.delayed ? POLL_WHEN_DELAYED_MS : POLL_MS);
}

async function refresh() {
  if (!state.contactId) return;
  try {
    const contact = await api(`/v1/contacts/${state.contactId}`);
    el("docket-status").textContent = contact.status;
    showNetwork(contact);
    for (const message of contact.messages.filter((m) => m.message_id > state.seen)) {
      if (message.direction === "outbound") {
        addMessage(message);
        state.awaiting = false;
        offerNextLine();
      }
      state.seen = Math.max(state.seen, message.message_id);
    }
    showDelay(contact.reply_delayed);
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
    Object.assign(state, { contactId: null, seen: 0, awaiting: false });
    showDelay(false);
    el("thread").replaceChildren();
    el("trail").replaceChildren();
    el("docket").hidden = true;
    el("message").disabled = false;
    el("send").disabled = false;
    el("message").placeholder = "Describe the problem, for example: my internet has been down since morning";
    el("persona-status").textContent = customerId
      ? `Signed in as ${customerId} (identity verified by the channel).`
      : "Arrived as a guest (identity not known).";
    // A new session for the console's customer starts its lines again; anyone else, none.
    Object.assign(scripted, { on: customerId === scripted.customer, next: 0, offered: false });
    if (scripted.on && scripted.lines.length) {
      el("persona-status").textContent += " The demo console's lines wait in the message box.";
    }
    offerNextLine();
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
  if (scripted.offered) Object.assign(scripted, { next: scripted.next + 1, offered: false });
  addMessage({ direction: "inbound", author: "customer", body: text });
  try {
    if (!state.contactId) {
      const started = performance.now();
      const contact = await api("/v1/contacts", {
        method: "POST",
        body: { channel: "chat", client_request_id: newId(), text },
      });
      state.contactId = contact.contact_id;
      state.awaiting = true;
      showDocket(contact, Math.round(performance.now() - started));
      showDelay(contact.reply_delayed);
      el("trail-refresh").disabled = false;
      poll();
    } else {
      const sent = await api(`/v1/contacts/${state.contactId}/messages`, {
        method: "POST",
        body: { client_message_id: newId(), text },
      });
      state.awaiting = true;
      showDelay(sent.reply_delayed);
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

if (scripted.customer) {
  el("customer-id").value = scripted.customer;
  el("persona-form").requestSubmit();
}

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
