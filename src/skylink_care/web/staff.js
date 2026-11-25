// SkyLink Staff Desk — the queue of what the assistant passed to a person, and the credits that
// wait for a person's decision. All text from the server is written with textContent.

import { api, messageItem, node, trailItem } from "./shared.js";

const el = (id) => document.getElementById(id);
const state = { token: null, role: null, who: null, timer: null, open: null };
const REFRESH_MS = 3000;

const call = (path, options = {}) => api(path, { ...options, token: state.token });
const money = (amount) => `$${amount}`;
const clock = (iso) => new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });

function ago(iso) {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
}

function fact(term, value) {
  return node("div", {}, node("dt", { text: term }), node("dd", { text: value ?? "—" }));
}

// --- credits awaiting a decision ---------------------------------------------------------------

function workingOf(request) {
  const working = request.working ?? {};
  const rows = [
    ["Monthly fee", working.monthly_fee_usd && money(working.monthly_fee_usd)],
    ["Outage", working.outage_minutes !== undefined && `${working.outage_minutes} min`],
    ["Billable hours", working.billable_hours],
    ["Multiplier", working.multiplier && `× ${working.multiplier}`],
    ["By the formula", working.computed_usd && money(working.computed_usd)],
    ["Raised to the minimum", working.floor_applied ? "yes" : "no"],
    ["Cut to the cycle cap", working.cap_applied ? "yes" : "no"],
  ];
  const list = node("dl", { className: "docket__facts" }, ...rows.filter(([, value]) => value !== undefined && value !== false && value !== null).map(([term, value]) => fact(term, String(value))));
  return node("details", { className: "working" }, node("summary", { text: "How it was worked out" }), list);
}

function slip(approval) {
  const request = approval.request ?? {};
  const note = node("input", { className: "slip__note" });
  note.maxLength = 500;
  note.placeholder = "Note (a reason is needed to reject)";
  note.setAttribute("aria-label", `Note for the decision on ${money(request.amount_usd)}`);
  const status = node("p", { className: "status" });
  status.setAttribute("role", "status");
  const decide = async (decision) => {
    if (decision === "reject" && (note.value.match(/\p{L}/gu) ?? []).length < 5) {
      status.textContent = "A rejection needs its reason: write it in the note.";
      note.focus();
      return;
    }
    buttons.forEach((button) => { button.disabled = true; });
    try {
      await call(`/v1/approvals/${approval.approval_id}/decision`, {
        method: "POST",
        body: { decision, request_hash: approval.request_hash, ...(note.value.trim() ? { note: note.value.trim() } : {}) },
      });
      await refresh();
    } catch (error) {
      status.textContent = `Not recorded: ${error.message}`;
      buttons.forEach((button) => { button.disabled = false; });
    }
  };
  const approve = node("button", { text: "Approve" });
  const reject = node("button", { className: "quiet", text: "Reject" });
  const buttons = [approve, reject];
  approve.type = reject.type = "button";
  approve.addEventListener("click", () => decide("approve"));
  reject.addEventListener("click", () => decide("reject"));
  return node(
    "li",
    { className: "slip", dataset: { level: approval.required_role } },
    node("p", { className: "slip__amount", text: money(request.amount_usd) }),
    node("span", { className: "chip", dataset: { state: approval.required_role }, text: `needs ${approval.required_role === "supervisor" ? "a supervisor" : "an agent"}` }),
    node(
      "dl",
      { className: "docket__facts" },
      fact("Complaint", request.complaint_ref),
      fact("Outage", request.outage_id),
      fact("Service", request.service_id),
      fact("Policy", request.policy_version),
      fact("Lapses", clock(approval.expires_at)),
    ),
    workingOf(request),
    node("div", { className: "slip__decide" }, note, approve, reject),
    status,
  );
}

// --- with a person -------------------------------------------------------------------------------

function queueRow(item) {
  const open = node(
    "button",
    { className: "queue__row" },
    node("span", { className: "queue__priority", text: `P${item.priority}` }),
    node("span", { className: "queue__reason", text: item.reason.replaceAll("_", " ").toLowerCase() }),
    node("span", { className: "queue__ref", text: item.complaint_ref ?? "" }),
    node("span", { className: "queue__age", text: ago(item.created_at) }),
  );
  open.type = "button";
  open.setAttribute("aria-pressed", String(state.open?.handoff_id === item.handoff_id));
  open.addEventListener("click", () => openCase(item));
  return node("li", { dataset: { priority: item.priority } }, open);
}

async function openCase(item) {
  state.open = item;
  el("case").hidden = false;
  el("case-ref").textContent = item.complaint_ref ?? "—";
  el("case-summary").textContent = item.summary;
  el("case-status").textContent = "";
  el("done-note").value = "";
  el("done").disabled = false;
  el("case-heading").textContent = `Case · ${item.reason.replaceAll("_", " ").toLowerCase()}`;
  try {
    const [contact, trail] = await Promise.all([
      call(`/v1/contacts/${item.contact_id}`),
      call(`/v1/contacts/${item.contact_id}/audit`),
    ]);
    el("case-thread").replaceChildren(...contact.messages.map((message) => messageItem(message, "Customer")));
    el("case-trail").replaceChildren(...trail.map(trailItem));
  } catch (error) {
    el("case-status").textContent = `Could not load the case: ${error.message}`;
  }
  el("case").scrollIntoView({ block: "nearest", behavior: "smooth" });
  await refresh();
}

function closeCase() {
  state.open = null;
  el("case").hidden = true;
}

// --- operations ----------------------------------------------------------------------------------

const MAY_CLOSE_OPS = new Set(["ops", "supervisor"]);

function opsSlip(item) {
  const status = node("p", { className: "status" });
  status.setAttribute("role", "status");
  const parts = [
    node("p", { className: "slip__kind", text: item.kind.replaceAll("_", " ").toLowerCase() }),
    node("p", { className: "summary", text: item.summary }),
    node(
      "dl",
      { className: "docket__facts" },
      fact("About", item.subject),
      fact("Seen", `${item.occurrences} time${item.occurrences === 1 ? "" : "s"}`),
      fact("First", ago(item.created_at)),
      fact("Last", ago(item.last_seen_at)),
    ),
  ];
  if (MAY_CLOSE_OPS.has(state.role)) {
    const note = node("input", { className: "slip__note" });
    note.maxLength = 500;
    note.placeholder = "What was done (optional, kept in the audit log)";
    note.setAttribute("aria-label", `What was done about ${item.subject}`);
    const done = node("button", { text: "Mark done" });
    done.type = "button";
    done.addEventListener("click", async () => {
      done.disabled = true;
      try {
        const text = note.value.trim();
        await call(`/v1/ops/${item.item_id}/done`, { method: "POST", body: text ? { note: text } : {} });
        await refresh();
      } catch (error) {
        status.textContent = `Not recorded: ${error.message}`;
        done.disabled = false;
      }
    });
    parts.push(node("div", { className: "slip__decide" }, note, done));
  }
  const made = node("li", { className: "slip", dataset: { level: item.priority <= 1 ? "supervisor" : "agent" } }, ...parts, status);
  made.dataset.id = item.item_id;
  return made;
}

function showOps({ items, total }) {
  // The list is the most urgent hundred. The tally is all of them.
  el("ops-count").textContent = total > items.length ? `${items.length} of ${total}` : total;
  // An item someone is typing a note on is not redrawn under their hands.
  const busy = el("ops").contains(document.activeElement);
  const shown = [...el("ops").children].map((child) => child.dataset.id).join();
  if (!busy || shown !== items.map((item) => item.item_id).join()) {
    el("ops").replaceChildren(...items.map(opsSlip));
  }
}

function loadUnknown() {
  // A figure that could not be read is not left standing as if it were current.
  el("ops-load").dataset.state = "unknown";
  el("ops-load").textContent = "The assistant's backlog could not be read.";
}

function showLoad({ waiting_contacts: waiting, oldest_seconds: oldest, behind }) {
  const line = el("ops-load");
  const longest = oldest >= 90 ? `${Math.round(oldest / 60)} min` : `${oldest} s`;
  const contacts = `${waiting} ${waiting === 1 ? "contact is" : "contacts are"}`;
  line.dataset.state = behind ? "behind" : "keeping-up";
  if (behind) {
    line.textContent = `The assistant is behind: ${contacts} waiting for it, the longest for ${longest}. Customers are told that replies will take longer.`;
  } else if (waiting) {
    line.textContent = `The assistant is keeping up: ${contacts} in hand, the longest for ${longest}.`;
  } else {
    line.textContent = "The assistant is keeping up: no contact is waiting for it.";
  }
}

// --- loading -------------------------------------------------------------------------------------

function showApprovals(approvals) {
  el("approvals-count").textContent = approvals.length;
  // A slip someone is typing a note on, or has opened, is not redrawn under their hands.
  const busy = el("approvals").contains(document.activeElement) || el("approvals").querySelector("details[open]");
  const shown = [...el("approvals").children].map((child) => child.dataset.id).join();
  if (!busy || shown !== approvals.map((a) => a.approval_id).join()) {
    el("approvals").replaceChildren(...approvals.map((approval) => {
      const made = slip(approval);
      made.dataset.id = approval.approval_id;
      return made;
    }));
  }
}

function showQueue(queue) {
  el("queue-count").textContent = queue.length;
  el("queue").replaceChildren(...queue.map(queueRow));
}

async function refresh() {
  if (!state.token) return;
  // Credits are decided by agents and supervisors; operations staff are not shown them.
  const credits = state.role === "ops" ? Promise.resolve([]) : call("/v1/approvals");
  // Each list is loaded and shown for itself: one that fails does not blank the others.
  const lists = [
    ["credits", credits, showApprovals],
    ["queue", call("/v1/handoffs"), showQueue],
    ["operations", call("/v1/ops"), showOps],
    ["load", call("/v1/ops/load"), showLoad, loadUnknown],
  ];
  const settled = await Promise.allSettled(lists.map(([, loading]) => loading));
  const failed = [];
  settled.forEach((result, index) => {
    const [name, , show, onFailure] = lists[index];
    if (result.status === "fulfilled") {
      show(result.value);
    } else {
      failed.push(`${name} (${result.reason.message})`);
      onFailure?.();
    }
  });
  const updated = `Signed in as ${state.who}. Updated ${new Date().toLocaleTimeString("en-IN")}.`;
  el("sign-status").textContent = failed.length ? `${updated} Could not load: ${failed.join(", ")}.` : updated;
}

el("sign-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const role = el("role").value;
  const name = el("name").value.trim().toLowerCase();
  try {
    const signed = await api("/v1/dev/token", { method: "POST", body: { role, name, staff_key: el("staff-key").value } });
    Object.assign(state, { token: signed.token, role, who: `${role}:${name}` });
    el("staff-key").value = "";
    closeCase();
    clearInterval(state.timer);
    state.timer = setInterval(refresh, REFRESH_MS);
    await refresh();
  } catch (error) {
    state.token = null;
    clearInterval(state.timer);
    el("sign-status").textContent = `Not signed in: ${error.message}`;
  }
});

el("case-close").addEventListener("click", closeCase);

el("done-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.open) return;
  const note = el("done-note").value.trim();
  el("done").disabled = true;
  try {
    await call(`/v1/handoffs/${state.open.handoff_id}/done`, { method: "POST", body: note ? { note } : {} });
    closeCase();
    await refresh();
  } catch (error) {
    el("case-status").textContent = `Not recorded: ${error.message}`;
    el("done").disabled = false;
  }
});
