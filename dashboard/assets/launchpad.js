/* Local gateway is the authority. All imported/runtime fields render as text.
   Presentation follows the Claude Design "Roboport Launch Pad" document; every
   value shown is a gateway observation or an explicit unknown. */
"use strict";
const $ = (id) => document.getElementById(id);
const views = [
  ["Launch Pad", "◎"],
  ["Missions", "≡"],
  ["Fleet", "⬡"],
  ["Evidence", "▤"],
  ["Approvals", "✓"],
  ["Settings", "⚙"],
];
/* Mission states are gateway facts; tone, glyph and label are presentation. */
const MISSION_STATES = {
  draft: ["steel", "◌", "draft"],
  "waiting-review": ["amber", "◐", "waiting review"],
  queued: ["blue", "●", "queued"],
  running: ["cyan", "▶", "running"],
  cancelling: ["amber", "⏹", "cancelling"],
  failed: ["red", "✕", "failed"],
  completed: ["green", "✓", "completed"],
  cancelled: ["steel", "⊘", "cancelled"],
  rejected: ["red", "⊘", "rejected"],
  unknown: ["steel", "?", "unknown"],
};
const STEP_STATES = {
  running: ["amber", "⚙", "running"],
  completed: ["green", "✓", "completed"],
  failed: ["red", "✕", "failed"],
};
const CAP_STATES = {
  implemented: ["green", "✓"],
  verified: ["cyan", "◆"],
  proposed: ["blue", "◇"],
  "not configured": ["amber", "◌"],
  unavailable: ["steel", "⊘"],
  "observation only": ["steel", "◔"],
};
const TONE_GLYPH = {
  green: "✓",
  amber: "⚠",
  red: "⊘",
  blue: "▤",
  cyan: "▶",
  steel: "?",
};
// Hardware tier A minimums (deploy/windows/hardware-tiers.json); observed
// RAM and volumes replace these where the gateway reports them.
const EXPECTED_PROFILE = [
  ["OS", "Windows x64"],
  ["Tier", "A · local GPU workstation"],
  ["GPU", "NVIDIA · ≥ 30 GiB VRAM"],
  ["RAM", "≥ 64 GiB"],
  ["Storage", "≥ 20 GiB free"],
  ["Runtime", "Python operator service · Ollama · REA static adapter"],
];
const STANDARD_ARTIFACTS = [
  ["artifacts/final_output.json", "final result · typed output"],
  ["artifacts/provenance.json", "provenance manifest"],
  ["artifacts/budget.json", "budget ledger"],
  ["artifacts/grading.json", "grading"],
  ["artifacts/plan.json", "execution inputs"],
];
let actor = null,
  csrf = "",
  mode = "Live",
  view = "Launch Pad",
  readiness = null,
  state = {},
  stream = null,
  reviewed = null,
  formDraft = null,
  artifact = null,
  observing = true;
const decisionsDraft = new Map();
let reviewMissionId = null,
  reviewFilter = null,
  artifactMissionId = null,
  artifactRequest = 0,
  reviewContext = null,
  reviewContextNotice = "";
let showManifest = false,
  selectedMissionId = null,
  fleetMode = "topology",
  enrollmentRequired = false,
  draftSavedAt = null;
const narrowQuery = window.matchMedia("(max-width: 720px)");

/* ---------- DOM helpers ---------- */
function node(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = String(text);
  if (cls) el.className = cls;
  return el;
}
function button(text, action, cls = "") {
  const el = node("button", text, cls);
  el.type = "button";
  el.addEventListener("click", () => Promise.resolve(action()).catch(notify));
  return el;
}
function glyph(mark) {
  const el = node("span", mark, "glyph");
  el.setAttribute("aria-hidden", "true");
  return el;
}
function toned(tag, text, tone, cls) {
  const el = node(tag, text, cls);
  el.classList.add("tone-" + (tone || "steel"));
  return el;
}
function withGlyph(el, mark, text) {
  el.append(glyph(mark), node("span", text));
  return el;
}
function notify(error) {
  const box = $("message");
  box.dataset.tone = error instanceof Error ? "red" : "green";
  const dismiss = button("Dismiss", () => box.replaceChildren(), "quiet");
  dismiss.setAttribute("aria-label", "Dismiss notification");
  box.replaceChildren(node("span", error?.message || String(error)), dismiss);
}
async function api(path, body) {
  const r = await fetch("/api/launchpad/" + path, {
    credentials: "same-origin",
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let value = await r.json();
  if (!r.ok) throw Error(value.error || "Gateway request failed");
  return value;
}
function field(label, name, type = "text", value = "", hint) {
  const box = node("div", undefined, "field"),
    id = "field-" + name;
  const lab = node("label", label);
  lab.htmlFor = id;
  if (hint) lab.append(node("span", " · " + hint, "hint"));
  const input = node(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") input.type = type;
  input.id = id;
  input.name = name;
  input.value = value;
  box.append(lab, input);
  return box;
}
function selectField(label, name, options, value) {
  const box = node("div", undefined, "field"),
    id = "field-" + name;
  const lab = node("label", label);
  lab.htmlFor = id;
  const select = node("select");
  select.id = id;
  select.name = name;
  for (const [v, text] of options) {
    const option = node("option", text);
    option.value = v;
    select.append(option);
  }
  select.value = value;
  box.append(lab, select);
  return box;
}
function card(title, aside) {
  const el = node("section", undefined, "panel");
  if (title && aside !== undefined) {
    const row = node("div", undefined, "eyebrow-row");
    row.append(node("h2", title), node("span", aside, "aside"));
    el.append(row);
  } else if (title) el.append(node("h2", title));
  return el;
}
function heading(title, meta, note) {
  const box = node("div", undefined, "section-head"),
    h = node("h1", title);
  if (meta) h.append(node("span", " · " + meta, "meta"));
  box.append(h);
  if (note) box.append(node("div", note, "section-note"));
  return box;
}
function kv(k, v) {
  const el = node("div");
  el.append(node("div", k, "k"), node("div", v, "v"));
  return el;
}
function shortId(id) {
  return String(id || "").slice(0, 8);
}
function clock(ts) {
  return ts ? new Date(ts * 1000).toLocaleTimeString() : "never";
}
function stamp(ts) {
  return ts ? new Date(ts * 1000).toLocaleString() : "never";
}
function text(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}
function gib(bytes) {
  return (bytes / 1024 ** 3).toFixed(0) + " GiB";
}
function tb(bytes) {
  return (bytes / 1e12).toFixed(2) + " TB";
}

/* ---------- observed state ---------- */
function missions() {
  return state.data?.missions || [];
}
function active() {
  return missions().filter((m) =>
    ["queued", "running", "cancelling", "unknown"].includes(m.state),
  );
}
function usable() {
  return mode === "Live" && actor && ["operator", "admin"].includes(actor.role);
}
function freshReadiness() {
  return readiness && Date.now() / 1000 < readiness.expires_at;
}
function isNarrow() {
  return narrowQuery.matches;
}
function stepsOf(id) {
  return (state.data?.steps || []).filter((s) => s.mission_id === id);
}
function observation(id) {
  return readiness?.observations?.find((o) => o.id === id);
}
function modelObservations() {
  return (readiness?.observations || []).filter((o) =>
    String(o.id).startsWith("model:"),
  );
}
function sourceLabel() {
  return mode === "Replay"
    ? "imported replay"
    : mode === "Demo"
      ? "demo · no gateway"
      : "runtime observations";
}
function connectionSummary() {
  if (mode === "Replay") return ["Replay", "blue"];
  if (mode === "Demo") return ["Demo · no gateway", "amber"];
  if (!actor) return ["Not signed in", "steel"];
  if (!readiness) return ["Unobserved", "steel"];
  if (!freshReadiness()) return ["Stale", "amber"];
  return readiness.ready ? ["Gateway observed", "green"] : ["Blocked", "amber"];
}

/* ---------- chrome ---------- */
function setConnection(text, tone = "steel") {
  const chip = $("env-status");
  chip.className = "env-status tone-" + tone;
  chip.replaceChildren(
    glyph(TONE_GLYPH[tone] || "?"),
    node("span", text.split(" · ")[0]),
  );
  chip.title = text;
  $("connection").textContent = text;
  const observed = $("env-observed");
  observed.textContent = readiness?.observed_at
    ? "· " +
      clock(readiness.observed_at) +
      " · " +
      (freshReadiness() ? "fresh" : "stale")
    : "· never observed";
  observed.classList.toggle("stale", Boolean(readiness) && !freshReadiness());
}
function updateChrome() {
  const test = readiness?.environment_id === "TEST-SIMULATION";
  const badge = $("mode");
  badge.className = "mode-badge mode-" + mode.toLowerCase();
  badge.replaceChildren(
    node("span", undefined, "mode-dot"),
    node(
      "span",
      (mode === "Demo" ? "DEMO · FIXTURES" : mode.toUpperCase()) +
        (test ? " · TEST FIXTURE" : ""),
    ),
  );
  badge.firstChild.setAttribute("aria-hidden", "true");
  $("environment").textContent =
    mode === "Replay"
      ? state.environment_id
      : readiness?.environment_id || state.environment_id || "Not observed";
  $("simulation").hidden = $("environment").textContent !== "TEST-SIMULATION";
  $("sign-out").hidden = !actor;
  $("demo-mode").setAttribute("aria-pressed", String(mode === "Demo"));
  $("live-mode").setAttribute("aria-pressed", String(mode === "Live"));
  $("modes-line").textContent =
    "Modes: Demo · fixtures" +
    (mode === "Demo" ? " (active)" : "") +
    " · Replay" +
    (mode === "Replay" ? " (active)" : " — import a run under Evidence") +
    " · Live" +
    (mode === "Live" ? (actor ? " (active)" : " — sign in required") : "");
  const [summary, tone] = connectionSummary();
  if (mode !== "Live") setConnection(summary + " · no commands are sent", tone);
}
function nav() {
  const el = $("navigation");
  el.replaceChildren();
  views.forEach(([name, mark]) => {
    const b = button("", () => {
      if (view === "Launch Pad" && $("mission-form")) formDraft = readForm();
      view = name;
      render();
      window.scrollTo(0, 0);
    });
    b.append(glyph(mark), node("span", name));
    b.setAttribute("aria-current", view === name ? "page" : "false");
    el.append(b);
  });
}

/* ---------- draft form ---------- */
function readForm() {
  if (!$("mission-form")) return formDraft || {};
  const data = Object.fromEntries(new FormData($("mission-form")));
  for (const input of $("mission-form").querySelectorAll(
    "[data-unobserved-default]",
  )) {
    delete data[input.name];
  }
  return { ...data };
}
function manifestFromForm() {
  const values = readForm(),
    profile = values.profile;
  return {
    profile,
    objective: values.objective,
    project_root: values.project_root,
    tools:
      profile === "environment_investigation" ? ["rea_analyze_project"] : [],
    classification: values.classification,
    limits: {
      max_llm_calls: Number(values.calls),
      max_tool_calls: Number(values.tools),
      max_wall_seconds: Number(values.seconds),
      max_usd: Number(values.usd),
    },
    context_tokens: Number(values.context),
    completion_tokens: Number(values.completion),
    success_criteria: String(values.criteria || "")
      .split("\n")
      .filter((s) => s.trim()),
  };
}
function savedLabel() {
  return draftSavedAt
    ? "saved " + clock(draftSavedAt) + " · local store"
    : "unsaved · local store";
}
function saveDraft() {
  const draft = readForm();
  if (!["public", "internal"].includes(draft.classification))
    throw Error("Restricted drafts cannot be cached locally");
  localStorage.setItem("roboport-draft", JSON.stringify(draft));
  formDraft = draft;
  draftSavedAt = Date.now() / 1000;
  const label = document.querySelector("[data-saved-label]");
  if (label) label.textContent = savedLabel();
  notify(
    "Draft saved locally. It will be validated by the gateway before launch.",
  );
}

/* ---------- mission presentation ---------- */
function missionState(m) {
  return MISSION_STATES[m.state] || MISSION_STATES.unknown;
}
function stateMark(m, cls = "mission-state") {
  const [tone, icon, label] = missionState(m);
  return withGlyph(toned("span", undefined, tone, cls), icon, label);
}
function approvalLabel(m) {
  const last = (m.decisions || []).slice(-1)[0];
  if (last) return last.verdict + " · " + last.actor_id;
  if (m.manifest.require_review === false) return "not required";
  return m.state === "draft" ? "not requested" : "waiting · proposal review";
}
function runtimeLabel(m) {
  const rt = state.data?.runtime?.[m.id];
  if (m.outcome?.status)
    return m.outcome.status + (m.outcome.error ? " · error" : "");
  if (rt?.status) return rt.status;
  return m.state === "draft" ? "—" : "not observed";
}
function budgetChips(ledger) {
  const metrics = node("div", undefined, "budget-summary");
  for (const [label, value] of [
    ["Model calls", ledger.llm_calls],
    ["Tool calls", ledger.tool_calls],
    ["API spent $", ledger.spent_usd],
    ["Reserved $", ledger.reserved_usd],
    [
      "Elapsed s",
      ledger.elapsed_seconds === undefined
        ? undefined
        : Math.round(ledger.elapsed_seconds),
    ],
    ["Host / electricity", "Unknown"],
  ])
    metrics.append(node("span", label + ": " + (value ?? "Unknown")));
  return metrics;
}
async function cancelMission(m) {
  await api("commands", {
    operation_id: crypto.randomUUID(),
    mission_id: m.id,
    expected_revision: m.revision,
    manifest_hash: m.manifest.manifest_hash,
    intent: "cancel",
  });
  notify("Cancellation requested; waiting for process cleanup.");
  await refresh();
  render();
}
function toggleObservation() {
  observing = !observing;
  if (!observing) {
    stream?.close();
    stream = null;
    notify("Observation paused. Missions continue.");
  } else connect();
  render();
}
function missionRow(m, opts = {}) {
  const row = node("div", undefined, "mission");
  const expanded = Boolean(opts.controls) && selectedMissionId === m.id;
  if (expanded) row.classList.add("sel");
  const interactive = Boolean(opts.controls || opts.link);
  const head = node(interactive ? "button" : "div", undefined, "mission-head");
  if (interactive) head.type = "button";
  else head.classList.add("static");
  head.append(
    node("span", shortId(m.id), "mission-id"),
    node("span", m.manifest.objective, "mission-title"),
    stateMark(m),
  );
  const approval = node("span", undefined, "mission-col");
  approval.append(node("span", "approval ", "k"), node("span", approvalLabel(m)));
  const runtime = node("span", undefined, "mission-col");
  runtime.append(node("span", "runtime ", "k"), node("span", runtimeLabel(m)));
  head.append(
    approval,
    runtime,
    node("span", clock(m.updated_at || m.created_at), "mission-updated"),
  );
  if (opts.controls) {
    head.setAttribute("aria-expanded", String(expanded));
    head.addEventListener("click", () => {
      selectedMissionId = expanded ? null : m.id;
      render();
    });
  } else if (opts.link) {
    head.setAttribute(
      "aria-label",
      "Open mission " + shortId(m.id) + " in Missions",
    );
    head.addEventListener("click", () => {
      selectedMissionId = m.id;
      view = "Missions";
      render();
      window.scrollTo(0, 0);
    });
  }
  row.append(head);
  if (expanded) row.append(missionDetail(m));
  return row;
}
function missionDetail(m) {
  const box = node("div", undefined, "mission-detail");
  const facts = node("div", undefined, "facts");
  const man = m.manifest,
    approval = man.approval || {};
  for (const [k, v] of [
    ["Owner", man.owner],
    ["Profile", man.profile],
    ["Classification", man.classification],
    ["Manifest hash", man.manifest_hash],
    ["Project root", man.project_root || "—"],
    [
      "Reviewer requirement",
      (man.require_review === false
        ? "no review required"
        : man.independent_review
          ? "independent proposal review"
          : "operator checkpoint") +
        (man.reviewer_id ? " · " + man.reviewer_id : " · any enrolled reviewer"),
    ],
    [
      "Approval",
      approval.id
        ? approval.id +
          (approval.expires_at ? " · expires " + stamp(approval.expires_at) : "")
        : "—",
    ],
    ["Revision", m.revision],
    [
      "Source",
      mode === "Replay" ? "imported replay · read-only" : "gateway mission store",
    ],
  ])
    facts.append(kv(k, v ?? "Unknown"));
  const actions = node("div", undefined, "mission-actions");
  const cancellable = usable() && ["queued", "running"].includes(m.state);
  const cancel = button("Cancel mission", () => cancelMission(m), "danger");
  cancel.disabled = !cancellable;
  cancel.title = cancellable
    ? "Requests cancellation; completion requires an observed terminal outcome"
    : ["queued", "running"].includes(m.state)
      ? "Live operator session required to cancel"
      : "Mission is not in a cancellable state";
  const observe = button(
    observing ? "Stop observation" : "Resume observation",
    toggleObservation,
  );
  observe.disabled = mode !== "Live" || !actor;
  observe.title = observe.disabled
    ? "Observation runs only in Live mode"
    : "Pausing observation never cancels a mission";
  actions.append(
    cancel,
    observe,
    node("span", "Closing this page never cancels a mission.", "note"),
  );
  facts.append(actions);
  box.append(facts);
  const col = node("div");
  col.append(node("div", "Event timeline", "eyebrow"));
  const ol = node("ol", undefined, "timeline");
  const events = [{ t: stamp(m.created_at), text: "Mission drafted · " + man.profile }];
  for (const d of m.decisions || [])
    events.push({
      t: stamp(d.timestamp),
      text: `Decision ${d.verdict} · ${d.actor_id}: ${d.rationale}`,
    });
  for (const s of stepsOf(m.id))
    events.push({
      t: s.step_id,
      text:
        `${s.agent_id} · ${s.state}` +
        (s.llm_calls !== undefined && s.llm_calls !== null
          ? ` · ${s.llm_calls} model calls`
          : "") +
        (s.error ? " · " + text(s.error) : ""),
    });
  const rt = state.data?.runtime?.[m.id];
  if (rt) events.push({ t: "runtime", text: text(rt) });
  if (m.outcome?.status)
    events.push({
      t: stamp(m.updated_at),
      text:
        "Terminal outcome · " +
        m.outcome.status +
        (m.outcome.error ? " · " + text(m.outcome.error) : ""),
    });
  for (const ev of events) {
    const li = node("li");
    li.append(node("span", ev.t, "t"), node("span", ev.text, "text"));
    ol.append(li);
  }
  col.append(ol);
  const ledger = state.data?.budgets?.[m.id];
  if (ledger) col.append(budgetChips(ledger));
  box.append(col);
  return box;
}

/* ---------- Launch Pad ---------- */
function connectionPill() {
  const [label, tone] = connectionSummary();
  return withGlyph(toned("span", undefined, tone, "pill"), TONE_GLYPH[tone], label);
}
function environmentPanel() {
  const panel = node("section", undefined, "panel");
  panel.setAttribute("aria-label", "Environment");
  const grid = node("div", undefined, "env-grid"),
    left = node("div"),
    right = node("div");
  left.append(node("div", "Environment", "eyebrow"));
  const row = node("div", undefined, "env-row");
  const select = node("select", undefined, "env-select");
  select.setAttribute("aria-label", "Select environment");
  const envId = $("environment").textContent;
  const current = node("option", envId);
  current.value = envId;
  const add = node("option", "+ Add environment (Settings)");
  add.disabled = true;
  select.append(current, add);
  row.append(select, connectionPill());
  left.append(row);
  const observed = node("div", undefined, "env-line");
  observed.append(
    "Last observed ",
    toned("span", readiness ? clock(readiness.observed_at) : "never", "bright"),
    " · source: " +
      (mode === "Replay"
        ? "imported replay records"
        : mode === "Demo"
          ? "demo fixture · no gateway"
          : "gateway readiness on loopback") +
      " · ",
    readiness
      ? toned(
          "span",
          freshReadiness() ? "fresh" : "stale",
          freshReadiness() ? "green" : "amber",
        )
      : toned("span", "no observation", "amber"),
  );
  left.append(observed);
  const power = node("div", undefined, "env-line");
  power.append(
    "Gateway process: ",
    toned("span", readiness ? "observed · 127.0.0.1" : "unknown", "bright"),
    readiness
      ? " · each readiness observation expires after 30 s."
      : " — no supported evidence distinguishes offline, sleeping or unreachable.",
  );
  left.append(power);
  const memory = observation("memory"),
    volumes = observation("volumes");
  const eyebrow = node("div", "Expected profile · ", "eyebrow");
  eyebrow.append(
    node(
      "span",
      memory || volumes
        ? "RAM and volumes observed · GPU placement unverified"
        : "unverified on target",
      "warn",
    ),
  );
  right.append(eyebrow);
  const pg = node("div", undefined, "profile-grid");
  for (const [k, expected] of EXPECTED_PROFILE) {
    const cell = node("div");
    cell.append(node("span", k + " ", "k"));
    let value = expected,
      extra = null;
    if (k === "RAM" && memory?.value) value = gib(memory.value) + " observed";
    if (k === "Storage" && Array.isArray(volumes?.value) && volumes.value.length) {
      value = volumes.value
        .slice(0, 4)
        .map(
          (v) =>
            `${v.mountpoint} ${tb(v.free_bytes)} free / ${tb(v.total_bytes)}`,
        )
        .join(" · ");
      extra = "· physical drive map unverified";
    }
    if (k === "GPU") extra = "· placement unverified";
    if (k === "Runtime") {
      const routes = Object.entries(readiness?.model_routes || {});
      if (routes.length)
        value +=
          " · " +
          routes
            .map(([hint, r]) => `${hint}: ${r.provider || "?"} ${r.model || "?"}`)
            .join(", ");
    }
    cell.append(node("span", value, "v"));
    if (extra) cell.append(" ", node("span", extra, "warn"));
    pg.append(cell);
  }
  right.append(pg);
  grid.append(left, right);
  panel.append(grid);
  return panel;
}
function readinessTiles() {
  const checks = node("div", undefined, "readiness");
  const fresh = Boolean(readiness) && freshReadiness();
  const compliance = observation("compliance"),
    models = modelObservations();
  const modelOk = models.length > 0 && models.every((m) => m.state === "observed");
  const firstModel = models.find((m) => m.observed_at);
  const tiles = [
    [
      "Connection",
      fresh
        ? readiness.ready
          ? ["Gateway observed", "✓", "green"]
          : ["Blocked", "⚠", "amber"]
        : readiness
          ? ["Stale", "⚠", "amber"]
          : ["Unknown", "?", "steel"],
      fresh
        ? "Loopback gateway answered on 127.0.0.1 · profile checks repeat at dispatch."
        : readiness
          ? "Observation older than 30 s. Refresh before dispatch."
          : "No gateway observation in this session.",
      readiness
        ? `observed ${clock(readiness.observed_at)} · ${fresh ? "fresh" : "stale"}`
        : "never observed",
    ],
    [
      "Compliance",
      compliance?.state === "observed"
        ? ["Observed", "✓", "green"]
        : ["No evidence", "?", "steel"],
      compliance?.source ||
        "Intune · BitLocker · Defender · standard-user: evidence links required, no checks received.",
      compliance?.observed_at
        ? "observed " + clock(compliance.observed_at)
        : "never observed",
    ],
    [
      "GPU · model",
      modelOk && fresh
        ? ["Model observed", "✓", "green"]
        : ["Unknown / blocked", "?", "steel"],
      models.length
        ? models
            .map(
              (m) =>
                `${m.id.slice(6)} · ${m.state === "observed" ? "digest " + String(m.value).slice(0, 12) : "not installed"}`,
            )
            .join(" · ") + " · GPU placement unverified on target."
        : "GPU placement, driver and model residency unverified on target.",
      firstModel
        ? "observed " + clock(firstModel.observed_at) + " · Ollama tags"
        : "never observed",
    ],
    [
      "Services",
      readiness ? ["Gateway observed", "✓", "green"] : ["Unknown", "?", "steel"],
      readiness
        ? "Operator service observed · Ollama per model check · bridge on this port."
        : "Operator service · bridge · Ollama. Restart must reconcile owned processes first.",
      readiness ? "observed " + clock(readiness.observed_at) : "never observed",
    ],
    [
      "Permissions",
      actor ? ["Enrolled", "✓", "green"] : ["Not enrolled", "⊘", "red"],
      actor
        ? `Local identity ${actor.id} · role ${actor.role} · classifications ${actor.classifications.join(", ")}.`
        : "No local operator enrollment or authenticated session. Policy applies to local HTTP callers too.",
      actor ? "checked now · gateway session" : "checked now · no session",
    ],
  ];
  for (const [label, [stateText, icon, tone], detail, fresh_] of tiles) {
    const c = node("div", undefined, "check");
    c.append(
      node("div", label, "label"),
      withGlyph(toned("div", undefined, tone, "state"), icon, stateText),
      node("div", detail, "detail"),
      node("div", fresh_, "fresh"),
    );
    checks.append(c);
  }
  return checks;
}
function blockerAlert() {
  const box = node("div", undefined, "alert");
  box.setAttribute("role", "alert");
  const mark = node("div", "!", "alert-mark");
  mark.setAttribute("aria-hidden", "true");
  const body = node("div", undefined, "alert-body");
  let title, next;
  if (!actor) {
    title = "Blocked — no authenticated operator session";
    next =
      "Next supported action: sign in with an enrolled local identity, or enroll one with launchpad_admin.py on this workstation. Drafts can be saved while signed out.";
  } else if (!readiness) {
    title = "Blocked — gateway readiness not observed";
    next =
      "Next supported action: refresh observations. Nothing is assumed about the workstation until the gateway answers.";
  } else if (!freshReadiness()) {
    title = "Readiness stale — refresh observations before dispatch";
    next =
      "Observations expire 30 s after they are taken. The gateway re-checks every condition at dispatch regardless of this view.";
  } else {
    title = "Blocked — " + readiness.blockers[0];
    next =
      readiness.blockers.length > 1
        ? "Also: " +
          readiness.blockers.slice(1).join("; ") +
          ". Drafts can be saved while blocked."
        : "Next supported action: resolve the blocker on the workstation, then refresh observations. Drafts can be saved while blocked.";
  }
  body.append(node("div", title, "alert-title"), node("div", next, "alert-text"));
  const actions = node("div", undefined, "actions");
  if (actor)
    actions.append(
      button(
        "Refresh observations",
        async () => {
          await refresh();
          formDraft = readForm();
          render();
        },
        "warn",
      ),
    );
  actions.append(
    button(
      "Open Settings → Connections",
      () => {
        view = "Settings";
        render();
        window.scrollTo(0, 0);
      },
      "warn",
    ),
  );
  box.append(mark, body, actions);
  return box;
}
function draftCard() {
  const c = node("section", undefined, "panel");
  const row = node("div", undefined, "eyebrow-row");
  const title = node("h2", "Mission draft · ");
  title.append(node("span", reviewed ? shortId(reviewed.id) : "unsaved", "hl"));
  const saved = node("span", savedLabel(), "aside");
  saved.dataset.savedLabel = "true";
  row.append(title, saved);
  c.append(row);
  const form = node("form");
  form.id = "mission-form";
  form.addEventListener("submit", (e) => e.preventDefault());
  const defaults = readiness?.defaults || {
    limits: {
      max_llm_calls: 25,
      max_tool_calls: 50,
      max_wall_seconds: 600,
      max_usd: 5,
    },
    // Placeholders until readiness reports the deployment defaults; they
    // match the gateway's own fallback (policy.py, readiness.py).
    context_tokens: 32768,
    completion_tokens: 2048,
  };
  let cached = formDraft;
  try {
    cached = cached || JSON.parse(localStorage.getItem("roboport-draft"));
  } catch {}
  cached = cached || {};
  const profiles = readiness?.profiles || {};
  const rootHint = (profile) => {
    const roots = profiles[profile]?.roots || [];
    return roots.length
      ? "approved roots: " + roots.join(", ")
      : "no approved root observed";
  };
  const initialProfile = cached.profile || "environment_investigation";
  form.append(
    field("Mission objective", "objective", "textarea", cached.objective || ""),
  );
  const rootField = field(
    "Approved project root",
    "project_root",
    "text",
    cached.project_root || Object.values(profiles)[0]?.roots?.[0] || "",
    rootHint(initialProfile),
  );
  form.append(rootField);
  const pair = node("div", undefined, "form-grid");
  const profileField = selectField(
    "Crew / profile",
    "profile",
    [
      ["environment_investigation", "REA investigation → governed proposal"],
      ["jd_crew", "JD crew · controlled deployment fixture"],
    ],
    initialProfile,
  );
  const select = profileField.querySelector("select");
  pair.append(
    profileField,
    selectField(
      "Data classification",
      "classification",
      [
        ["internal", "Internal"],
        ["public", "Public"],
      ],
      cached.classification || "internal",
    ),
  );
  form.append(pair);
  const limits = node("div", undefined, "form-grid-sm");
  limits.append(
    field(
      "Model call ceiling",
      "calls",
      "number",
      cached.calls ?? defaults.limits.max_llm_calls,
    ),
    field(
      "Tool call ceiling",
      "tools",
      "number",
      cached.tools ?? defaults.limits.max_tool_calls,
    ),
    field(
      "Time ceiling (seconds)",
      "seconds",
      "number",
      cached.seconds ?? defaults.limits.max_wall_seconds,
    ),
    field(
      "API spend ceiling (USD)",
      "usd",
      "number",
      cached.usd ?? defaults.limits.max_usd,
    ),
    field(
      "Context limit (tokens)",
      "context",
      "number",
      cached.context ?? defaults.context_tokens,
    ),
    field(
      "Completion limit (tokens)",
      "completion",
      "number",
      cached.completion ?? defaults.completion_tokens,
    ),
  );
  form.append(limits);
  form.append(
    field(
      "Success criteria (one per line)",
      "criteria",
      "textarea",
      cached.criteria ||
        "Evidence-backed result with limitations; no project mutation.",
    ),
  );
  // Before sign-in these are placeholders, not observed deployment limits.
  // Preserve explicit edits/saved drafts; let untouched fields adopt readiness.
  if (!readiness) {
    for (const name of [
      "project_root",
      "calls",
      "tools",
      "seconds",
      "usd",
      "context",
      "completion",
    ]) {
      if (cached[name] !== undefined && cached[name] !== null) continue;
      const input = form.elements.namedItem(name);
      input.dataset.unobservedDefault = "true";
      input.addEventListener(
        "input",
        () => delete input.dataset.unobservedDefault,
      );
    }
  }
  const scopes = node("div", undefined, "scope-summary");
  const tools = node("div", undefined, "scope-box"),
    reviewer = node("div", undefined, "scope-box");
  function updateScope() {
    const profile = select.value,
      policy = profiles[profile],
      pr = readiness?.profile_readiness?.[profile];
    tools.replaceChildren(
      node("h3", "Allowed tools · profile"),
      node(
        "div",
        profile === "environment_investigation"
          ? (policy?.tools?.length ? policy.tools.join(", ") : "rea_analyze_project") +
            " · REA static inspection · no target code execution"
          : "Controlled JD fixture · no external tools",
        "v",
      ),
      node(
        "div",
        policy
          ? "Profile " +
            (policy.enabled ? "enabled" : "not enabled") +
            " by policy. Mutation tools: not in profile. Restricted or regulated data: not supported in this slice."
          : "No policy observed for this profile. Mutation tools: not in profile.",
        policy?.enabled ? "note" : "warn",
      ),
    );
    reviewer.replaceChildren(
      node("h3", "Reviewer requirement"),
      node(
        "div",
        pr?.reviewer_id || "Unconfigured · named proposal reviewer required",
        "v",
      ),
      node(
        "div",
        "Proposal checkpoint · decision bound to exact evidence · self-approval rejected for independent review",
        "note",
      ),
    );
    if (pr?.blockers?.length) reviewer.append(node("div", pr.blockers.join(" · "), "warn"));
  }
  select.addEventListener("change", () => {
    rootField.querySelector(".hint").textContent = " · " + rootHint(select.value);
    updateScope();
  });
  scopes.append(tools, reviewer);
  updateScope();
  form.append(scopes);
  c.append(form);
  return c;
}
function previewManifest() {
  return { ...manifestFromForm(), environment_id: readiness?.environment_id };
}
function planRows(manifest, persisted) {
  const limits = manifest.limits || {};
  // Placeholder limits shown before sign-in are not observed deployment values.
  const show = (v) => (Number.isFinite(v) ? v : "unobserved");
  return [
    [
      "Environment",
      (readiness?.environment_id ||
        manifest.environment_id ||
        "—") + " · " + (freshReadiness() ? "observed" : "unobserved"),
    ],
    ["Profile", manifest.profile || "—"],
    ["Project root", manifest.project_root || "—"],
    ["Tools", (manifest.tools || []).join(", ") || "none"],
    ["Classification", manifest.classification || "—"],
    [
      "Limits",
      `${show(limits.max_llm_calls)} calls · ${show(limits.max_tool_calls)} tools · ${show(limits.max_wall_seconds)} s · $${show(limits.max_usd)}`,
    ],
    [
      "Context",
      `${show(manifest.context_tokens)} ctx · ${show(manifest.completion_tokens)} completion` +
        contextShortfall(manifest),
    ],
    [
      "Review",
      persisted
        ? (manifest.independent_review
            ? "independent proposal review"
            : "operator checkpoint") +
          (manifest.reviewer_id ? " · " + manifest.reviewer_id : "")
        : "assigned by the gateway at review",
    ],
    ["Owner", persisted ? manifest.owner : actor?.id || "unassigned"],
    [
      "Approval",
      persisted ? manifest.approval?.id || "—" : "checked by the gateway at review",
    ],
  ];
}
function startBlockers(current) {
  const list = [];
  if (!actor)
    list.push(
      "Operator session not enrolled — commands require an authenticated local session",
    );
  else if (mode !== "Live")
    list.push("Live mode required — " + mode + " never sends commands");
  else if (!["operator", "admin"].includes(actor.role))
    list.push("Operator role required for dispatch");
  if (!readiness) list.push("Gateway readiness not observed");
  else {
    if (!freshReadiness())
      list.push("Readiness stale — refresh observations before dispatch");
    if (!readiness.ready) for (const b of readiness.blockers || []) list.push(b);
  }
  if (!current)
    list.push(
      "Launch plan not yet reviewed — the gateway must persist the exact manifest",
    );
  else {
    const pr = readiness?.profile_readiness?.[current.manifest.profile];
    if (pr?.ready === false) for (const b of pr.blockers) list.push(b);
    if (current.state !== "draft")
      list.push(
        "Reviewed mission is " + current.state + " — only a draft can start",
      );
  }
  if (active().length)
    list.push("A mission is already active — one bounded mission at a time");
  return list;
}
// A saved draft can carry a context limit below the deployment default; the
// runtime then fails the first step before any model call. Say so on the plan.
function contextShortfall(manifest) {
  const floor = Number(readiness?.defaults?.context_tokens);
  const value = Number(manifest?.context_tokens);
  return floor > 0 && value > 0 && value < floor
    ? ` · below the deployment default ${floor}; large inputs fail the first step`
    : "";
}
function renderReview() {
  const el = $("review-plan");
  if (!el) return;
  el.replaceChildren();
  const current = reviewed
    ? missions().find((m) => m.id === reviewed.id) || reviewed
    : null;
  if (current) reviewed = current;
  const persisted = Boolean(current);
  const manifest = persisted ? current.manifest : previewManifest();
  const row = node("div", undefined, "eyebrow-row");
  const title = node("h2", "Launch plan · ");
  title.append(
    node("span", persisted ? "persisted manifest" : "preview · not persisted", "hl"),
  );
  row.append(
    title,
    node(
      "span",
      persisted
        ? "manifest sha256 assigned by the gateway"
        : "digest assigned by the gateway at review",
      "aside",
    ),
  );
  el.append(row);
  if (persisted) el.append(node("div", current.manifest.manifest_hash, "review-hash"));
  if (showManifest && persisted)
    el.append(
      node(
        "pre",
        JSON.stringify(
          {
            objective: current.manifest.objective,
            profile: current.manifest.profile,
            root: current.manifest.project_root,
            tools: current.manifest.tools,
            classification: current.manifest.classification,
            limits: current.manifest.limits,
            context_tokens: current.manifest.context_tokens,
            completion_tokens: current.manifest.completion_tokens,
            approval: current.manifest.approval,
            independent_review: current.manifest.independent_review,
            review_phase: current.manifest.review_phase,
            reviewer_id: current.manifest.reviewer_id,
            owner: current.manifest.owner,
            state: current.state,
          },
          null,
          2,
        ),
      ),
    );
  else {
    const rows = node("div", undefined, "plan-rows");
    for (const [k, v] of planRows(manifest, persisted)) rows.append(kv(k, v));
    el.append(rows);
  }
  const actions = node("div", undefined, "actions");
  actions.append(button("Save draft", saveDraft));
  const review = button(
    persisted ? "Re-review launch plan" : "Review launch plan",
    async () => {
      formDraft = readForm();
      reviewed = await api("missions", manifestFromForm());
      showManifest = false;
      await refresh();
      renderReview();
      updateRecent();
      notify(
        "Launch plan persisted by the gateway · " +
          reviewed.manifest.manifest_hash.slice(0, 16) +
          "…",
      );
    },
    "outline",
  );
  review.disabled = !usable();
  review.title = usable()
    ? "Validates the draft against policy and persists the exact manifest"
    : "Live operator session required";
  actions.append(review);
  if (persisted) {
    const toggle = button(
      showManifest ? "Hide manifest JSON" : "Show manifest JSON",
      () => {
        showManifest = !showManifest;
        renderReview();
      },
      "quiet",
    );
    toggle.setAttribute("aria-expanded", String(showManifest));
    actions.append(toggle);
  }
  const start = button(
    "",
    async () => {
      const op = {
        operation_id: crypto.randomUUID(),
        mission_id: current.id,
        expected_revision: current.revision,
        manifest_hash: current.manifest.manifest_hash,
        intent: "start",
      };
      sessionStorage.setItem("roboport-pending-operation", JSON.stringify(op));
      const result = await api("commands", op);
      notify(
        `${result.status}: command intent recorded; completion requires runtime evidence.`,
      );
      sessionStorage.removeItem("roboport-pending-operation");
      await refresh();
      renderReview();
      updateRecent();
    },
    "primary",
  );
  withGlyph(start, "⏵", "Start when ready");
  start.setAttribute("aria-describedby", "start-why");
  const blockers = startBlockers(current);
  start.disabled = blockers.length > 0;
  actions.append(start);
  const pending = sessionStorage.getItem("roboport-pending-operation");
  if (pending) {
    const retry = button("Retry recorded command", async () => {
      if (!usable() || !freshReadiness())
        throw Error("Live identity and fresh readiness required");
      const value = await api("commands", JSON.parse(pending));
      notify(value.status);
      sessionStorage.removeItem("roboport-pending-operation");
      await refresh();
      renderReview();
      updateRecent();
    });
    retry.disabled = !usable() || !freshReadiness();
    actions.append(retry);
  }
  el.append(actions);
  const why = node("div", undefined, "why");
  why.id = "start-why";
  if (blockers.length) {
    why.append(
      node(
        "div",
        `Start is disabled — ${blockers.length} requirement${blockers.length === 1 ? "" : "s"} missing. The gateway re-checks every condition at dispatch regardless of this view.`,
        "lead",
      ),
    );
    const ol = node("ol");
    for (const b of blockers) ol.append(node("li", b));
    why.append(ol);
  } else
    why.append(
      node(
        "div",
        "All observed requirements met. The gateway independently checks every condition at dispatch.",
        "lead tone-green",
      ),
    );
  el.append(why);
  el.append(
    node(
      "div",
      "Only enabled, approved profiles can launch. A proposal is evidence for a reviewer; it is not an applied change. Local API charges: $0 when observed; host and electricity cost unknown.",
      "foot-note",
    ),
  );
}
function planColumn() {
  const col = node("div", undefined, "stack");
  const plan = node("section", undefined, "panel");
  plan.id = "review-plan";
  col.append(plan);
  const ready = card("Readiness & blockers");
  ready.append(
    node(
      "p",
      readiness?.blockers?.length
        ? readiness.blockers.join("\n")
        : readiness
          ? "Checks observed. Profile and manifest will be checked again at dispatch."
          : "No gateway observations in this session.",
    ),
  );
  for (const o of readiness?.observations || [])
    ready.append(
      node(
        "div",
        `${o.id}: ${o.state} · ${typeof o.value === "object" && o.value !== null ? "observed details in Settings" : (o.value ?? "Unknown")}`,
        "details",
      ),
    );
  col.append(ready);
  return col;
}
function recentRow(m) {
  const b = button("", () => {
    selectedMissionId = m.id;
    view = "Missions";
    render();
    window.scrollTo(0, 0);
  }, "recent");
  b.append(
    node("span", shortId(m.id), "id"),
    node("span", m.manifest.objective, "title"),
    stateMark(m, "chip solid"),
  );
  return b;
}
function updateRecent() {
  const c = $("recent-missions");
  if (!c) return;
  const row = node("div", undefined, "eyebrow-row");
  row.append(
    node("h2", "Active / recent missions"),
    button(
      "All missions →",
      () => {
        view = "Missions";
        render();
        window.scrollTo(0, 0);
      },
      "link",
    ),
  );
  c.replaceChildren(row);
  const list = node("div", undefined, "recent-list");
  if (!missions().length) list.append(node("div", "No missions observed", "empty"));
  missions()
    .slice(0, 5)
    .forEach((m) => list.append(recentRow(m)));
  c.append(list);
}
function incidentsCard() {
  const c = card("Evidence · incidents · decisions");
  const list = node("div", undefined, "incidents");
  const rows = [];
  for (const m of missions().slice(0, 12)) {
    const id = shortId(m.id);
    if (m.state === "failed")
      rows.push([
        "red",
        "✕",
        `Failed · ${id} · ${m.manifest.profile}`,
        m.outcome?.error
          ? text(m.outcome.error)
          : "terminal outcome recorded without grading success",
        m,
      ]);
    else if (m.state === "waiting-review")
      rows.push([
        "amber",
        "◐",
        `Awaiting reviewer · ${id}`,
        (m.manifest.reviewer_id || "any enrolled reviewer") +
          " · " +
          (m.manifest.independent_review
            ? "independent review required"
            : "operator checkpoint"),
        m,
      ]);
    else if (m.state === "unknown")
      rows.push([
        "steel",
        "?",
        `State unknown · ${id}`,
        "owned processes not reconciled · duplicate dispatch blocked",
        null,
      ]);
    else if (m.state === "completed") {
      const n = Object.keys(m.outcome?.artifact_hashes || {}).length;
      rows.push([
        "green",
        "▤",
        `${n} artifact${n === 1 ? "" : "s"} sealed · ${id}`,
        "hashes recorded · " +
          ((m.decisions || []).length
            ? "decision recorded"
            : "awaiting review decision"),
        m,
      ]);
    }
  }
  for (const [tone, icon, title, sub, m] of rows.slice(0, 5)) {
    const row = node("div", undefined, "incident");
    const mark = glyph(icon);
    mark.classList.add("tone-" + tone);
    row.append(mark);
    const copy = node("div");
    copy.append(node("div", title, "title"));
    const s = node("div", sub, "sub");
    if (m) {
      s.append(" · ");
      s.append(
        button(
          "open Evidence →",
          () => {
            reviewMissionId = m.id;
            reviewFilter = "all";
            artifact = null;
            artifactMissionId = null;
            artifactRequest++;
            view = "Evidence";
            render();
            window.scrollTo(0, 0);
          },
          "link",
        ),
      );
    }
    copy.append(s);
    row.append(copy);
    list.append(row);
  }
  if (!rows.length)
    list.append(node("div", "No incidents or decisions observed.", "empty"));
  const decisions = missions().reduce(
    (n, m) => n + (m.decisions || []).length,
    0,
  );
  list.append(
    node(
      "div",
      `Decisions recorded: ${decisions || "none"} · Quality / SLA: no observations`,
      "foot-note",
    ),
  );
  c.append(list);
  return c;
}
function renderHome() {
  const main = $("main");
  const h = node("h1", "Launch Pad", "sr-only");
  h.id = "h-launch";
  main.append(h);
  main.append(environmentPanel(), readinessTiles());
  if (!actor || !readiness?.ready || !freshReadiness()) main.append(blockerAlert());
  const pair = node("div", undefined, "grid-2");
  pair.append(draftCard(), planColumn());
  main.append(pair);
  const bottom = node("div", undefined, "grid-home");
  const recent = node("section", undefined, "panel");
  recent.id = "recent-missions";
  bottom.append(recent, incidentsCard());
  main.append(bottom);
  updateRecent();
  renderReview();
}

/* ---------- Missions ---------- */
function renderMissions() {
  const main = $("main");
  main.append(
    heading(
      "Missions",
      `${missions().length} observed · ${sourceLabel()}`,
      "Approval acceptance and runtime success are separate fields. A proposal never changes an environment automatically. Browser disconnect does not cancel work.",
    ),
  );
  const legend = node("div", undefined, "legend");
  for (const [tone, icon, label] of Object.values(MISSION_STATES))
    legend.append(withGlyph(toned("span", undefined, tone, "chip"), icon, label));
  main.append(legend);
  const bar = node("div", undefined, "actions");
  const observe = button(
    observing ? "Stop observation" : "Resume observation",
    toggleObservation,
  );
  observe.disabled = mode !== "Live" || !actor;
  bar.append(
    observe,
    node("span", "Pausing observation never cancels a mission.", "foot-note"),
  );
  main.append(bar);
  const list = node("div", undefined, "mission-list");
  list.setAttribute("role", "list");
  if (!missions().length) list.append(node("div", "No missions observed", "empty"));
  for (const m of missions()) {
    const row = missionRow(m, { controls: true });
    row.setAttribute("role", "listitem");
    list.append(row);
  }
  main.append(list);
}

/* ---------- Fleet ---------- */
function renderFleet() {
  RoboportFleet.render(
    $("main"),
    state.data || {},
    { node, button, card, glyph, toned },
    {
      mode: fleetMode,
      narrow: isNarrow(),
      source: sourceLabel(),
      setMode: (next) => {
        fleetMode = next;
        render();
      },
    },
  );
}

/* ---------- Evidence ---------- */
function selectEvidence(m, path) {
  return loadReviewArtifact(m, path);
}
function artifactsCard(m) {
  const c = card("Artifacts · typed references");
  const list = node("div", undefined, "artifact-list");
  const hashes = m.outcome?.artifact_hashes || m.evidence_hashes || {};
  const names = [
    ...new Set([...STANDARD_ARTIFACTS.map((s) => s[0]), ...Object.keys(hashes)]),
  ].slice(0, 40);
  for (const name of names) {
    const b = button("", () => selectEvidence(m, name), "artifact");
    const loaded = artifact?.path === name && artifactMissionId === m.id;
    b.setAttribute("aria-pressed", String(loaded));
    b.append(
      node("span", name, "name"),
      node("span", loaded ? artifact.bytes + " B" : "—", "size"),
      node(
        "span",
        STANDARD_ARTIFACTS.find((s) => s[0] === name)?.[1] || "sealed artifact",
        "type",
      ),
      node(
        "span",
        hashes[name]
          ? "sha256 " + hashes[name].slice(0, 16) + "…"
          : "hash recorded at completion",
        "hash",
      ),
    );
    b.disabled = mode !== "Live" || !m.run_dir;
    b.title = b.disabled
      ? "Artifacts are readable for Live missions with a recorded run directory"
      : "Load this artifact into the readable result";
    list.append(b);
  }
  c.append(list);
  c.append(
    node(
      "div",
      `Source: ${mode === "Replay" ? "imported replay" : "mission artifact store"} · ${Object.keys(hashes).length} sealed hash${Object.keys(hashes).length === 1 ? "" : "es"} · single-mission scope`,
      "foot-note",
    ),
  );
  return c;
}
function stepsCard(m) {
  const c = card("Typed step outputs · grading");
  const steps = stepsOf(m.id);
  const list = node("div", undefined, "steps");
  for (const s of steps) {
    const [tone, icon, label] = STEP_STATES[s.state] || ["steel", "?", s.state];
    const row = node("div", undefined, "step");
    row.append(
      node("span", s.step_id, "name"),
      node(
        "span",
        s.agent_id +
          (s.llm_calls !== undefined && s.llm_calls !== null
            ? ` · ${s.llm_calls} calls`
            : ""),
        "contract",
      ),
      withGlyph(toned("span", undefined, tone, "status"), icon, label),
    );
    list.append(row);
  }
  if (!steps.length)
    list.append(node("div", "No typed step observations for this mission.", "empty"));
  c.append(list);
  const failed = steps.find((s) => s.state === "failed");
  if (failed)
    c.append(
      node(
        "p",
        `✕ failed step · ${failed.step_id} · ${failed.error ? text(failed.error) : "no error detail recorded"}`,
        "tone-red",
      ),
    );
  return c;
}
function blockersCard(m) {
  const c = card("Blocker results");
  const rows = node("div", undefined, "kv-rows");
  const outcome = m.outcome || {};
  const add = (k, v, tone) => {
    const row = node("div", undefined, "kv");
    row.append(node("span", k, "k"), toned("span", v, tone, "v"));
    rows.append(row);
  };
  add(
    "Terminal outcome",
    outcome.status || m.state,
    outcome.status === "completed"
      ? "green"
      : outcome.status === "failed"
        ? "red"
        : "steel",
  );
  add(
    "Accounting",
    outcome.accounting_complete === false
      ? "incomplete"
      : outcome.status
        ? "complete"
        : "not recorded",
    outcome.accounting_complete === false ? "amber" : "muted",
  );
  add(
    "Sealed artifacts",
    String(Object.keys(outcome.artifact_hashes || {}).length),
    "muted",
  );
  add(
    "Grading",
    outcome.artifact_hashes?.["artifacts/grading.json"]
      ? "recorded · open grading artifact"
      : "not recorded",
    "muted",
  );
  c.append(rows);
  return c;
}
function ledgerCard(m) {
  const c = card("Budget ledger · measured");
  const ledger = state.data?.budgets?.[m.id];
  const grid = node("div", undefined, "ledger");
  const add = (k, v, note, tone) => {
    const el = node("div");
    el.append(node("div", k, "k"), toned("div", v, tone || "bright", "v"));
    if (note) el.append(node("div", note, "note"));
    grid.append(el);
  };
  if (ledger) {
    add("Model calls", ledger.llm_calls ?? "Unknown", "measured · budget.json");
    add("Tool calls", ledger.tool_calls ?? "Unknown", "measured");
    add("API spent", "$" + (ledger.spent_usd ?? "?"), "local route · not total cost", "green");
    add("Reserved", "$" + (ledger.reserved_usd ?? "?"), "measured");
    add(
      "Elapsed",
      ledger.elapsed_seconds === undefined
        ? "Unknown"
        : Math.round(ledger.elapsed_seconds) + " s",
      "run.start → terminal",
    );
  } else add("Ledger", "No observations", "budget.json not recorded", "steel");
  add(
    "Context limit",
    String(m.manifest.context_tokens ?? "?"),
    "completion limit " + (m.manifest.completion_tokens ?? "?"),
  );
  add("Electricity · host", "unknown", "not measured", "steel");
  add("GPU placement", "unknown", "target not observed", "steel");
  c.append(grid);
  return c;
}
function replayCard() {
  const c = card("Replay · exports");
  const actions = node("div", undefined, "actions");
  actions.append(
    button(
      "Open replay in Fleet",
      () => {
        view = "Fleet";
        render();
        window.scrollTo(0, 0);
      },
      "outline",
    ),
  );
  const safe = button("Safe export", () => {});
  safe.disabled = true;
  safe.title = "Classification policy not configured — safe export unavailable";
  actions.append(safe);
  c.append(actions);
  const importCard = node("div", undefined, "replay-tools"),
    file = node("input");
  file.type = "file";
  file.accept = ".log,.jsonl,.json";
  file.setAttribute("aria-label", "Replay log file");
  importCard.append(file, field("Replay telemetry run ID", "replay_run_id"));
  importCard.append(
    button("Load replay", async () => {
      const f = file.files[0];
      if (!f || f.size > 10 * 1024 * 1024)
        throw Error("Choose a log file up to 10MiB");
      const text_ = await f.text();
      const records = f.name.endsWith(".json")
        ? JSON.parse(text_)
        : text_
            .trim()
            .split("\n")
            .map((l) => JSON.parse(l));
      const value = await api("replay", {
        records,
        provenance: { telemetry_run_id: $("field-replay_run_id").value },
      });
      mode = "Replay";
      stream?.close();
      stream = null;
      state = value.snapshot;
      reviewed = null;
      artifact = value;
      artifactMissionId = null;
      artifactRequest++;
      render();
      setConnection("Replay · imported observations only", "blue");
      notify("Replay is read-only; live commands are unavailable.");
    }),
  );
  const replay = node("details");
  replay.append(node("summary", "Import a read-only replay"), importCard);
  c.append(replay);
  c.append(
    node(
      "div",
      "Safe export is unavailable until a classification policy governs redaction. Source fields render as text only.",
      "foot-note",
    ),
  );
  return c;
}
function renderEvidence() {
  const main = $("main");
  const m = reviewSelectorTarget();
  main.append(
    heading(
      "Evidence",
      m ? `${shortId(m.id)} · ${m.manifest.profile} · ${sourceLabel()}` : "no mission selected",
      "Run artifacts remain authoritative; this view stores exact references and hashes only. Review one mission at a time.",
    ),
  );
  const selected = reviewSelector(main);
  if (selected) {
    const grid = node("div", undefined, "grid-3");
    const col2 = node("div", undefined, "stack"),
      col3 = node("div", undefined, "stack");
    col2.append(stepsCard(selected), blockersCard(selected));
    col3.append(ledgerCard(selected), qualityCard(), replayCard());
    grid.append(artifactsCard(selected), col2, col3);
    main.append(grid);
  } else main.append(replayCard());
  const panel = card("Readable result");
  panel.id = "artifact";
  main.append(panel);
  renderEvidenceArtifact();
  if (selected) {
    const actions = node("div", undefined, "actions");
    actions.append(
      button(
        "Review this evidence",
        () => {
          view = "Approvals";
          render();
          window.scrollTo(0, 0);
        },
        "primary",
      ),
    );
    main.append(actions);
    if (mode === "Live" && selected.run_dir && artifactMissionId !== selected.id)
      loadReviewArtifact(selected).catch(notify);
  }
}
function qualityCard() {
  const c = card("Quality · SLA");
  c.append(
    node("div", "No observations", "big"),
    node(
      "p",
      "Zero samples in this scope. Nothing is implied about reliability.",
    ),
  );
  return c;
}
function eligibleMissions() {
  const all = missions()
    .slice()
    .sort((a, b) => b.created_at - a.created_at);
  const filterValue =
    reviewFilter ||
    (all.some((m) => m.state === "completed") ? "completed" : "all");
  return {
    all,
    filterValue,
    eligible: all.filter(
      (m) => filterValue === "all" || m.state === filterValue,
    ),
  };
}
function reviewSelectorTarget() {
  const { eligible } = eligibleMissions();
  return eligible.find((m) => m.id === reviewMissionId) || eligible[0] || null;
}
function reviewSelector(main) {
  const { filterValue, eligible } = eligibleMissions();
  if (!eligible.some((m) => m.id === reviewMissionId)) {
    reviewMissionId = eligible[0]?.id || null;
    if (mode !== "Replay") artifact = null;
    artifactMissionId = null;
    artifactRequest++;
  }
  const toolbar = node("div", undefined, "review-toolbar");
  const box = node("div", undefined, "field"),
    select = node("select");
  select.id = "review-mission";
  const lab = node("label", "Review mission");
  lab.htmlFor = select.id;
  for (const m of eligible) {
    const option = node(
      "option",
      `${m.manifest.profile} · ${shortId(m.id)} · ${m.state} · ${m.manifest.objective}`,
    );
    option.value = m.id;
    select.append(option);
  }
  select.value = reviewMissionId || "";
  select.disabled = !eligible.length;
  select.addEventListener("change", () => {
    reviewMissionId = select.value;
    artifact = null;
    artifactMissionId = null;
    artifactRequest++;
    render();
    $("review-mission")?.focus();
  });
  box.append(lab, select);
  const filterBox = node("div", undefined, "field"),
    filter = node("select"),
    filterLabel = node("label", "Mission status");
  filter.id = "review-status";
  filterLabel.htmlFor = filter.id;
  for (const [value, text_] of [
    ["completed", "Completed"],
    ["failed", "Failed"],
    ["waiting-review", "Waiting review"],
    ["all", "All missions"],
  ]) {
    const option = node("option", text_);
    option.value = value;
    filter.append(option);
  }
  filter.value = filterValue;
  filter.addEventListener("change", () => {
    reviewFilter = filter.value;
    render();
    $("review-status")?.focus();
  });
  filterBox.append(filterLabel, filter);
  toolbar.append(box, filterBox);
  const next = button("Next mission", () => {
    const index = eligible.findIndex((m) => m.id === reviewMissionId);
    reviewMissionId = eligible[(index + 1) % eligible.length].id;
    artifact = null;
    artifactMissionId = null;
    artifactRequest++;
    render();
    $("review-mission")?.focus();
  });
  next.disabled = eligible.length < 2;
  toolbar.append(next);
  main.append(toolbar);
  const selected = eligible.find((m) => m.id === reviewMissionId);
  if (selected) main.append(missionRow(selected));
  else
    main.append(
      node(
        "p",
        "No missions match this status. Choose All missions to inspect drafts or other outcomes.",
      ),
    );
  return selected;
}
async function loadReviewArtifact(m, path = "artifacts/final_output.json") {
  const request = ++artifactRequest,
    identity = actor?.id,
    requestMode = mode;
  artifact = null;
  artifactMissionId = null;
  reviewContext = null;
  reviewContextNotice = "";
  renderEvidenceArtifact();
  updateReviewButtons();
  try {
    const value = await api(
      `missions/${m.id}/evidence?path=${encodeURIComponent(path)}`,
    );
    if (
      request !== artifactRequest ||
      actor?.id !== identity ||
      mode !== requestMode ||
      reviewMissionId !== m.id ||
      !["Evidence", "Approvals"].includes(view)
    )
      return;
    artifact = value;
    artifactMissionId = m.id;
    renderEvidenceArtifact();
    updateReviewButtons();
    for (const b of document.querySelectorAll(".artifact"))
      b.setAttribute(
        "aria-pressed",
        String(b.querySelector(".name")?.textContent === path),
      );
    if (
      path === "artifacts/final_output.json" &&
      m.outcome?.artifact_hashes?.["artifacts/review_context.json"]
    ) {
      try {
        const context = await api(
          `missions/${m.id}/evidence?path=artifacts%2Freview_context.json`,
        );
        if (
          request !== artifactRequest ||
          actor?.id !== identity ||
          mode !== requestMode ||
          reviewMissionId !== m.id
        )
          return;
        if (
          context.content.manifest_hash !== m.manifest.manifest_hash ||
          context.content.final_output_sha256 !== value.sha256
        )
          throw Error("Supporting inputs do not match this displayed result.");
        reviewContext = context;
      } catch (error) {
        if (
          request !== artifactRequest ||
          actor?.id !== identity ||
          mode !== requestMode ||
          reviewMissionId !== m.id
        )
          return;
        reviewContextNotice = "Supporting inputs unavailable: " + error.message;
      }
      renderEvidenceArtifact();
      updateReviewButtons();
    }
  } catch (error) {
    if (
      request !== artifactRequest ||
      actor?.id !== identity ||
      mode !== requestMode ||
      reviewMissionId !== m.id
    )
      return;
    const c = $("artifact");
    if (c)
      c.replaceChildren(
        node("h2", "Result unavailable"),
        node("p", error.message),
      );
    throw error;
  }
}
function renderEvidenceArtifact() {
  const c = $("artifact");
  if (!c) return;
  c.replaceChildren(node("h2", "Readable result"));
  if (artifact) {
    const hash = node(
      "div",
      artifact.sha256 || artifact.normalized_log_sha256 || "",
      "review-hash",
    );
    hash.dataset.artifactHash = "true";
    c.append(
      node(
        "div",
        (artifact.path || "imported replay") + " · artifact SHA-256",
        "foot-note",
      ),
      hash,
    );
    RoboportReview.render(c, artifact);
    if (artifact.path === "artifacts/final_output.json") {
      const support = node("section");
      support.id = "review-context";
      support.append(node("h3", "Supporting inputs"));
      if (
        reviewContext?.content.manifest_hash ===
          missions().find((x) => x.id === artifactMissionId)?.manifest
            .manifest_hash &&
        reviewContext?.content.final_output_sha256 === artifact.sha256
      ) {
        support.append(
          node(
            "p",
            "Supporting packet SHA-256: " + reviewContext.sha256,
            "review-hash",
          ),
        );
        RoboportReview.render(support, reviewContext);
      } else {
        const m = missions().find((x) => x.id === artifactMissionId);
        support.append(
          node(
            "p",
            reviewContextNotice ||
              (m?.outcome?.artifact_hashes?.["artifacts/review_context.json"]
                ? "Loading the recorded supporting inputs…"
                : "This historical mission has no supporting packet. Its recorded execution inputs remain available in Evidence."),
          ),
        );
      }
      c.append(support);
    }
  } else
    c.append(
      node(
        "p",
        "Select a mission with recorded evidence. Its final result appears here; supporting artifacts are optional.",
      ),
    );
}

/* ---------- Approvals ---------- */
function reviewEvidenceReady(m) {
  return (
    mode === "Live" &&
    ["reviewer", "admin"].includes(actor?.role) &&
    (!m.manifest.reviewer_id || m.manifest.reviewer_id === actor.id) &&
    !(m.manifest.independent_review && m.manifest.owner === actor.id) &&
    reviewMissionId === m.id &&
    artifactMissionId === m.id &&
    artifact?.path === "artifacts/final_output.json" &&
    Boolean(artifact.sha256) &&
    (!m.outcome?.artifact_hashes?.["artifacts/review_context.json"] ||
      (reviewContext?.content.manifest_hash === m.manifest.manifest_hash &&
        reviewContext?.content.final_output_sha256 === artifact.sha256)) &&
    Boolean(decisionsDraft.get(m.id)?.trim())
  );
}
function reviewWhy(m) {
  if (mode !== "Live") return "Decisions require Live mode.";
  if (!["reviewer", "admin"].includes(actor?.role))
    return "Reviewer role required.";
  if (m.manifest.reviewer_id && m.manifest.reviewer_id !== actor.id)
    return "Named checkpoint reviewer " + m.manifest.reviewer_id + " required.";
  if (m.manifest.independent_review && m.manifest.owner === actor.id)
    return "Independent review profile — requester self-approval is rejected.";
  if (
    reviewMissionId !== m.id ||
    artifactMissionId !== m.id ||
    artifact?.path !== "artifacts/final_output.json" ||
    !artifact?.sha256
  )
    return "Inspect this mission's final output before recording a decision.";
  if (
    m.outcome?.artifact_hashes?.["artifacts/review_context.json"] &&
    !(
      reviewContext?.content.manifest_hash === m.manifest.manifest_hash &&
      reviewContext?.content.final_output_sha256 === artifact.sha256
    )
  )
    return (
      reviewContextNotice ||
      "Supporting inputs are still loading or do not match the displayed result."
    );
  if (!decisionsDraft.get(m.id)?.trim())
    return "Enter your rationale to enable a decision.";
  return "";
}
function updateReviewButtons() {
  const m = missions().find((x) => x.id === reviewMissionId);
  for (const b of document.querySelectorAll("[data-review-decision]"))
    b.disabled = !m || !reviewEvidenceReady(m);
  const why = document.querySelector("[data-review-why]");
  if (why && m) why.textContent = reviewWhy(m);
}
function renderApprovals() {
  const main = $("main");
  const waiting = missions().filter(
    (m) =>
      m.state === "waiting-review" ||
      (m.state === "completed" && !(m.decisions || []).length),
  ).length;
  const rosterNote = Object.values(readiness?.profile_readiness || {})
    .map((p) => p.reviewer_id)
    .filter(Boolean);
  main.append(
    heading(
      "Approvals",
      `${waiting} waiting · ${rosterNote.length ? "reviewers " + [...new Set(rosterNote)].join(", ") : "reviewer roster unconfigured"}`,
      "Decisions record authenticated identity, manifest and evidence hashes, rationale and timestamp. Independent-review profiles reject self-approval. Decisions never apply a proposed patch.",
    ),
  );
  if (!["reviewer", "admin"].includes(actor?.role))
    main.append(node("p", "Reviewer role required to record a decision.", "section-note"));
  const m = reviewSelector(main);
  if (!m) return;
  const grid = node("div", undefined, "review-grid");
  const queue = card("Reviewer queue");
  const item = node("div", undefined, "queue-item");
  const head = node("div", undefined, "queue-head");
  head.append(
    node("span", `${shortId(m.id)} · ${m.manifest.objective}`, "queue-title"),
    (m.decisions || []).length
      ? withGlyph(
          toned(
            "span",
            undefined,
            m.decisions.slice(-1)[0].verdict === "approved" ? "green" : "red",
            "chip",
          ),
          m.decisions.slice(-1)[0].verdict === "approved" ? "✓" : "⊘",
          m.decisions.slice(-1)[0].verdict,
        )
      : ["waiting-review", "completed"].includes(m.state)
        ? withGlyph(toned("span", undefined, "amber", "chip"), "◐", "waiting")
        : stateMark(m, "chip"),
  );
  item.append(head);
  const rows = [
    ["trigger", `${m.manifest.review_phase || "proposal"} checkpoint · mission ${m.state}`],
    [
      "reviewer",
      (m.manifest.reviewer_id || "any enrolled reviewer") +
        (m.manifest.independent_review
          ? " · independent process-owner review"
          : " · self-review permitted by pilot policy"),
    ],
    [
      "affected versions",
      `${Object.keys(m.manifest.component_hashes || {}).length} component hashes · models ${Object.keys(m.manifest.model_digests || {}).join(", ") || "none"}`,
    ],
    [
      "evidence",
      `${Object.keys(m.outcome?.artifact_hashes || {}).length} sealed artifacts` +
        (artifact?.sha256 && artifactMissionId === m.id
          ? " · displayed result sha256 " + artifact.sha256.slice(0, 16) + "…"
          : " · final output not yet inspected"),
    ],
  ];
  for (const [k, v] of rows) {
    const row = node("div", undefined, "row");
    row.append(node("span", k + " ", "k"), node("span", v));
    item.append(row);
  }
  const identity = node("details");
  identity.append(
    node("summary", "Manifest identity"),
    node("div", m.manifest.manifest_hash, "review-hash"),
  );
  item.append(identity);
  if (["reviewer", "admin"].includes(actor?.role)) {
    const box = field(
      "Review rationale",
      "rationale-" + m.id,
      "textarea",
      decisionsDraft.get(m.id) || "",
    );
    box.querySelector("textarea").addEventListener("input", (e) => {
      decisionsDraft.set(m.id, e.target.value);
      updateReviewButtons();
    });
    item.append(box);
  }
  const actions = node("div", undefined, "review-actions");
  for (const verdict of ["approved", "rejected"]) {
    const b = button(
      verdict === "approved" ? "Approve evidence" : "Reject evidence",
      async () => {
        if (!reviewEvidenceReady(m))
          throw Error(
            "Inspect this mission's final output and enter your rationale before recording a decision.",
          );
        const evidenceHash = artifact.sha256;
        await api("decisions", {
          mission_id: m.id,
          manifest_hash: m.manifest.manifest_hash,
          evidence_hash: evidenceHash,
          verdict,
          rationale: decisionsDraft.get(m.id) || "",
        });
        await refresh();
        render();
      },
      verdict === "approved" ? "primary" : "danger",
    );
    b.dataset.reviewDecision = "true";
    b.disabled = !reviewEvidenceReady(m);
    actions.append(b);
  }
  const escalate = button("Escalate", () => {});
  escalate.disabled = true;
  escalate.title = "Escalation path not configured";
  actions.append(escalate);
  item.append(actions);
  const why = node("div", reviewWhy(m), "warn");
  why.dataset.reviewWhy = "true";
  item.append(why);
  queue.append(item);
  const left = node("div", undefined, "stack");
  left.append(queue);
  const panel = card("Evidence under review");
  panel.id = "artifact";
  left.append(panel);
  grid.append(left);
  const right = node("div", undefined, "stack");
  const decisions = card("Decisions");
  if ((m.decisions || []).length)
    for (const d of m.decisions) {
      const row = node("div", undefined, "decision");
      row.append(
        node("span", stamp(d.timestamp), "k"),
        node(
          "span",
          `${d.verdict} · ${d.actor_id} · ${d.rationale} · evidence ${String(d.evidence_hash || "").slice(0, 12)}…`,
          "v",
        ),
      );
      decisions.append(row);
    }
  else
    decisions.append(
      node("div", "No decisions recorded", "big"),
      node(
        "p",
        "Each decision stores identity, manifest and evidence hashes, rationale and timestamp.",
      ),
    );
  right.append(decisions);
  for (const [k, v, note] of [
    ["Exceptions", "None", "Exception records carry an expiry and the accountable governance owner."],
    ["Escalations", "None", "Escalation is a separate state with its own trigger and reviewer."],
    ["Revocations", "None", "Revoking an approval blocks further dispatch under it immediately."],
  ]) {
    const c = card(k);
    c.classList.add("empty-card");
    c.append(node("div", v, "v"), node("p", note));
    right.append(c);
  }
  grid.append(right);
  main.append(grid);
  renderEvidenceArtifact();
  if (
    mode === "Live" &&
    m.run_dir &&
    (artifactMissionId !== m.id ||
      artifact?.path !== "artifacts/final_output.json")
  )
    loadReviewArtifact(m).catch(notify);
}

/* ---------- Settings ---------- */
function capability(name, stateKey, detail, blocker, action) {
  const [tone, icon] = CAP_STATES[stateKey] || CAP_STATES.unavailable;
  const c = node("div", undefined, "cap");
  const head = node("div", undefined, "cap-head");
  head.append(
    node("span", name, "cap-name"),
    withGlyph(toned("span", undefined, tone, "cap-state"), icon, stateKey),
  );
  c.append(head, node("div", detail, "cap-detail"));
  if (blocker) c.append(node("div", blocker, "cap-blocker"));
  if (action) {
    const slot = node("div", undefined, "cap-action");
    const b = button(action.label, action.run || (() => {}), action.disabled ? "" : "outline");
    b.disabled = Boolean(action.disabled);
    if (action.why) b.title = action.why;
    slot.append(b);
    c.append(slot);
  }
  return c;
}
function renderSettings() {
  const main = $("main");
  const head = heading(
    "Settings",
    "capabilities by state",
    undefined,
  );
  const legend = node("div", undefined, "state-legend");
  for (const [key, [tone, icon]] of Object.entries(CAP_STATES))
    legend.append(withGlyph(toned("span", undefined, tone), icon, key));
  head.append(legend);
  main.append(head);
  const caps = readiness?.capabilities || {};
  const fresh = Boolean(readiness) && freshReadiness();
  const profiles = Object.entries(readiness?.profiles || {});
  const profileReadiness = readiness?.profile_readiness || {};
  const routes = Object.entries(readiness?.model_routes || {});
  const models = modelObservations();
  const compliance = observation("compliance");
  const groups = [
    [
      "Environments · connection providers",
      [
        capability(
          $("environment").textContent,
          fresh ? "verified" : readiness ? "observation only" : "proposed",
          "Expected profile: hardware tier A (NVIDIA ≥ 30 GiB VRAM · ≥ 64 GiB RAM · ≥ 20 GiB free). " +
            (readiness
              ? `Observed machine ${observation("machine")?.value ?? "unknown"} on the loopback gateway.`
              : "No gateway observation in this session."),
          readiness?.blockers?.length ? readiness.blockers.join(" · ") : fresh ? "" : readiness ? "Observation stale — refresh." : "Not observed — gateway session required.",
          actor
            ? {
                label: "Refresh observations",
                run: async () => {
                  await refresh();
                  render();
                },
              }
            : { label: "Refresh observations", disabled: true, why: "Sign in first" },
        ),
        capability(
          "LAN relay / wake provider",
          caps.remote_wake ? "implemented" : "unavailable",
          "Wake and remote connection need a configured relay with capability discovery and a connection acceptance test.",
          caps.remote_wake ? "" : "Not supported in this slice. Wake is not an Entra action.",
          { label: "Configure relay", disabled: true, why: "Later slice" },
        ),
        capability(
          "Entra · Intune Remote Desktop policy",
          "observation only",
          compliance?.source ||
            "Prior policy success is an observation; it is not an active connection or remote-wake capability.",
          compliance?.state === "observed" ? "" : "No current compliance observation received.",
          null,
        ),
      ],
    ],
    [
      "Identity · roles · modes",
      [
        capability(
          "Local operator enrollment",
          "implemented",
          "The gateway owns authenticated sessions, origin and CSRF checks, rate limits and authorization — applies to local HTTP callers too." +
            (actor ? ` Signed in as ${actor.id} (${actor.role}).` : ""),
          actor
            ? ""
            : enrollmentRequired
              ? "No identities enrolled — use launchpad_admin.py on this workstation."
              : "No operator session on this console.",
          { label: "Enroll via launchpad_admin.py", disabled: true, why: "Terminal enrollment only; never through a URL" },
        ),
        capability(
          "Reviewer roster",
          profiles.length && Object.values(profileReadiness).every((p) => p.ready)
            ? "implemented"
            : "not configured",
          profiles.length
            ? profiles
                .map(
                  ([name]) =>
                    `${name}: ${profileReadiness[name]?.reviewer_id || "unconfigured"}`,
                )
                .join(" · ")
            : "Reviewer identities, independent process-owner assignment and approval-record authority.",
          Object.values(profileReadiness)
            .flatMap((p) => p.blockers || [])
            .join(" · "),
          null,
        ),
        capability(
          "Operating modes",
          "implemented",
          "Demo (fixtures) · Replay (imported run) · Live (gateway). Permanently labelled; demo never appears as live. Current: " + mode + ".",
          mode === "Live" && !actor ? "Live commands need a signed-in session." : "",
          {
            label: mode === "Live" ? "Switch to Demo" : "Switch to Live",
            run: () => (mode === "Live" ? $("demo-mode") : $("live-mode")).click(),
          },
        ),
      ],
    ],
    [
      "Registry · catalogues · model routes",
      [
        capability(
          "Local mission profiles",
          profiles.some(([, p]) => p.enabled) ? "implemented" : "not configured",
          profiles.length
            ? profiles
                .map(
                  ([name, p]) =>
                    `${name}: ${p.enabled ? "enabled" : "not enabled"} · tools ${(p.tools || []).join(", ") || "none"} · roots ${(p.roots || []).length}`,
                )
                .join(" · ")
            : "Explicit policy and enrollment required; nothing is enabled by default.",
          profiles.some(([, p]) => p.enabled) ? "" : "No profile enabled by policy.",
          null,
        ),
        capability(
          "Model routes",
          routes.length && models.length && models.every((m) => m.state === "observed")
            ? "implemented"
            : routes.length
              ? "not configured"
              : "unavailable",
          routes.length
            ? routes
                .map(([hint, r]) => `${hint}: ${r.provider || "?"} · ${r.model || "?"}`)
                .join(" · ")
            : "No model routes observed.",
          models.some((m) => m.state !== "observed")
            ? "Configured local model unavailable — check Ollama."
            : "Placement on target unverified.",
          { label: "Verify on target", disabled: true, why: "GPU placement is not observable from this console" },
        ),
        capability(
          "Enterprise register synchronization",
          "not configured",
          "Permanent AGT/SKL/PRM/INS/MCP IDs, owners, approvals, review and pilot dates live in the enterprise register.",
          "Canonical owner and source not identified — no synchronization.",
          { label: "Preview import", disabled: true, why: "Owner not identified" },
        ),
        capability(
          "OpenAI provider / sessions",
          caps.openai_sessions ? "implemented" : "unavailable",
          "Optional adapter — data-egress approval and priced budgets are route gates.",
          caps.openai_sessions ? "" : "Not authorized in this slice.",
          { label: "Configure provider", disabled: true, why: "Separately authorized capability" },
        ),
      ],
    ],
    [
      "Data · retention · credentials",
      [
        capability(
          "Classification · retention",
          actor ? "proposed" : "not configured",
          actor
            ? `This identity may read ${actor.classifications.join(" and ")} missions. Classification governs access, safe exports, logs and retention.`
            : "Classification governs access, safe exports, logs and retention.",
          "Retention policy not configured — safe export disabled.",
          { label: "Define policy", disabled: true, why: "Policy authority not identified" },
        ),
        capability(
          "Credentials",
          "implemented",
          "Referenced by name only; never bundled or exposed through a URL. None stored in this console.",
          "",
          null,
        ),
        capability(
          "Tenant corpus retrieval / training",
          caps.corpus ? "implemented" : "unavailable",
          "Retrieval and training require owner, classification, approved purpose, population and date range, exclusions, retention, evaluations and disposal state.",
          caps.corpus ? "" : "Approval evidence missing. Hardware capacity is not approval to ingest.",
          { label: "Request approval record", disabled: true, why: "Later slices" },
        ),
        capability(
          "Container administration",
          caps.container_admin ? "implemented" : "unavailable",
          "Container lifecycle on the workstation stays outside this console.",
          caps.container_admin ? "" : "Not supported in this slice.",
          null,
        ),
      ],
    ],
  ];
  for (const [title, items] of groups) {
    const group = node("div", undefined, "cap-group");
    group.append(node("div", title, "eyebrow"));
    const grid = node("div", undefined, "cap-grid");
    for (const item of items) grid.append(item);
    group.append(grid);
    main.append(group);
  }
  const c = card("Environment observations");
  c.classList.add("observations");
  if (!readiness) c.append(node("p", "No observations in this session."));
  for (const o of readiness?.observations || []) {
    const d = node("details");
    d.append(
      node("summary", `${o.id} · ${o.state}`),
      node("pre", JSON.stringify(o, null, 2)),
    );
    c.append(d);
  }
  main.append(c);
}

/* ---------- render, session, transport ---------- */
function render() {
  nav();
  updateChrome();
  const main = $("main");
  main.replaceChildren();
  if (!actor) {
    renderHome();
    renderLogin(enrollmentRequired);
    return;
  }
  if (view === "Launch Pad") renderHome();
  else if (view === "Missions") renderMissions();
  else if (view === "Fleet") renderFleet();
  else if (view === "Evidence") renderEvidence();
  else if (view === "Approvals") renderApprovals();
  else renderSettings();
}
function renderLogin(enrollment = false) {
  const c = card("Connect your local identity");
  c.classList.add("login");
  c.append(
    node(
      "p",
      enrollment
        ? "No identities enrolled. Use launchpad_admin.py on this workstation to enroll a named operator and reviewer."
        : "Sign in with an explicitly enrolled local identity. Intune registration does not create a console session.",
    ),
  );
  const form = node("form");
  form.append(
    field("Username", "username"),
    field("Password", "password", "password"),
  );
  const b = node("button", "Sign in", "primary");
  b.type = "submit";
  form.append(b);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const result = await api("login", {
        username: $("field-username").value,
        password: $("field-password").value,
      });
      actor = result.actor;
      csrf = result.csrf;
      await refresh();
      formDraft = readForm();
      render();
      connect();
    } catch (error) {
      notify(error);
    }
  });
  c.append(form);
  $("main").prepend(c);
}
async function refresh() {
  if (!actor) return;
  const [observations, snapshot] = await Promise.all([
    api("readiness"),
    api("snapshot"),
  ]);
  readiness = observations;
  state =
    stream && state.stream_id ? { ...state, data: snapshot.data } : snapshot;
  updateChrome();
  setConnection(
    readiness.ready
      ? "Gateway observed · profile checks at dispatch"
      : "Blocked / unknown · check readiness",
    readiness.ready ? "green" : "amber",
  );
  $("freshness").textContent = "Observed " + clock(readiness.observed_at);
}
setInterval(() => {
  if (actor && mode === "Live" && readiness && !freshReadiness()) {
    if (readiness.stale) return;
    setConnection("Readiness stale · refresh required", "amber");
    readiness.stale = true;
    readiness.ready = false;
    if (view === "Launch Pad") {
      formDraft = readForm();
      render();
    } else renderReview();
  }
}, 1000);
function connect() {
  stream?.close();
  if (mode !== "Live" || !actor || !observing) return;
  const connection = new EventSource("/api/launchpad/events");
  stream = connection;
  stream.onmessage = (e) => {
    if (mode !== "Live" || stream !== connection || !observing) return;
    const next = reduceEnvelope(state, JSON.parse(e.data));
    if (next.resync) {
      connect();
      return;
    }
    state = next;
    if (view === "Launch Pad") {
      updateRecent();
      renderReview();
    } else if (
      view === "Fleet" &&
      fleetMode === "topology" &&
      RoboportFleet.update(state.data || {})
    ) {
      // The embedded console animates the change; re-rendering would reload it.
    } else if (["Missions", "Fleet"].includes(view)) {
      render();
    }
  };
  stream.onerror = () => {
    setConnection("Disconnected / stale · missions may continue", "amber");
  };
}
narrowQuery.addEventListener("change", () => {
  if (view === "Fleet") render();
});
$("demo-mode").addEventListener("click", () => {
  mode = "Demo";
  artifactRequest++;
  artifact = null;
  artifactMissionId = null;
  stream?.close();
  stream = null;
  state = {
    environment_id: "Demo",
    data: { missions: [], agents: [], steps: [], runtime: {} },
  };
  render();
  notify("Demo is read-only. No mission commands will be sent.");
});
$("live-mode").addEventListener("click", async () => {
  try {
    mode = "Live";
    await refresh();
    render();
    connect();
  } catch (error) {
    notify(error);
  }
});
$("sign-out").addEventListener("click", async () => {
  try {
    await api("logout", {});
    stream?.close();
    stream = null;
    actor = null;
    readiness = null;
    decisionsDraft.clear();
    csrf = "";
    state = {};
    artifact = null;
    artifactMissionId = null;
    reviewMissionId = null;
    reviewFilter = null;
    artifactRequest++;
    reviewed = null;
    selectedMissionId = null;
    render();
    setConnection("Signed out · gateway session required", "steel");
  } catch (error) {
    notify(error);
  }
});
/* Deep link: /#fleet opens Fleet -> Topology (Roboport Control's CONSOLE
   button); any view name works, e.g. /#missions. Unknown hashes are ignored. */
function applyLocationView() {
  const target = decodeURIComponent(location.hash.slice(1)).toLowerCase();
  const match = views.find(
    ([name]) => name.toLowerCase().replace(/\s+/g, "-") === target,
  );
  if (!match) return false;
  view = match[0];
  if (view === "Fleet") fleetMode = "topology";
  return true;
}
applyLocationView();
window.addEventListener("hashchange", () => {
  if (applyLocationView()) render();
});
(async () => {
  try {
    const session = await api("session");
    actor = session.actor;
    csrf = session.csrf || "";
    enrollmentRequired = Boolean(session.enrollment_required);
    if (actor) {
      await refresh();
      render();
      connect();
    } else {
      render();
      setConnection("Not signed in · gateway session required", "steel");
    }
  } catch (error) {
    render();
    notify(error);
  }
})();
