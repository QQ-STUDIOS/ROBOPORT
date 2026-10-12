// ROBOPORT Launch Pad — six-section operator shell over demo fixtures.
// No framework, no build step: state lives in `state`, each section renders to an HTML string,
// and clicks are routed through data-action attributes on #lp.

import {
  NAV, ENVIRONMENT, DEFAULT_DRAFT, READINESS, BASE_BLOCKERS, MANIFEST_COMPONENTS, LAUNCH_INCIDENTS,
  MISSION_STATES, MISSIONS, AGENT_STATES, FLEET, ARTIFACTS, STEPS, BLOCKER_RESULTS, LEDGER,
  APPROVALS, APPROVAL_EMPTIES, CAPABILITY_STATES, SETTINGS_GROUPS,
} from "./fixtures.js";

const DRAFT_KEY = "rbp.launchpad.draft.v1";
const NARROW_PX = 720;
const SECTIONS = NAV.map(n => n[0]);
// The Ops Console lives one directory up (dashboard/) and is served by the same static server.
const CONSOLE_SRC = "../Roboport%20Ops%20Console.dc.html";

const state = {
  section: "launch",
  narrow: false,
  fleetMode: "topology",
  selMission: "M-0004",
  showPlan: false,
  savedAt: null,
  dirty: false,
  draft: { ...DEFAULT_DRAFT },
};

const $ = id => document.getElementById(id);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// ── derived values ──────────────────────────────────────────────────────

function fnv1a(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return "fnv1a:" + h.toString(16).padStart(8, "0");
}

const isRegulated = d => d.dataClass !== "Internal";

function reviewerReq(d) {
  return isRegulated(d) ? `Independent process-owner review (${d.dataClass})` : "Operator review checkpoint (Internal)";
}

function blockers(d) {
  const list = [...BASE_BLOCKERS];
  if (isRegulated(d)) list.push(`Independent process-owner reviewer not configured for ${d.dataClass} data`);
  return list;
}

// Key order is part of the preview digest — keep it stable.
function manifest(d) {
  return {
    environment: ENVIRONMENT.name,
    mission_id: "M-0006 (draft)",
    owner: "unassigned",
    objective: d.objective,
    project_root: d.root,
    approved_roots: [],
    crew: d.crew,
    components: MANIFEST_COMPONENTS,
    model_route: "ollama://localhost · qwen3:14b · placement unverified on target",
    data_classification: d.dataClass,
    allowed_tools: ["validate.py", "diff_runs.py", "read-only repo scan"],
    connector_routes: [],
    limits: { llm_calls: d.calls, wall_time_min: d.minutes, context_tokens: d.context, completion_tokens: 2048, api_spend_usd: 0 },
    success_criteria: d.criteria,
    review: { required: isRegulated(d) ? "independent process-owner review" : "operator review checkpoint", reviewer: "unconfigured", self_approval: "rejected" },
    profile: "bounded local static-inspection / proposal (not enabled)",
  };
}

function manifestInfo(d) {
  const text = JSON.stringify(manifest(d), null, 2);
  return { text, hash: fnv1a(text) };
}

function planRows(d) {
  return [
    ["Environment", `${ENVIRONMENT.name} · unreachable`],
    ["Crew / version", d.crew],
    ["Components", `${MANIFEST_COMPONENTS.length} pinned (AGT ×4 · PRM ×1 · SKL ×1)`],
    ["Model route", "Ollama local · qwen3:14b · placement unverified"],
    ["Data class · tools", `${d.dataClass} · validate.py, diff_runs.py, read-only scan`],
    ["Limits", `${d.calls} calls · ${d.minutes} min · ${Number(d.context).toLocaleString()} ctx · $0.00`],
    ["Review", reviewerReq(d)],
    ["Owner", "unassigned"],
  ];
}

function savedLabel() {
  if (!state.savedAt) return "unsaved · local store";
  const at = new Date(state.savedAt).toLocaleTimeString();
  return state.dirty ? `unsaved changes · last saved ${at}` : `saved ${at} · local store`;
}

// ── shared fragments ────────────────────────────────────────────────────

const pill = (tone, icon, label, extra = "") =>
  `<span class="pill b-${tone} ${extra}"><span aria-hidden="true">${icon}</span>${esc(label)}</span>`;
const status = (tone, icon, label) =>
  `<span class="status t-${tone}"><span aria-hidden="true">${icon}</span>${esc(label)}</span>`;
const kv = (k, v) => `<div><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`;
const ghostBtn = (label, why, size = "btn--sm") =>
  `<button type="button" class="btn btn--ghost ${size}" disabled title="${esc(why)}">${esc(label)}</button>`;

// ── sections ────────────────────────────────────────────────────────────

function renderLaunch() {
  const d = state.draft;
  const env = ENVIRONMENT;
  const recent = MISSIONS.slice(0, 4).map(m => {
    const [tone, icon, label] = MISSION_STATES[m.state];
    return `<button type="button" class="mission-chip" data-action="open-mission" data-id="${m.id}">
      <span class="mission-chip__id">${m.id}</span><span class="mission-chip__title">${esc(m.title)}</span>${pill(tone, icon, label)}</button>`;
  }).join("");
  const incidents = LAUNCH_INCIDENTS.map(e => `<div class="event"><span aria-hidden="true" class="t-${e.tone}">${e.icon}</span><div>
      <div class="t-bright">${esc(e.title)}</div>
      <div class="t-muted">${esc(e.note)}${e.link ? `<button type="button" class="link-btn" data-action="go" data-section="${e.link}">open Evidence →</button>` : ""}</div></div></div>`).join("");

  return `<section class="section" aria-labelledby="h-launch" data-screen-label="Launch Pad">
    <h1 id="h-launch" class="sr-only">Launch Pad</h1>

    <div class="card grid grid--300">
      <div>
        <div class="eyebrow eyebrow--mb6">Environment</div>
        <div class="row-wrap" style="align-items:center;gap:10px">
          <select class="env-select" aria-label="Select environment"><option>${env.name}</option><option disabled>+ Add environment (Settings)</option></select>
          ${pill("amber", "⚠", env.reach, "pill--lg")}
        </div>
        <div class="small t-muted mt10">Last observed <span class="t-bright">${env.observedAt}</span> · source: ${esc(env.source)} · device not listed · <span class="t-amber">stale</span></div>
        <div class="small t-muted mt4">Power state: <span class="t-bright">unknown</span> — no supported evidence distinguishes offline, sleeping or unreachable.</div>
      </div>
      <div>
        <div class="eyebrow eyebrow--mb6">Expected profile · <span class="t-amber eyebrow__plain">unverified on target</span></div>
        <div class="kv-grid kv-grid--150">
          ${env.profile.map(([k, v, warn]) => `<div><span class="t-steel">${k}</span> <span class="t-bright">${esc(v)}</span>${warn ? ` <span class="t-amber">· ${esc(warn)}</span>` : ""}</div>`).join("")}
        </div>
      </div>
    </div>

    <div class="grid grid--readiness">
      ${READINESS.map(r => `<div class="readiness b-${r.tone}">
        <div class="readiness__label">${esc(r.label)}</div>
        <div class="readiness__state"><span aria-hidden="true">${r.icon}</span>${esc(r.state)}</div>
        <div class="readiness__detail">${esc(r.detail)}</div>
        <div class="readiness__fresh">${esc(r.fresh)}</div></div>`).join("")}
    </div>

    <div class="banner" role="alert">
      <div class="banner__icon" aria-hidden="true">!</div>
      <div class="banner__body">
        <div class="banner__title">Blocked — no supported connection route to ${env.name}</div>
        <div class="banner__text">Next supported action: configure a LAN relay or enroll an operator session on the workstation. Entra/Intune Remote Desktop policy success is a prior policy observation, not an active connection; wake is not an Entra action. Drafts can be saved while the machine is unreachable.</div>
      </div>
      <button type="button" class="btn btn--amber" data-action="go" data-section="settings">Open Settings → Connections</button>
    </div>

    <div class="grid grid--420">
      <form class="card card--col" id="draft-form" onsubmit="return false">
        <div class="card-head"><div class="eyebrow">Mission draft · <span class="t-bright">M-0006</span></div><div class="foot-note t-muted" id="saved-label">${savedLabel()}</div></div>
        <label class="field"><span class="field__label">Objective</span><textarea class="input" rows="2" name="objective">${esc(d.objective)}</textarea></label>
        <label class="field"><span class="field__label">Project root <span class="t-amber">· not within an approved root (none configured)</span></span><input class="input" type="text" name="root" value="${esc(d.root)}" spellcheck="false"></label>
        <div class="form-row form-row--180">
          <label class="field"><span class="field__label">Crew / version</span><select class="input" name="crew">
            ${[["JD-Crew v0.9.3", "JD-Crew v0.9.3 · local registry (unreconciled)"], ["REA investigation v0.2.0", "REA investigation v0.2.0 · local registry"]].map(([v, l]) => `<option value="${v}"${d.crew === v ? " selected" : ""}>${l}</option>`).join("")}
          </select></label>
          <label class="field"><span class="field__label">Data classification</span><select class="input" name="dataClass">
            ${["Internal", "Restricted", "Regulated"].map(v => `<option value="${v}"${d.dataClass === v ? " selected" : ""}>${v}</option>`).join("")}
          </select></label>
        </div>
        <div class="form-row form-row--120">
          <label class="field"><span class="field__label">LLM calls</span><input class="input" type="number" min="1" name="calls" value="${esc(d.calls)}"></label>
          <label class="field"><span class="field__label">Wall time (min)</span><input class="input" type="number" min="1" name="minutes" value="${esc(d.minutes)}"></label>
          <label class="field"><span class="field__label">Context (tokens)</span><input class="input" type="number" min="1024" step="1024" name="context" value="${esc(d.context)}"></label>
          <div class="field"><span class="field__label">API spend</span><div class="input input--static">$0.00 · local route</div></div>
        </div>
        <label class="field"><span class="field__label">Success criteria</span><textarea class="input" rows="3" name="criteria">${esc(d.criteria)}</textarea></label>
        <div class="form-row form-row--200">
          <div class="tile"><div class="k" style="margin-bottom:4px">Allowed tools · profile</div><div class="t-bright">validate.py · diff_runs.py · read-only repo scan</div><div class="t-muted" style="margin-top:2px">No connector routes granted. Mutation tools: not in profile.</div></div>
          <div class="tile"><div class="k" style="margin-bottom:4px">Reviewer requirement</div><div class="t-bright" id="reviewer-req">${esc(reviewerReq(d))}</div><div class="t-amber" style="margin-top:2px">Reviewer roster: unconfigured · self-approval rejected</div></div>
        </div>
      </form>

      <div class="stack">
        <div class="card card--col10" id="plan-card">${renderPlanCard()}</div>
      </div>
    </div>

    <div class="grid grid--380">
      <div class="card">
        <div class="card-head card-head--mb"><div class="eyebrow">Active / recent missions</div><button type="button" class="link-btn" data-action="go" data-section="missions">All missions →</button></div>
        <div class="list">${recent}</div>
      </div>
      <div class="card">
        <div class="eyebrow eyebrow--mb10">Evidence · incidents · decisions</div>
        <div class="list list--8 small">${incidents}
          <div class="foot-note mt4">Decisions recorded: none · Quality / SLA: no observations</div>
        </div>
      </div>
    </div>
  </section>`;
}

// The plan card re-renders on every draft edit; the form itself does not, so focus and caret survive.
function renderPlanCard() {
  const d = state.draft;
  const { text, hash } = manifestInfo(d);
  const bl = blockers(d);
  const body = state.showPlan
    ? `<pre class="manifest">${esc(text)}</pre>`
    : `<div class="kv-grid kv-grid--170">${planRows(d).map(([k, v]) => kv(k, v)).join("")}</div>`;
  return `<div class="card-head"><div class="eyebrow">Launch plan · persisted manifest</div><div class="foot-note t-muted">preview digest <span class="t-bright">${hash}</span> · sha256 assigned by gateway at dispatch</div></div>
    ${body}
    <div class="row-wrap mt4">
      <button type="button" class="btn btn--secondary" data-action="save-draft">Save draft</button>
      <button type="button" class="btn btn--cyan" data-action="toggle-plan" aria-expanded="${state.showPlan}">${state.showPlan ? "Hide launch plan" : "Review launch plan"}</button>
      <button type="button" class="btn btn--start" disabled aria-describedby="start-why"><span aria-hidden="true">⏵</span>Start when ready</button>
    </div>
    <div id="start-why" class="start-why">
      <div class="start-why__lead">Start is disabled — ${bl.length} requirements missing. The gateway re-checks every condition at dispatch regardless of this view.</div>
      <ol>${bl.map(b => `<li>${esc(b)}</li>`).join("")}</ol>
    </div>`;
}

function renderMissions() {
  const legend = Object.values(MISSION_STATES).map(([tone, icon, label]) => `<span class="pill pill--legend b-${tone}"><span aria-hidden="true">${icon}</span>${label}</span>`).join("");
  const rows = MISSIONS.map(m => {
    const [tone, icon, label] = MISSION_STATES[m.state];
    const open = state.selMission === m.id;
    const cancelWhy = m.state === "running" || m.state === "queued"
      ? "Cancel requires a server-supported command with an observable terminal outcome — no gateway in demo"
      : "Mission is not in a cancellable state";
    const detail = !open ? "" : `<div class="mission__detail" id="md-${m.id}">
        <div class="kv-grid kv-grid--140">
          ${kv("Owner", m.owner)}${kv("Baseline intent", m.baseline)}${kv("Input hash", m.inputHash)}${kv("Config hash", m.configHash)}${kv("Reviewer requirement", m.reviewer)}${kv("Source", m.source)}
          <div class="mission__actions">
            ${ghostBtn("Cancel mission", cancelWhy)}
            ${ghostBtn("Stop observation", "Observation is not running in demo mode")}
            <span class="foot-note">Closing this page never cancels a mission.</span>
          </div>
        </div>
        <div>
          <div class="k eyebrow--mb8">Event timeline</div>
          <ol class="timeline">${m.timeline.map(ev => `<li><span class="t-steel">${esc(ev.t)}</span><span class="t-bright">${esc(ev.text)}</span></li>`).join("")}</ol>
        </div>
      </div>`;
    return `<div role="listitem" class="mission${open ? " is-open" : ""}">
      <button type="button" class="mission__row" data-action="toggle-mission" data-id="${m.id}" aria-expanded="${open}"${open ? ` aria-controls="md-${m.id}"` : ""}>
        <span class="mission__id">${m.id}</span>
        <span class="mission__title">${esc(m.title)}</span>
        <span class="mission__state b-${tone}"><span aria-hidden="true">${icon}</span>${label}</span>
        <span class="mission__field"><span class="t-steel">approval </span>${esc(m.approval)}</span>
        <span class="mission__field"><span class="t-steel">runtime </span>${esc(m.runtime)}</span>
        <span class="mission__updated">${esc(m.updated)}</span>
      </button>${detail}</div>`;
  }).join("");

  return `<section class="section" aria-labelledby="h-missions" data-screen-label="Missions">
    <div class="section-head"><h1 id="h-missions" class="h1">Missions <span class="h1__meta">· ${MISSIONS.length} · fixture records</span></h1><div class="lede">Approval acceptance and runtime success are separate fields. A proposal never changes an environment automatically.</div></div>
    <div class="row-wrap" style="gap:6px">${legend}</div>
    <div role="list" class="list">${rows}</div>
  </section>`;
}

function renderFleet() {
  const canvas = !state.narrow && state.fleetMode === "topology";
  const topoOn = state.fleetMode === "topology" && !state.narrow;
  const body = canvas
    ? `<div class="stage"><iframe src="${CONSOLE_SRC}" title="Ops Console — Port, Flow and DAG topology (demo backend)" loading="lazy"></iframe></div>`
    : `<div class="card card--flush">
        <div class="roster-head">Roster · fixture snapshot · ${FLEET.length} agents</div>
        ${FLEET.map(a => {
          const [tone, icon, label] = AGENT_STATES[a.state];
          return `<div class="roster-row">
            <div class="roster-row__id">${a.id}</div>
            <div class="roster-row__cells">
              <span><span class="roster-row__k">state</span>${status(tone, icon, label)}</span>
              <span><span class="roster-row__k">station · step</span><span class="t-bright">${esc(a.station)}</span></span>
              <span><span class="roster-row__k">task</span><span class="t-muted">${esc(a.task)}</span></span>
              <span><span class="roster-row__k">progress</span><span class="bar b-${tone}"><span class="bar__track" role="progressbar" aria-valuenow="${a.pct}" aria-valuemin="0" aria-valuemax="100" aria-label="${a.id} progress"><span class="bar__fill" style="width:${a.pct}%"></span></span><span class="bar__pct">${a.pct}%</span></span></span>
              <span><span class="roster-row__k">context tokens</span><span class="t-muted">${esc(a.ctx)}</span></span>
              <span class="roster-row__wide"><span class="roster-row__k">relationships</span><span class="t-muted">${esc(a.rel)}</span></span>
            </div></div>`;
        }).join("")}
        <div class="roster-foot">Energy percentages shown in the topology are a simulation artefact and are not displayed here (no measured resource field). Snapshot: fixture · 2026-10-12 09:12 ET.</div>
      </div>`;

  return `<section class="section section--tight" aria-labelledby="h-fleet" data-screen-label="Fleet">
    <div class="section-head section-head--center">
      <h1 id="h-fleet" class="h1">Fleet <span class="h1__meta">· 5 agents · 8 stations · demo backend</span></h1>
      <div class="seg" role="group" aria-label="Fleet view">
        <button type="button" class="seg__btn" data-action="fleet-mode" data-mode="topology" aria-pressed="${topoOn}"${state.narrow ? " disabled" : ""} title="${state.narrow ? "Topology is desktop-only; narrow screens use the list" : "Animated Port / Flow / DAG views"}">⬡ Topology</button>
        <button type="button" class="seg__btn" data-action="fleet-mode" data-mode="table" aria-pressed="${!topoOn}">▤ Table</button>
      </div>
    </div>
    <div class="lede">Topology embeds the Ops Console (Port / Flow / DAG) on its demo backend. The table is the accessible equivalent — a fixture snapshot of the same roster, not synchronized with the animation. “+ Agent” in the console enters no registration flow here; agent creation requires registration and evaluation gates.</div>
    ${body}
  </section>`;
}

function renderEvidence() {
  return `<section class="section" aria-labelledby="h-evidence" data-screen-label="Evidence">
    <div class="section-head"><h1 id="h-evidence" class="h1">Evidence <span class="h1__meta">· M-0004 · run 41 vs baseline run 40 · fixture import</span></h1><div class="lede">Run artifacts remain authoritative; this view stores exact references and hashes only.</div></div>
    <div class="grid grid--340">
      <div class="card">
        <div class="eyebrow eyebrow--mb10">Artifacts · typed references</div>
        <div class="list">${ARTIFACTS.map(f => `<div class="artifact"><span class="artifact__name" title="${esc(f.name)}">${esc(f.name)}</span><span class="t-steel">${esc(f.size)}</span><span class="t-muted">${esc(f.type)}</span><span class="t-muted">${esc(f.hash)}</span></div>`).join("")}</div>
        <div class="foot-note mt10">Source: run.log fixture import · provenance manifest validated · single-run scope · imported 2026-10-12 09:12 ET</div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="eyebrow eyebrow--mb10">Typed step outputs · grading</div>
          <div class="list">${STEPS.map(s => `<div class="step"><span class="step__name">${esc(s.step)}</span><span class="step__contract">${esc(s.contract)}</span>${status(s.tone, s.icon, s.status)}</div>`).join("")}</div>
          <div class="small t-muted mt10"><span class="t-red">✕ failed grading</span> · compliance_review · schema drift: missing field <span class="t-bright">risk_tier</span> → attributed to contract boundary technical_analyst → compliance_review</div>
        </div>
        <div class="card">
          <div class="eyebrow eyebrow--mb10">Blocker results</div>
          <div class="list small">${BLOCKER_RESULTS.map(b => `<div class="kv-line"><span class="t-bright">${esc(b.name)}</span><span>${b.html}</span></div>`).join("")}</div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <div class="eyebrow eyebrow--mb10">Budget ledger · measured</div>
          <div class="kv-grid kv-grid--ledger">${LEDGER.map(l => `<div><div class="k">${esc(l.k)}</div><div class="big t-${l.tone}">${esc(l.v)}</div><div class="foot-note t-muted">${esc(l.note)}</div></div>`).join("")}</div>
        </div>
        <div class="card">
          <div class="eyebrow eyebrow--mb8">Quality · SLA</div>
          <div class="big">No observations</div>
          <div class="note">Zero samples in this scope. Nothing is implied about reliability.</div>
        </div>
        <div class="card card--col8">
          <div class="eyebrow">Replay · exports</div>
          <div class="row-wrap">
            <button type="button" class="btn btn--cyan btn--sm" data-action="go" data-section="fleet">Open replay in Fleet</button>
            ${ghostBtn("Safe export", "Classification policy not configured — safe export unavailable")}
          </div>
          <div class="foot-note">Safe export is unavailable until a classification policy governs redaction. Source fields render as text only.</div>
        </div>
      </div>
    </div>
  </section>`;
}

function renderApprovals() {
  return `<section class="section" aria-labelledby="h-approvals" data-screen-label="Approvals">
    <div class="section-head"><h1 id="h-approvals" class="h1">Approvals <span class="h1__meta">· ${APPROVALS.length} waiting · reviewer roster unconfigured</span></h1><div class="lede">Decisions record authenticated identity, manifest/evidence hashes, rationale, timestamp and expiry. Independent-review profiles reject self-approval.</div></div>
    <div class="grid grid--360">
      <div class="card">
        <div class="eyebrow eyebrow--mb10">Reviewer queue</div>
        <div class="list list--8">${APPROVALS.map(ap => `<div class="approval">
          <div class="approval__head"><span class="t-bright" style="font-weight:700">${ap.id} · ${esc(ap.title)}</span>${status("amber", "◐", "waiting")}</div>
          <div class="t-muted"><span class="t-steel">trigger</span> ${esc(ap.trigger)}</div>
          <div class="t-muted"><span class="t-steel">reviewer</span> ${esc(ap.reviewer)}</div>
          <div class="t-muted"><span class="t-steel">affected versions</span> ${esc(ap.versions)}</div>
          <div class="t-muted"><span class="t-steel">evidence</span> ${esc(ap.evidence)}</div>
          <div class="row-wrap mt4">${ghostBtn("Approve", ap.why, "btn--xs")}${ghostBtn("Reject", ap.why, "btn--xs")}${ghostBtn("Escalate", "Escalation path not configured", "btn--xs")}</div>
          <div class="t-amber foot-note">${esc(ap.why)}</div>
        </div>`).join("")}</div>
      </div>
      <div class="stack">${APPROVAL_EMPTIES.map(e => `<div class="card"><div class="eyebrow eyebrow--mb6">${esc(e.k)}</div><div class="big">${esc(e.v)}</div><div class="note">${esc(e.note)}</div></div>`).join("")}</div>
    </div>
  </section>`;
}

function renderSettings() {
  const groups = SETTINGS_GROUPS.map(g => `<div>
    <div class="group-title">${esc(g.title)}</div>
    <div class="grid grid--290">${g.items.map(c => {
      const [tone, icon] = CAPABILITY_STATES[c.state] || CAPABILITY_STATES.unavailable;
      const btn = c.disabled
        ? `<button type="button" class="btn btn--ghost btn--xs btn--40" disabled title="${esc(c.why)}">${esc(c.action)}</button>`
        : `<button type="button" class="btn btn--cyan btn--xs btn--40" data-action="fixture-action" data-label="${esc(c.action)}">${esc(c.action)}</button>`;
      return `<div class="cap">
        <div class="cap__head"><span class="cap__name">${esc(c.name)}</span>${status(tone, icon, c.state)}</div>
        <div class="t-muted">${esc(c.detail)}</div>
        ${c.blocker ? `<div class="t-amber">${esc(c.blocker)}</div>` : ""}
        <div class="cap__action">${btn}</div></div>`;
    }).join("")}</div></div>`).join("");

  return `<section class="section" aria-labelledby="h-settings" data-screen-label="Settings">
    <div class="section-head"><h1 id="h-settings" class="h1">Settings <span class="h1__meta">· capabilities by state</span></h1>
      <div class="legend"><span class="t-green">✓ implemented</span><span class="t-cyan">◆ verified</span><span class="t-blue">◇ proposed</span><span class="t-amber">◌ not configured</span><span class="t-steel">⊘ unavailable</span></div></div>
    ${groups}
  </section>`;
}

const RENDER = { launch: renderLaunch, missions: renderMissions, fleet: renderFleet, evidence: renderEvidence, approvals: renderApprovals, settings: renderSettings };

// ── render + routing ────────────────────────────────────────────────────

function renderNav() {
  $("nav").innerHTML = NAV.map(([id, label, glyph]) =>
    `<button type="button" class="nav__btn" data-action="go" data-section="${id}"${state.section === id ? ' aria-current="page"' : ""}><span class="nav__glyph" aria-hidden="true">${glyph}</span>${label}</button>`
  ).join("");
}

function render() {
  renderNav();
  $("main").innerHTML = RENDER[state.section]();
}

function go(section) {
  if (!SECTIONS.includes(section)) section = "launch";
  if (location.hash !== "#" + section) history.pushState(null, "", "#" + section);
  state.section = section;
  render();
  window.scrollTo(0, 0);
}

function sectionFromUrl() {
  const h = location.hash.replace(/^#/, "");
  if (SECTIONS.includes(h)) return h;
  const q = new URLSearchParams(location.search).get("section");
  return SECTIONS.includes(q) ? q : "launch";
}

let toastTimer;
function flash(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  // restart the entry animation on consecutive toasts
  t.style.animation = "none"; void t.offsetWidth; t.style.animation = "";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
}

function saveDraft() {
  const savedAt = new Date().toISOString();
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ draft: state.draft, savedAt }));
  } catch {
    flash("Draft not saved — browser storage is unavailable; edits stay in this tab only");
    return;
  }
  state.savedAt = savedAt;
  state.dirty = false;
  const label = $("saved-label");
  if (label) label.textContent = savedLabel();
  flash(`Draft saved locally · ${manifestInfo(state.draft).hash} · works offline`);
}

function loadDraft() {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return;
    const d = JSON.parse(raw);
    state.draft = { ...state.draft, ...(d.draft || {}) };
    state.savedAt = d.savedAt || null;
  } catch { /* ignore corrupt or blocked storage */ }
}

const NUMERIC = new Set(["calls", "minutes", "context"]);

function onDraftInput(e) {
  const el = e.target;
  if (!el.name || !(el.name in state.draft)) return;
  state.draft = { ...state.draft, [el.name]: NUMERIC.has(el.name) ? Number(el.value) : el.value };
  $("plan-card").innerHTML = renderPlanCard();
  $("reviewer-req").textContent = reviewerReq(state.draft);
  if (!state.dirty) { state.dirty = true; $("saved-label").textContent = savedLabel(); }
}

const ACTIONS = {
  "go": el => go(el.dataset.section),
  "open-mission": el => { state.selMission = el.dataset.id; go("missions"); },
  "toggle-mission": el => {
    state.selMission = state.selMission === el.dataset.id ? null : el.dataset.id;
    render();
    document.querySelector(`[data-action="toggle-mission"][data-id="${el.dataset.id}"]`)?.focus();
  },
  "toggle-plan": () => {
    state.showPlan = !state.showPlan;
    $("plan-card").innerHTML = renderPlanCard();
    $("plan-card").querySelector('[data-action="toggle-plan"]').focus();
  },
  "save-draft": saveDraft,
  "fleet-mode": el => { state.fleetMode = el.dataset.mode; render(); },
  "fixture-action": el => flash(`${el.dataset.label} · fixture view — not wired in demo mode`),
};

function init() {
  loadDraft();
  state.section = sectionFromUrl();

  const root = $("lp");
  root.addEventListener("click", e => {
    const el = e.target.closest("[data-action]");
    if (!el || el.disabled) return;
    ACTIONS[el.dataset.action]?.(el);
  });
  root.addEventListener("input", onDraftInput);
  window.addEventListener("popstate", () => { state.section = sectionFromUrl(); render(); });

  // Narrow layouts force the accessible table; widening restores the topology.
  // A media query measures the viewport including any scrollbar, so the taller topology view
  // gaining a scrollbar cannot flip the mode back (as an element-width observer could).
  const narrowQuery = window.matchMedia(`(max-width: ${NARROW_PX - 0.02}px)`);
  const applyWidth = () => {
    const narrow = narrowQuery.matches;
    if (narrow === state.narrow) return false;
    state.narrow = narrow;
    state.fleetMode = narrow ? "table" : "topology";
    return true;
  };
  applyWidth();
  render();
  narrowQuery.addEventListener("change", () => { if (applyWidth() && state.section === "fleet") render(); });
}

init();
