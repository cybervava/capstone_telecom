// SkyLink Demo Console. It plays the network, the field force and the failures through the
// simulator's admin API, opens the customer desk with what the customer will say, and reads back
// what the platform wrote. Text from the server is written with textContent, never as HTML.

import { BASE_SEARCH, SCENES } from "./scenes.js";

const el = (id) => document.getElementById(id);
// The console is served at <base>/console/: on the demo host the simulator sits under /sim.
const BASE = location.pathname.replace(/\/console(\/.*)?$/, "");
const POLL_MS = 4000;
const FREE = "free";
const SEARCH = "/admin/v1/customers/search";
const IST = { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" };
const TEMPLATES = { "outage_started.v1": "Outage notices", "outage_resolved.v1": "Restored notices" };

// --- small things ----------------------------------------------------------------------------

function node(tag, { className, text, dataset, attrs } = {}, ...children) {
  const made = document.createElement(tag);
  if (className) made.className = className;
  if (text !== undefined) made.textContent = text;
  Object.assign(made.dataset, dataset ?? {});
  for (const [name, value] of Object.entries(attrs ?? {})) made.setAttribute(name, value);
  made.append(...children);
  return made;
}

function button(text, onClick, { className, disabled } = {}) {
  const made = node("button", { className, text });
  made.type = "button";
  made.disabled = Boolean(disabled);
  made.addEventListener("click", onClick);
  return made;
}

// A desk opened from here gets no opener: on the demo host the desks share the console's origin,
// and a window it opened would otherwise be handed a copy of its session storage, the key with it.
function link(text, href, target) {
  const made = node("a", { text });
  made.href = href;
  if (target) {
    made.target = target;
    made.rel = "noopener";
  }
  return made;
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const time = (iso) => (iso ? `${new Date(iso).toLocaleTimeString("en-IN", IST)} IST` : "—");
const when = (iso) =>
  new Date(iso).toLocaleString("en-IN", { weekday: "short", day: "numeric", month: "short", ...IST });

// Browser storage may be off (a private window, blocked site data). The console works without
// it and only forgets on reload.
function storage(kind) {
  try {
    return window[kind];
  } catch {
    return null;
  }
}

function recall(kind, key, fallback) {
  try {
    const value = storage(kind)?.getItem(key);
    return value == null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

function keep(kind, key, value) {
  try {
    storage(kind)?.setItem(key, JSON.stringify(value));
  } catch {
    // See recall.
  }
}

function forget(kind, key) {
  try {
    storage(kind)?.removeItem(key);
  } catch {
    // See recall.
  }
}

function defaultDesk() {
  // On the demo host the desks are the site itself; on a laptop, the care API's port.
  return BASE ? `${location.origin}/` : `${location.protocol}//${location.hostname}:8080/`;
}

// --- state -------------------------------------------------------------------------------------

const state = {
  key: recall("sessionStorage", "console.key", ""),
  scene: recall("sessionStorage", "console.scene", SCENES[0].id),
  // Per scene: the customer cast, the options chosen, the scene's outage and what each cue did.
  plays: recall("sessionStorage", "console.plays", {}),
  // Customers cast already, so that no two scenes share one.
  used: recall("sessionStorage", "console.used", []),
  desk: recall("localStorage", "console.desk", null) ?? defaultDesk(),
  world: { outages: [], chaos: [], faults: [], requests: {} },
  evidence: null,
  // The connection line once connected, to put back after a poll that failed.
  connected: "",
  timer: null,
  busy: false,
};

const savePlays = () => keep("sessionStorage", "console.plays", state.plays);
const sceneOf = (id) => SCENES.find((scene) => scene.id === id);

function deskUrl(customerId, lines = []) {
  const url = new URL(state.desk);
  if (customerId) url.searchParams.set("customer", customerId);
  for (const line of lines) url.searchParams.append("say", line);
  return url.href;
}

const staffUrl = () => new URL("staff.html", state.desk).href;

// --- the simulator -----------------------------------------------------------------------------

async function sim(path, { method = "GET", body, params } = {}) {
  const url = new URL(BASE + path, location.origin);
  for (const [name, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(name, String(value));
  }
  const response = await fetch(url, {
    method,
    headers: { "X-API-Key": state.key, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message ?? `the simulator answered ${response.status}`);
    error.status = response.status;
    error.code = payload.error?.code;
    throw error;
  }
  return payload;
}

const setLine = (serviceId, fails) =>
  sim("/admin/v1/line-faults", { method: "POST", body: { service_id: serviceId, fault: fails } });
const setChaos = (target, mode) => sim("/admin/v1/chaos", { method: "POST", body: { target, mode } });
const resolveOutage = (outageId) =>
  sim(`/admin/v1/outages/${encodeURIComponent(outageId)}/resolve`, { method: "POST" });

function refused(target) {
  const counts = state.world.requests[target] ?? {};
  return Object.entries(counts)
    .filter(([status]) => Number(status) >= 500)
    .reduce((sum, [, count]) => sum + count, 0);
}

function outageNodes() {
  const nodes = new Map();
  for (const outage of state.world.outages) {
    for (const nodeId of outage.affected_node_ids) nodes.set(nodeId, outage.outage_id);
  }
  return nodes;
}

// --- connection --------------------------------------------------------------------------------

function setConnection(text, tone = "") {
  el("connection").textContent = text;
  el("connection").dataset.tone = tone;
}

const REFUSED = new Set(["UNAUTHENTICATED", "FORBIDDEN"]);

function trouble(error) {
  // The simulator's own refusal names its code; a 401 without one is the site's sign-in.
  if (REFUSED.has(error.code)) return "That key was refused. It is OSS_KEY_ADMIN.";
  if (error.status === 401) return "The site's sign-in has lapsed: reload the page and sign in again.";
  return `Could not reach the simulator: ${error.message}`;
}

async function connect() {
  setConnection("Connecting…");
  try {
    await sim("/admin/v1/chaos");
    keep("sessionStorage", "console.key", state.key);
    const health = await fetch(new URL(`${BASE}/health`, location.origin))
      .then((response) => response.json())
      .catch(() => null);
    const size = health?.customers ? ` · ${health.customers.toLocaleString("en-IN")} customers` : "";
    state.connected = `Connected to the simulator${size}.`;
    setConnection(state.connected, "good");
    el("key-form").hidden = true;
    el("forget").hidden = false;
    el("console").hidden = false;
    el("end-all").disabled = false;
    await refreshWorld();
    await selectScene(state.scene);
    poll();
  } catch (error) {
    if (REFUSED.has(error.code)) forget("sessionStorage", "console.key");
    el("key-form").hidden = false;
    setConnection(trouble(error), "bad");
  }
}

function poll() {
  clearTimeout(state.timer);
  state.timer = setTimeout(async () => {
    if (document.visibilityState === "visible") {
      try {
        await refreshWorld();
        if (el("connection").dataset.tone === "lost") setConnection(state.connected, "good");
      } catch (error) {
        setConnection(`Lost the simulator. ${trouble(error)}`, "lost");
      }
    }
    poll();
  }, POLL_MS);
}

// --- what is in force --------------------------------------------------------------------------

async function refreshWorld() {
  const [outages, chaos, faults, counts] = await Promise.all([
    sim("/admin/v1/outages", { params: { active: "true" } }),
    sim("/admin/v1/chaos"),
    sim("/admin/v1/line-faults"),
    sim("/admin/v1/state"),
  ]);
  state.world = {
    outages: outages.outages,
    chaos: chaos.rules,
    faults: faults.line_faults,
    requests: counts.requests,
  };
  renderWorld();
  await refreshEvidence();
}

// A change made from the board: shown at once, and the scene redrawn in case it depended on it.
async function act(change) {
  try {
    await change();
  } catch (error) {
    setConnection(`Not done: ${error.message}`, "bad");
  }
  await refreshWorld().catch(() => {});
  if (state.scene !== FREE) renderScript();
}

function worldGroup(title, rows, empty) {
  return node(
    "section",
    { className: "world__group" },
    node("h3", { text: title }),
    rows.length ? node("ul", { className: "world__list" }, ...rows) : node("p", { className: "quiet-note", text: empty }),
  );
}

function worldRow(tone, label, meta, control) {
  return node(
    "li",
    { dataset: tone ? { tone } : {} },
    node("span", { className: "mono", text: label }),
    node("span", { className: "world__meta", text: meta }),
    control,
  );
}

function renderWorld() {
  const { outages, chaos, faults } = state.world;
  el("world").replaceChildren(
    worldGroup(
      "Outages",
      outages.map((outage) =>
        worldRow(
          "outage",
          outage.outage_id,
          `${outage.affected_node_ids.join(", ")} · ${outage.cause.toLowerCase().replaceAll("_", " ")}` +
            ` · began ${time(outage.started_at)} · ETA ${time(outage.eta)}`,
          button("Resolve", () => act(() => resolveOutage(outage.outage_id)), { className: "quiet" }),
        ),
      ),
      "No outage in force.",
    ),
    worldGroup(
      "Failing APIs",
      chaos.map((rule) =>
        worldRow(
          "unknown",
          `${rule.target} · ${rule.mode.replaceAll("_", " ")}`,
          rule.expires_at ? `until ${time(rule.expires_at)}` : "until it is ended",
          button("End", () => act(() => setChaos(rule.target, "ok")), { className: "quiet" }),
        ),
      ),
      "Every API answers normally.",
    ),
    worldGroup(
      "Lines made to fail",
      faults.map((fault) =>
        worldRow(
          "",
          fault.service_id,
          fault.result === "FAIL" ? "fails its remote test" : "its test is inconclusive",
          button("Pass", () => act(() => setLine(fault.service_id, false)), { className: "quiet" }),
        ),
      ),
      "No line made to fail.",
    ),
  );
}

// --- scenes ------------------------------------------------------------------------------------

function progressOf(scene) {
  const play = state.plays[scene.id];
  if (!play) return "";
  const actions = scene.cues.flatMap((cue, index) => (cue.do ? [index] : []));
  const done = actions.filter((index) => play.done[index]).length;
  if (!actions.length || done === 0) return "cast";
  return done === actions.length ? "played" : "playing";
}

function renderSceneList() {
  const entries = [...SCENES, { id: FREE, tag: "Anything else", title: "Free play" }];
  el("scene-list").replaceChildren(
    ...entries.map((scene, index) => {
      const item = button("", () => selectScene(scene.id), { className: "scene-list__item" });
      item.setAttribute("aria-current", scene.id === state.scene ? "step" : "false");
      item.dataset.progress = scene.id === FREE ? "" : progressOf(scene);
      item.append(
        node("span", { className: "scene-list__n", text: scene.id === FREE ? "+" : String(index + 1) }),
        node("span", { className: "scene-list__title", text: scene.title }),
        node("span", { className: "scene-list__tag", text: scene.tag }),
      );
      return node("li", {}, item);
    }),
  );
}

async function selectScene(id) {
  state.scene = (sceneOf(id) || id === FREE) ? id : SCENES[0].id;
  keep("sessionStorage", "console.scene", state.scene);
  renderSceneList();
  let notice = null;
  if (state.scene !== FREE && !state.plays[state.scene]) notice = await cast(sceneOf(state.scene));
  renderScript(notice);
  await refreshEvidence();
}

// --- casting -----------------------------------------------------------------------------------

function defaultOptions(scene) {
  return {
    ...(scene.minutesAgoOption ? { minutesAgo: scene.minutesAgoOption } : {}),
    ...(scene.staleFeedOption ? { staleFeed: false } : {}),
    ...(scene.chooseRouter ? { routerModel: "" } : {}),
  };
}

async function castFor(scene, options) {
  // Only customers the platform has written nothing for: a scene played again, or after a
  // rehearsal in another browser, gets a fresh customer rather than one with a visit booked.
  const params = { ...BASE_SEARCH, ...scene.cast, untouched: "true", limit: 50 };
  let routers;
  if (scene.chooseRouter) {
    const { customers } = await sim(SEARCH, { params });
    routers = [...new Set(customers.map((c) => c.router_model))].sort();
    if (options.routerModel) params.router_model = options.routerModel;
  }
  const busy = outageNodes();
  const failing = new Set(state.world.faults.map((fault) => fault.service_id));
  const taken = new Set(state.used);
  const free = (c) => !taken.has(c.customer_id) && !failing.has(c.service_id);
  if (scene.preferOutage) {
    for (const [nodeId, outageId] of busy) {
      const { customers } = await sim(SEARCH, { params: { ...params, node_id: nodeId, limit: 20 } });
      const pick = customers.find(free);
      if (pick) return { cast: pick, outageId, routers };
    }
  }
  const { customers } = await sim(SEARCH, { params });
  // Away from the nodes other scenes cast on, so that one scene's outage leaves the others alone.
  const elsewhere = new Set(Object.values(state.plays).map((play) => play.cast.true_node_id));
  const healthy = customers.filter((c) => free(c) && !busy.has(c.true_node_id));
  const pick = healthy.find((c) => !elsewhere.has(c.true_node_id)) ?? healthy[0];
  return pick ? { cast: pick, routers } : null;
}

async function castById(raw) {
  const customerId = raw.trim().toUpperCase();
  if (!/^C\d{7}$/.test(customerId)) throw new Error("a customer ID looks like C0001234");
  const ask = (params) => sim(SEARCH, { params: { customer_id: customerId, limit: 5, ...params } });
  // Their active broadband line if they have one, as the platform itself prefers.
  let { customers } = await ask({ service_type: "broadband", service_status: "active" });
  if (!customers.length) ({ customers } = await ask({ service_status: "active" }));
  if (!customers.length) ({ customers } = await ask({}));
  if (!customers.length) throw new Error(`there is no customer ${customerId}`);
  return { cast: customers[0], outageId: outageNodes().get(customers[0].true_node_id) };
}

// Null once a customer is cast; else why not, to show in the scene.
async function cast(scene, { customerId, options, before } = {}) {
  const chosen = { ...defaultOptions(scene), ...(before?.options ?? {}), ...(options ?? {}) };
  if (state.scene === scene.id) renderScript("Finding a customer for this scene…");
  try {
    const found = customerId ? await castById(customerId) : await castFor(scene, chosen);
    if (!found) return "No free customer fits this scene. End what is in force, or cast one by ID.";
    const routers = found.routers ?? before?.routers;
    const play = { cast: found.cast, options: chosen, outageId: null, done: {}, failed: {}, routers };
    if (found.outageId) {
      play.outageId = found.outageId;
      const declare = scene.cues.findIndex((cue) => cue.do === "declare");
      if (declare >= 0) play.done[declare] = `${found.outageId} already covers ${found.cast.true_node_id}`;
    }
    if (scene.passLine) await setLine(found.cast.service_id, false);
    state.plays[scene.id] = play;
    state.used = [...new Set([...state.used, found.cast.customer_id])];
    keep("sessionStorage", "console.used", state.used);
    savePlays();
    return null;
  } catch (error) {
    return `Could not cast this scene: ${error.message}`;
  }
}

// What a scene started, ended: its own outage, a line it made fail, the Network API it took down.
async function finish(scene) {
  const play = state.plays[scene.id];
  if (!play) return;
  if (play.declared && play.outageId) {
    await resolveOutage(play.outageId).catch((error) => {
      if (error.status !== 404) throw error;
    });
  }
  if (play.lineFailed) await setLine(play.cast.service_id, false);
  if (play.networkDown) {
    await setChaos("network", "ok");
    await setChaos("network_feed", "ok");
  }
  Object.assign(play, { declared: false, lineFailed: false, networkDown: false });
  savePlays();
}

async function recast(scene, how = {}) {
  const before = state.plays[scene.id];
  let notice = null;
  state.busy = true;
  try {
    await finish(scene);
    delete state.plays[scene.id];
    savePlays();
    notice = await cast(scene, { ...how, before });
  } catch (error) {
    notice = `Not done: ${error.message}`;
  } finally {
    state.busy = false;
  }
  await refreshWorld().catch(() => {});
  renderSceneList();
  renderScript(notice);
}

// --- the cues ----------------------------------------------------------------------------------

const ACTIONS = {
  async declare(play, cue) {
    const body = { node_id: play.cast.true_node_id, cause: cue.cause, eta_minutes: cue.eta_minutes };
    if (cue.backdated) body.started_minutes_ago = play.options.minutesAgo;
    const outage = await sim("/admin/v1/outages", { method: "POST", body });
    Object.assign(play, { outageId: outage.outage_id, declared: true });
    return `${outage.outage_id} declared on ${outage.affected_node_ids.join(", ")}`;
  },
  async resolve(play) {
    if (!play.outageId) throw new Error("this scene has no outage yet: declare it first");
    const outage = await resolveOutage(play.outageId);
    return `${outage.outage_id} resolved at ${time(outage.resolved_at)}`;
  },
  async lineFail(play) {
    await setLine(play.cast.service_id, true);
    play.lineFailed = true;
    return `${play.cast.service_id} now fails its remote test`;
  },
  async linePass(play) {
    await setLine(play.cast.service_id, false);
    play.lineFailed = false;
    return `${play.cast.service_id} passes its remote test again`;
  },
  async networkDown(play) {
    play.refusedBefore = refused("network");
    await setChaos("network", "down");
    if (play.options.staleFeed) await setChaos("network_feed", "down");
    play.networkDown = true;
    return play.options.staleFeed
      ? "The Network API and the NOC event feed are down"
      : "The Network Monitoring API is down";
  },
  async networkBack(play) {
    await setChaos("network", "ok");
    await setChaos("network_feed", "ok");
    play.networkDown = false;
    return "The Network API answers again";
  },
};

async function runCue(scene, index) {
  const play = state.plays[scene.id];
  const cue = scene.cues[index];
  state.busy = true;
  renderScript();
  try {
    play.done[index] = await ACTIONS[cue.do](play, cue);
    delete play.failed[index];
  } catch (error) {
    play.failed[index] = `Not done: ${error.message}`;
  } finally {
    state.busy = false;
    savePlays();
  }
  await refreshWorld().catch(() => {});
  renderSceneList();
  renderScript();
}

async function copy(text, control) {
  try {
    await navigator.clipboard.writeText(text);
    control.textContent = "Copied";
  } catch {
    control.textContent = "Select it to copy";
  }
  setTimeout(() => {
    control.textContent = "Copy";
  }, 1500);
}

// --- the script of a scene ---------------------------------------------------------------------

function castCard(scene, play) {
  const c = play.cast;
  const mapped = c.mapped_node_ids.join(", ");
  const facts = [
    ["Customer", c.customer_id],
    ["Line", c.service_id],
    ["Node", c.true_node_id],
    ...(mapped === c.true_node_id ? [] : [["Node on record", mapped || "none"]]),
    ["Router", c.router_model],
    ["Plan", `$${c.monthly_fee_usd} a month`],
    ["Outage notices", c.consent ? "agreed" : "declined"],
  ];
  const warnings = [];
  if (c.service_status !== "active") {
    warnings.push("This line is suspended: the platform will talk about the customer's active service, if there is one.");
  }
  const covering = outageNodes().get(c.true_node_id);
  if (covering && !scene.cues.some((cue) => cue.do === "declare")) {
    warnings.push(`An outage (${covering}) covers this node, so the platform will report it instead.`);
  }
  if (state.world.faults.some((f) => f.service_id === c.service_id) && !scene.cues.some((cue) => cue.do === "lineFail")) {
    warnings.push("This line is made to fail its test, so the platform will head for a technician.");
  }
  const byId = node("form", { className: "recast", attrs: { method: "post" } });
  const input = node("input", {
    attrs: { "aria-label": "Customer ID to cast", placeholder: "C0001234", autocomplete: "off", required: "" },
  });
  const submit = node("button", { className: "quiet", text: "Cast this customer" });
  submit.type = "submit";
  byId.append(input, submit);
  byId.addEventListener("submit", (event) => {
    event.preventDefault();
    recast(scene, { customerId: input.value });
  });
  return node(
    "section",
    { className: "cast", attrs: { "aria-label": "Who is cast" } },
    node(
      "dl",
      { className: "cast__facts" },
      ...facts.map(([term, value]) => node("div", {}, node("dt", { text: term }), node("dd", { text: value }))),
    ),
    ...warnings.map((text) => node("p", { className: "warning", text })),
    node(
      "div",
      { className: "cast__controls" },
      button("Another customer", () => recast(scene), { className: "quiet", disabled: state.busy }),
      byId,
    ),
  );
}

function optionsOf(scene, play) {
  const parts = [];
  if (scene.chooseRouter) {
    const select = node("select", { attrs: { id: "router-model" } });
    const models = new Set([...(play.routers ?? []), play.options.routerModel || play.cast.router_model]);
    for (const [value, text] of [["", "any model"], ...[...models].sort().map((model) => [model, model])]) {
      const option = node("option", { text });
      option.value = value;
      select.append(option);
    }
    select.value = play.options.routerModel ?? "";
    select.addEventListener("change", () => recast(scene, { options: { routerModel: select.value } }));
    parts.push(
      node("label", { className: "option", attrs: { for: "router-model" } }, node("span", { text: "Router model" }), select),
    );
  }
  if (scene.staleFeedOption) {
    const box = node("input", { attrs: { type: "checkbox", id: "stale-feed" } });
    box.checked = Boolean(play.options.staleFeed);
    box.addEventListener("change", () => {
      play.options.staleFeed = box.checked;
      savePlays();
      renderScript();
    });
    parts.push(
      node(
        "label",
        { className: "option option--check", attrs: { for: "stale-feed" } },
        box,
        node("span", { text: "Also stop the NOC event feed, so the platform has no outage evidence at all" }),
      ),
    );
  }
  if (scene.minutesAgoOption) {
    const input = node("input", { attrs: { type: "number", id: "minutes-ago", min: "0", max: "86400", step: "1" } });
    input.value = String(play.options.minutesAgo);
    input.addEventListener("change", () => {
      const minutes = Number(input.value);
      if (Number.isInteger(minutes) && minutes >= 0 && minutes <= 86400) {
        play.options.minutesAgo = minutes;
        savePlays();
        renderScript();
      }
    });
    parts.push(
      node(
        "label",
        { className: "option", attrs: { for: "minutes-ago" } },
        node("span", { text: "The outage began this many minutes ago" }),
        input,
        node("span", {
          className: "hint",
          text: "3,000 minutes at $4,999 a month: $520.73, a supervisor's. 300 minutes: $50, the policy's minimum, applied without review.",
        }),
      ),
    );
  }
  return parts;
}

function cueItem(scene, play, cue, index) {
  const done = play.done[index];
  const failed = play.failed[index];
  const kind = cue.do ? "do" : cue.staff ? "staff" : "say";
  const item = node("li", { className: "cue", dataset: { kind, state: done ? "done" : failed ? "failed" : "ready" } });
  let action;
  if (kind === "do") {
    action = node(
      "div",
      { className: "cue__act" },
      node("p", { className: "cue__label", text: cue.label(play) }),
      button(done ? "Done" : "Go", () => runCue(scene, index), {
        className: "go",
        disabled: Boolean(done) || state.busy,
      }),
    );
  } else if (kind === "say") {
    const line = scene.lines[cue.say];
    action = node(
      "div",
      { className: "cue__act" },
      node("p", { className: "cue__label" }, node("span", { className: "cue__who", text: "The customer says" }), node("q", { text: line })),
      button("Copy", (event) => copy(line, event.currentTarget), { className: "quiet" }),
    );
  } else {
    action = node(
      "div",
      { className: "cue__act" },
      node("p", { className: "cue__label" }, node("span", { className: "cue__who", text: "Staff" }), "On the staff desk"),
      link("Open the staff desk ↗", staffUrl(), "skylink-staff"),
    );
  }
  const expect = typeof cue.expect === "function" ? cue.expect(play) : cue.expect;
  item.append(action, node("p", { className: "cue__expect", text: expect }));
  if (done) item.append(node("p", { className: "cue__result", text: done }));
  if (failed) item.append(node("p", { className: "cue__result cue__result--failed", text: failed }));
  return item;
}

function renderScript(notice = null) {
  if (state.scene === FREE) {
    renderFreePlay();
    return;
  }
  const scene = sceneOf(state.scene);
  const play = state.plays[scene.id];
  const head = [
    node("p", { className: "script__kicker", text: `Scene ${SCENES.indexOf(scene) + 1} · ${scene.tag}` }),
    node("h2", { className: "script__title", text: scene.title }),
    node("ul", { className: "shows", attrs: { "aria-label": "What it shows" } }, ...scene.shows.map((text) => node("li", { text }))),
  ];
  if (!play) {
    el("script").replaceChildren(
      ...head,
      node("p", { className: "notice", text: notice ?? "No customer is cast for this scene yet." }),
      button("Find a customer", () => recast(scene), { disabled: state.busy }),
    );
    return;
  }
  const desk = link(`Open the customer desk as ${play.cast.customer_id} ↗`, deskUrl(play.cast.customer_id, scene.lines), "skylink-desk");
  desk.className = "desk-link";
  el("script").replaceChildren(
    ...head,
    castCard(scene, play),
    ...optionsOf(scene, play),
    node(
      "div",
      { className: "desk" },
      desk,
      node("p", {
        className: "hint",
        text: "The session starts by itself, and each line below waits in the message box until you press Send, so it can be changed first.",
      }),
    ),
    node("ol", { className: "cues" }, ...scene.cues.map((cue, index) => cueItem(scene, play, cue, index))),
    node(
      "div",
      { className: "script__footer" },
      button("Play it again with another customer", () => recast(scene), { disabled: state.busy }),
      button(
        "End what this scene started",
        () => act(() => finish(scene)),
        { className: "quiet", disabled: state.busy },
      ),
    ),
  );
}

// --- evidence ----------------------------------------------------------------------------------

async function refreshEvidence() {
  const asked = state.scene;
  const scene = sceneOf(asked);
  const play = scene && state.plays[scene.id];
  if (!play) {
    state.evidence = null;
    renderEvidence();
    return;
  }
  const params = {
    outage_id: play.outageId,
    customer_id: play.cast.customer_id,
    service_id: play.cast.service_id,
    node_id: play.cast.true_node_id,
    limit: 5,
  };
  let found;
  try {
    found = await sim("/admin/v1/activity", { params });
  } catch (error) {
    if (error.code !== "OUTAGE_NOT_FOUND") {
      found = { error: error.message };
    } else {
      // The simulator was reset since: the scene's outage is gone.
      Object.assign(play, { outageId: null, declared: false });
      savePlays();
      found = await sim("/admin/v1/activity", { params: { ...params, outage_id: null } }).catch(
        (again) => ({ error: again.message }),
      );
    }
  }
  // A read that comes back after the presenter has moved to another scene is not shown.
  if (state.scene !== asked) return;
  state.evidence = found;
  renderEvidence();
}

function fact(term, value, tone) {
  return node("div", { className: "fact", dataset: tone ? { tone } : {} }, node("dt", { text: term }), node("dd", { text: value }));
}

function proof(title, facts, empty, ...extra) {
  return node(
    "section",
    { className: "proof" },
    node("h3", { text: title }),
    facts.length ? node("dl", { className: "facts" }, ...facts) : node("p", { className: "quiet-note", text: empty }),
    ...extra,
  );
}

function noticesProof(outage) {
  const facts = outage.notices.map((n) =>
    fact(
      TEMPLATES[n.template_id] ?? n.template_id,
      `${n.sent} sent to ${plural(n.customers, "customer")}`,
      n.sent === n.customers ? "" : "bad",
    ),
  );
  facts.push(
    fact(
      "Said no to notices",
      `${outage.declined_on_affected_nodes} on the node · ${outage.sent_without_consent} sent one`,
      outage.sent_without_consent === 0 ? "good" : "bad",
    ),
  );
  const sample = outage.latest.find((m) => m.template_id === "outage_started.v1") ?? outage.latest[0];
  const extra = sample
    ? [
        node("blockquote", { className: "sample", text: sample.body }),
        node("p", { className: "hint", text: `${sample.channel.toUpperCase()} to ${sample.customer_id} at ${time(sample.created_at)}` }),
      ]
    : [];
  return proof(`Notices for ${outage.outage_id}`, facts, "", ...extra);
}

function renderEvidence() {
  const box = el("evidence");
  const data = state.evidence;
  if (!data) {
    const text = state.scene === FREE ? "Free play has no scene of its own: what is in force is below." : "Nothing yet.";
    box.replaceChildren(node("p", { className: "quiet-note", text }));
    return;
  }
  if (data.error) {
    box.replaceChildren(node("p", { className: "warning", text: `Could not read it: ${data.error}` }));
    return;
  }
  const play = state.plays[state.scene];
  const parts = [];
  if (data.outage) parts.push(noticesProof(data.outage));
  if (data.node) {
    const incidents = data.node.incidents.map((i) =>
      fact(i.incident_id, `${i.status.toLowerCase()} · ${plural(i.complaint_refs.length, "complaint")} linked`),
    );
    const refs = data.node.incidents.flatMap((i) => i.complaint_refs);
    parts.push(
      proof(
        `Incidents on ${data.node.node_id}`,
        incidents,
        "None.",
        ...(refs.length ? [node("p", { className: "hint mono", text: refs.join(" · ") })] : []),
      ),
    );
  }
  if (data.service) {
    const visits = data.service.appointments.map((a) =>
      fact(a.appointment_id, `${a.status.toLowerCase()} · ${when(a.start)} to ${time(a.end)}`),
    );
    parts.push(proof(`Technician visits on ${data.service.service_id}`, visits, "None booked."));
  }
  if (data.customer) {
    const credits = data.customer.credits.map((c) =>
      fact(c.credit_id, `$${c.amount_usd} · ${c.status.toLowerCase()}${c.approved_by ? ` · ${c.approved_by}` : ""}`),
    );
    parts.push(proof(`Credits on ${data.customer.customer_id}'s account`, credits, "None."));
  }
  if (play?.refusedBefore !== undefined) {
    const count = Math.max(0, refused("network") - play.refusedBefore);
    parts.push(proof("The Network Monitoring API", [fact("Calls it refused", String(count), count ? "unknown" : "")], ""));
  }
  box.replaceChildren(...parts);
}

// --- free play ---------------------------------------------------------------------------------

function field(text, control) {
  return node("label", { className: "field" }, node("span", { text }), control);
}

function choice(name, options) {
  const made = node("select", { attrs: { name } });
  for (const [value, text] of options) {
    const option = node("option", { text });
    option.value = value;
    made.append(option);
  }
  return made;
}

function entry(name, attrs = {}) {
  return node("input", { attrs: { name, autocomplete: "off", ...attrs } });
}

function submitButton(text, value) {
  const made = node("button", { text });
  made.type = "submit";
  if (value !== undefined) {
    made.name = "outcome";
    made.value = value;
  }
  return made;
}

function tool(title, controls, buttons, onSubmit) {
  const form = node("form", { className: "tool", attrs: { method: "post" } });
  const status = node("p", { className: "status", attrs: { role: "status" } });
  form.append(node("h3", { text: title }), node("div", { className: "tool__grid" }, ...controls), node("div", { className: "row" }, ...buttons), status);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.dataset.tone = "";
    status.textContent = "…";
    try {
      status.textContent = await onSubmit(new FormData(form), event.submitter);
      status.dataset.tone = "good";
    } catch (error) {
      status.textContent = `Not done: ${error.message}`;
      status.dataset.tone = "bad";
    }
    await refreshWorld().catch(() => {});
  });
  return form;
}

const upper = (value) => String(value ?? "").trim().toUpperCase();

function foundRow(c) {
  const desk = link("Desk ↗", deskUrl(c.customer_id), "skylink-desk");
  const address = c.mapping_defect === "none" ? "" : ` · address ${c.mapping_defect}`;
  const line = c.service_status === "active" ? "" : ` · line ${c.service_status}`;
  return node(
    "li",
    { className: "found__row" },
    node("span", { className: "mono", text: `${c.customer_id} · ${c.service_id}` }),
    node("span", {
      className: "found__meta",
      text: `${c.true_node_id} · ${c.router_model ?? "no router"} · $${c.monthly_fee_usd} · notices ${c.consent ? "agreed" : "declined"}${address}${line}`,
    }),
    node(
      "span",
      { className: "found__do" },
      desk,
      button("Its node", () => {
        el("free-node").value = c.true_node_id;
      }, { className: "quiet" }),
      button("Its line", () => {
        el("free-line").value = c.service_id;
      }, { className: "quiet" }),
    ),
  );
}

function finder() {
  const results = node("ul", { className: "found" });
  const form = tool(
    "Find customers",
    [
      field("Service", choice("service_type", [["broadband", "broadband"], ["mobile", "mobile"]])),
      field("Segment", choice("segment", [["", "any"], ["consumer", "consumer"], ["sme", "business (SME)"]])),
      field("Outage notices", choice("consent", [["", "any"], ["true", "agreed"], ["false", "declined"]])),
      field("Address on record", choice("mapping_defect", [["none", "matches the network"], ["", "any"], ["missing", "missing"], ["ambiguous", "ambiguous"], ["stale", "out of date"]])),
      field("Account", choice("status", [["active", "active"], ["suspended", "suspended"], ["", "any"]])),
      field("Line", choice("service_status", [["active", "active"], ["suspended", "suspended"], ["", "any"]])),
      field("Router model", entry("router_model", { placeholder: "AL-AX18" })),
      field("Node", entry("node_id", { placeholder: "BLR-OLT-0001" })),
      field("Customer", entry("customer_id", { placeholder: "C0001234" })),
      field("Plan from $", entry("min_monthly_fee", { type: "number", min: "0", step: "1" })),
    ],
    [submitButton("Find")],
    async (data) => {
      const params = Object.fromEntries(data);
      for (const name of ["router_model", "node_id", "customer_id"]) params[name] = upper(params[name]);
      const { customers } = await sim(SEARCH, { params: { ...params, limit: 10 } });
      results.replaceChildren(...customers.map(foundRow));
      return customers.length ? `${plural(customers.length, "customer")} found.` : "Nobody fits.";
    },
  );
  form.append(results);
  return form;
}

function outageTool() {
  return tool(
    "Declare an outage",
    [
      field("Node", entry("node_id", { id: "free-node", required: "", placeholder: "BLR-OLT-0001" })),
      field(
        "Cause",
        choice("cause", [
          ["FIBRE_CUT", "fibre cut"],
          ["POWER_FAILURE", "power failure"],
          ["EQUIPMENT_FAULT", "equipment fault"],
          ["TOWER_FAULT", "tower fault"],
          ["PLANNED_MAINTENANCE", "planned maintenance"],
        ]),
      ),
      field("Back in (minutes)", entry("eta_minutes", { type: "number", min: "0", max: "43200", value: "120", required: "" })),
      field("Began (minutes ago)", entry("started_minutes_ago", { type: "number", min: "0", max: "86400", value: "0", required: "" })),
      field("Announced in advance", choice("planned", [["false", "no"], ["true", "yes (planned work)"]])),
    ],
    [submitButton("Declare")],
    async (data) => {
      const outage = await sim("/admin/v1/outages", {
        method: "POST",
        body: {
          node_id: upper(data.get("node_id")),
          cause: data.get("cause"),
          eta_minutes: Number(data.get("eta_minutes")),
          started_minutes_ago: Number(data.get("started_minutes_ago")),
          planned: data.get("planned") === "true",
        },
      });
      return `${outage.outage_id} declared on ${outage.affected_node_ids.join(", ")}.`;
    },
  );
}

function etaTool() {
  return tool(
    "Give an outage a new ETA",
    [
      field("Outage", entry("outage_id", { required: "", placeholder: "OUT-20261130-0001" })),
      field("Back in (minutes from now)", entry("eta_minutes", { type: "number", min: "0", max: "43200", value: "60", required: "" })),
    ],
    [submitButton("Update")],
    async (data) => {
      const outageId = upper(data.get("outage_id"));
      const updated = await sim(`/admin/v1/outages/${encodeURIComponent(outageId)}/update`, {
        method: "POST",
        body: { eta_minutes: Number(data.get("eta_minutes")) },
      });
      return `${outageId}: back by ${time(updated.eta)} (a field update).`;
    },
  );
}

function lineTool() {
  return tool(
    "A line's remote test",
    [field("Line", entry("service_id", { id: "free-line", required: "", placeholder: "S0001234" }))],
    [submitButton("Fails", "FAIL"), submitButton("Inconclusive", "INCONCLUSIVE"), submitButton("Passes", "PASS")],
    async (data, submitter) => {
      const serviceId = upper(data.get("service_id"));
      const outcome = submitter?.value ?? "FAIL";
      const body = outcome === "PASS" ? { service_id: serviceId, fault: false } : { service_id: serviceId, fault: true, result: outcome };
      await sim("/admin/v1/line-faults", { method: "POST", body });
      return outcome === "PASS" ? `${serviceId} passes its test.` : `${serviceId}: ${outcome.toLowerCase()} from now on.`;
    },
  );
}

function chaosTool() {
  const form = tool(
    "Make an API fail",
    [
      field(
        "API",
        choice("target", [
          ["network", "Network Monitoring API"],
          ["network_feed", "NOC event feed"],
          ["crm", "CRM"],
          ["billing", "Billing"],
          ["field", "Field service"],
          ["incident", "Incident management"],
          ["notify", "Notifications"],
        ]),
      ),
      field(
        "How",
        choice("mode", [
          ["down", "down (503)"],
          ["timeout", "time out"],
          ["latency", "slow"],
          ["error_rate", "some calls fail"],
          ["response_loss", "writes done, answers lost"],
        ]),
      ),
      field("Slow by (ms)", entry("latency_ms", { type: "number", min: "0", max: "120000", value: "3000" })),
      field("Share failing (0–1)", entry("error_rate", { type: "number", min: "0", max: "1", step: "0.05", value: "0.5" })),
      field("For (seconds, empty: until ended)", entry("for_seconds", { type: "number", min: "1" })),
    ],
    [submitButton("Apply"), button("End every failure", () => act(() => sim("/admin/v1/chaos", { method: "DELETE" })), { className: "quiet" })],
    async (data) => {
      const mode = data.get("mode");
      const body = { target: data.get("target"), mode };
      if (mode === "latency") body.latency_ms = Number(data.get("latency_ms"));
      if (mode === "error_rate") body.error_rate = Number(data.get("error_rate"));
      if (data.get("for_seconds")) body.ttl_seconds = Number(data.get("for_seconds"));
      const rule = await sim("/admin/v1/chaos", { method: "POST", body });
      return `${rule.target}: ${rule.mode.replaceAll("_", " ")}${rule.expires_at ? ` until ${time(rule.expires_at)}` : ""}.`;
    },
  );
  return form;
}

function renderFreePlay() {
  el("script").replaceChildren(
    node("p", { className: "script__kicker", text: "Anything else" }),
    node("h2", { className: "script__title", text: "Free play" }),
    node("p", {
      className: "hint",
      text: "For whatever a mentor asks: any customer, any node, any failure. What it changes shows under In force now.",
    }),
    finder(),
    outageTool(),
    etaTool(),
    lineTool(),
    chaosTool(),
  );
}

// --- the page ----------------------------------------------------------------------------------

function syncDeskLinks() {
  el("link-desk").href = state.desk;
  el("link-staff").href = staffUrl();
  el("desk-url").value = state.desk;
}

el("key-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.key = el("admin-key").value.trim();
  el("admin-key").value = "";
  connect();
});

el("forget").addEventListener("click", () => {
  forget("sessionStorage", "console.key");
  location.reload();
});

el("desk-form").addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const url = new URL(el("desk-url").value);
    state.desk = url.href.endsWith("/") ? url.href : `${url.href}/`;
  } catch {
    return;
  }
  keep("localStorage", "console.desk", state.desk);
  syncDeskLinks();
  if (state.scene !== FREE) renderScript();
});

// Two clicks, so that one stray click in front of the panel ends nothing.
let armed = null;
el("end-all").addEventListener("click", () => {
  const control = el("end-all");
  if (!armed) {
    control.textContent = "Click again: every outage resolved, every failure ended, every line passing";
    armed = setTimeout(() => {
      armed = null;
      control.textContent = "End everything in force";
    }, 4000);
    return;
  }
  clearTimeout(armed);
  armed = null;
  control.textContent = "End everything in force";
  act(async () => {
    // Read again first: what is shown can be a poll old. An outage gone since is no failure.
    await refreshWorld();
    const ended = await Promise.allSettled([
      ...state.world.outages.map((outage) => resolveOutage(outage.outage_id)),
      sim("/admin/v1/chaos", { method: "DELETE" }),
      ...state.world.faults.map((fault) => setLine(fault.service_id, false)),
    ]);
    const failed = ended.filter((result) => result.status === "rejected" && result.reason.status !== 404);
    for (const play of Object.values(state.plays)) {
      Object.assign(play, { declared: false, lineFailed: false, networkDown: false });
    }
    savePlays();
    if (failed.length) throw failed[0].reason;
  });
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !el("console").hidden) refreshWorld().catch(() => {});
});

syncDeskLinks();
renderSceneList();
if (state.key) connect();
