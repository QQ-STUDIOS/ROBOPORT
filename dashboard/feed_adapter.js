/**
 * ROBOPORT Ops Console — Live Feed Adapter  v1.0
 * ================================================
 * Bridges real ROBOPORT run_log.jsonl output to the console's
 * internal event-envelope format.
 *
 * TWO MODES
 * ---------
 * 1. SSE live mode  — open the console with ?api=http://localhost:4242
 *    Requires bridge.py to be running against the runs/ directory.
 *
 * 2. File-drop mode — drag any run_log.jsonl onto the canvas.
 *    No server needed; replays the run at configurable speed.
 *
 * USAGE
 * -----
 * Load this script before the console closes its <script> block:
 *
 *   <script src="dashboard/feed_adapter.js"></script>
 *
 * The adapter registers window.ROBOPORT_ADAPTER. In the console's main
 * script, replace:
 *
 *   const backend = createMockBackend();
 *
 * with:
 *
 *   const backend = createLiveBackend() || createMockBackend();
 *
 * where createLiveBackend() returns window.ROBOPORT_ADAPTER when the
 * ?api= param is present, or null otherwise.
 */

(function (global) {
  "use strict";

  // ── Station config (mirrors SC[] in the console) ───────────────────────
  const STATIONS = [
    { station_id: "job_scout",             wave: 0, order: 0, hue: "#7ea6ff", contract_out: "list[Job]",            det: false },
    { station_id: "technical_analyst",     wave: 1, order: 1, hue: "#36c6e0", contract_out: "TechnicalAnalysis",    det: false },
    { station_id: "compliance_risk",       wave: 1, order: 2, hue: "#c98bff", contract_out: "ComplianceAnalysis",   det: false },
    { station_id: "application_strategist",wave: 2, order: 3, hue: "#f2b134", contract_out: "CandidateMatch",       det: false },
    { station_id: "synthesizer",           wave: 3, order: 4, hue: "#4fd672", contract_out: "FinalReport",          det: true  },
    { station_id: "salary_estimator",      wave: 4, order: 5, hue: "#ff8f6b", contract_out: "SalaryBand",           det: false, optional: true },
    { station_id: "resume_tailor",         wave: 4, order: 6, hue: "#ff5a8a", contract_out: "TailoredResume",       det: false, optional: true },
    { station_id: "cover_letter_writer",   wave: 4, order: 7, hue: "#80d8c8", contract_out: "CoverLetter",          det: false, optional: true },
  ];

  // ROBOPORT crew edges (from registry.json)
  const EDGES = [
    { from: "job_scout",              to: "technical_analyst",       type: "list[Job]" },
    { from: "job_scout",              to: "compliance_risk",         type: "list[Job]" },
    { from: "technical_analyst",      to: "application_strategist",  type: "TechnicalAnalysis" },
    { from: "compliance_risk",        to: "application_strategist",  type: "ComplianceAnalysis" },
    { from: "application_strategist", to: "synthesizer",             type: "CandidateMatch" },
    { from: "synthesizer",            to: "salary_estimator",        type: "FinalReport", optional: true },
    { from: "synthesizer",            to: "resume_tailor",           type: "FinalReport", optional: true },
    { from: "synthesizer",            to: "cover_letter_writer",     type: "FinalReport", optional: true },
  ];

  // ── Envelope builder ───────────────────────────────────────────────────
  let _seq = 0;
  function envelope(type, data) {
    return { v: 1, seq: ++_seq, ts: new Date().toISOString(), type, data };
  }

  // ── Snapshot builder — seeds the console from the JD-Crew config ───────
  function makeSnapshot(runId, agentNames) {
    const agents = (agentNames || ["executor-01", "executor-02", "executor-03", "executor-04"]).map((id, i) => ({
      agent_id: id, name: id, state: "docked", energy: 85 + Math.random() * 10,
      hold: false, task_id: null, station_id: null, task_progress: 0,
      eta_s: null, completed_total: 0, error: null, rev: 0,
      specialty: STATIONS[i % STATIONS.length].station_id,
      tokens_used: 0, token_budget: 2048,
      updated_at: new Date().toISOString(),
    }));
    return {
      config: {
        stations: STATIONS,
        crew_edges: EDGES,
        energy_low_threshold: 22,
        max_agents: 12,
        token_budget_default: 2048,
        run_id: runId || null,
      },
      agents,
      stations: STATIONS.map(s => ({
        station_id: s.station_id, name: s.station_id, order: s.order,
        state: "idle", worker_agent_id: null, queue_depth: 0, drain: false, rev: 0,
      })),
      tasks: [],
      alerts: [],
      metrics: {
        tasks_per_min: 0, completed_total: 0, failed_total: 0,
        success_rate: 1, p95_ms: 0, active_agents: 0,
        total_agents: agents.length, queued: 0, uptime_s: 0,
        ts: new Date().toISOString(),
      },
    };
  }

  // ── ROBOPORT run_log.jsonl event → console envelopes ──────────────────
  // Maps one run_log event to zero-or-more console envelopes.
  function translate(ev, state) {
    const out = [];
    const log = html => out.push(envelope("log.appended", { html, ts: ev.ts || new Date().toISOString() }));

    switch (ev.event) {
      case "run.start": {
        const agentNames = (ev.agents || []).map(a => a.id || a.agent_id).filter(Boolean);
        state.snap = makeSnapshot(ev.run_id, agentNames.length ? agentNames : null);
        out.push(envelope("snapshot", state.snap));
        log(`<b style="color:#4fd672">RUN STARTED</b> · ${ev.run_id || "?"} · ${ev.crew || "jd_crew"}`);
        break;
      }

      case "plan.created": {
        if (!state.snap) { state.snap = makeSnapshot(ev.run_id); out.push(envelope("snapshot", state.snap)); }
        log(`<b style="color:#6c8cff">PLAN CREATED</b> · ${(ev.plan?.waves || []).length} waves · ${(ev.plan?.steps || []).length} steps`);
        break;
      }

      case "step.start": {
        const sid = ev.agent || ev.step_id || ev.owner;
        const aid = state.agentFor(sid);
        const taskId = "t_" + (ev.step_id || sid) + "_" + (_seq % 9999);
        state.taskOf[sid] = taskId;
        out.push(envelope("task.enqueued", {
          task_id: taskId, station_id: sid, status: "queued",
          priority: 100, work_estimate_s: 5,
          assigned_agent_id: null, enqueued_at: ev.ts, rev: 0,
        }));
        out.push(envelope("task.assigned", {
          task_id: taskId, station_id: sid, status: "assigned",
          priority: 100, work_estimate_s: 5,
          assigned_agent_id: aid, enqueued_at: ev.ts, started_at: ev.ts, rev: 1,
        }));
        out.push(envelope("agent.updated", {
          agent_id: aid, name: aid, state: "dispatched",
          energy: state.energyOf(aid), hold: false, task_id: taskId,
          station_id: sid, task_progress: 0, eta_s: 2.0,
          completed_total: state.completedOf(aid), rev: state.revOf(aid),
          specialty: sid, tokens_used: state.tokensOf(aid), token_budget: 2048,
          updated_at: ev.ts,
        }));
        out.push(envelope("station.updated", {
          station_id: sid, name: sid, state: "busy",
          worker_agent_id: aid, queue_depth: 0, drain: false, rev: state.stnRev(sid),
        }));
        log(`<b style="color:#36c6e0">${aid}</b> → <b style="color:${stHue(sid)}">${sid}</b> · ${taskId}`);
        break;
      }

      case "tool.call": {
        const sid = ev.step_id || ev.agent;
        log(`<b style="color:#5f717c">tool</b> <span style="color:#8a9da8">${ev.tool || "?"}</span>${sid ? ` · ${sid}` : ""}`);
        break;
      }

      case "step.complete": {
        const sid = ev.agent || ev.step_id || ev.owner;
        const aid = state.agentFor(sid);
        const taskId = state.taskOf[sid] || "t_done";
        const lat = ev.duration_ms || 3000;
        state.addCompleted(aid, { station: sid, task_id: taskId, ok: true, lat_ms: lat });
        state.addTokens(aid, Math.floor(1000 + Math.random() * 3000));
        const st = STATIONS.find(s => s.station_id === sid);
        out.push(envelope("task.completed", {
          task_id: taskId, station_id: sid, status: "completed",
          assigned_agent_id: aid, enqueued_at: ev.ts, started_at: ev.ts,
          finished_at: ev.ts, result: { ok: true, contract: st ? st.contract_out : null }, rev: 2,
        }));
        out.push(envelope("agent.updated", {
          agent_id: aid, name: aid, state: "returning",
          energy: state.energyOf(aid) - 8, hold: false, task_id: null,
          station_id: null, task_progress: 1, eta_s: 1.0,
          completed_total: state.completedOf(aid),
          rev: state.revOf(aid), specialty: sid,
          tokens_used: state.tokensOf(aid), token_budget: 2048,
          updated_at: ev.ts,
        }));
        out.push(envelope("station.updated", {
          station_id: sid, state: "idle", worker_agent_id: null,
          queue_depth: 0, drain: false, rev: state.stnRev(sid),
        }));
        out.push(envelope("metrics.tick", {
          tasks_per_min: ++state.completedTotal,
          completed_total: state.completedTotal,
          failed_total: state.failedTotal,
          success_rate: state.completedTotal / (state.completedTotal + state.failedTotal || 1),
          p95_ms: lat, active_agents: 1, total_agents: 4, queued: 0,
          uptime_s: Math.round((Date.now() - state.startMs) / 1000),
          ts: ev.ts,
        }));
        const llm = ev.llm_calls != null ? ` · ${ev.llm_calls} LLM` : "";
        const tools = ev.tool_calls != null ? ` · ${ev.tool_calls} tools` : "";
        const ms = ev.duration_ms != null ? ` · ${ev.duration_ms}ms` : "";
        log(`<b style="color:#4fd672">${aid}</b> ✓ <b style="color:${stHue(sid)}">${sid}</b>${llm}${tools}${ms}`);
        break;
      }

      case "step.failed": {
        const sid = ev.agent || ev.step_id || ev.owner;
        const aid = state.agentFor(sid);
        const taskId = state.taskOf[sid] || "t_fail";
        const layer = ev.layer || "criterion_failed";
        const kind = layer === "budget_exceeded" || layer === "unsafe" ? "critical" : "warning";
        state.failedTotal++;
        out.push(envelope("task.failed", {
          task_id: taskId, station_id: sid, status: "failed",
          assigned_agent_id: aid, error: ev.error || layer, rev: 2,
        }));
        out.push(envelope("alert.raised", {
          alert_id: "al_" + Math.random().toString(36).slice(2, 7),
          kind, title: `${sid} step failed`,
          body: (ev.error || layer) + ` [layer ${layer}]`,
          target: { type: "station", id: sid },
          raised_at: ev.ts, ttl_s: 8,
        }));
        out.push(envelope("station.updated", {
          station_id: sid, state: "idle", worker_agent_id: null,
          queue_depth: 0, drain: false, rev: state.stnRev(sid),
        }));
        log(`<b style="color:#ff5a52">${aid}</b> ✗ <b style="color:${stHue(sid)}">${sid}</b> · ${ev.error || layer}`);
        break;
      }

      case "retry": {
        const sid = ev.step_id || ev.agent;
        const aid = state.agentFor(sid);
        out.push(envelope("alert.raised", {
          alert_id: "al_" + Math.random().toString(36).slice(2, 7),
          kind: "warning", title: `${sid} retry #${ev.attempt || 1}`,
          body: ev.reason || "step retry", target: { type: "station", id: sid },
          raised_at: ev.ts, ttl_s: 6,
        }));
        log(`<b style="color:#f2b134">retry</b> ${sid} attempt ${ev.attempt || "?"} · ${ev.reason || ""}`);
        break;
      }

      case "critic.review": {
        const sid = ev.step_id;
        const verdict = ev.verdict || "?";
        const col = verdict === "pass" ? "#4fd672" : verdict === "fix" ? "#f2b134" : "#ff5a52";
        log(`<b style="color:#c98bff">critic</b> ${sid} → <b style="color:${col}">${verdict}</b>${ev.suggested_repair ? " · " + ev.suggested_repair.slice(0, 60) : ""}`);
        break;
      }

      case "run.complete": {
        const s = ev.run_summary || {};
        out.push(envelope("metrics.tick", {
          tasks_per_min: state.completedTotal,
          completed_total: state.completedTotal,
          failed_total: state.failedTotal,
          success_rate: state.completedTotal / (state.completedTotal + state.failedTotal || 1),
          p95_ms: s.p95_ms || 0,
          active_agents: 0, total_agents: 4, queued: 0,
          uptime_s: Math.round((Date.now() - state.startMs) / 1000),
          ts: ev.ts,
        }));
        log(`<b style="color:#4fd672">RUN COMPLETE</b> · ${s.steps || "?"} steps · ${s.llm_calls || "?"} LLM · ${s.wall_ms != null ? s.wall_ms + "ms" : ""}`);
        break;
      }

      default:
        if (ev.event) log(`<span style="color:#5f717c">${ev.event}</span>${ev.step_id ? " · " + ev.step_id : ""}`);
    }
    return out;
  }

  function stHue(sid) {
    const s = STATIONS.find(x => x.station_id === sid);
    return s ? s.hue : "#8a9da8";
  }

  // ── Mutable run state ─────────────────────────────────────────────────
  function makeState() {
    const agentN = {};
    const energy = {};
    const tokens = {};
    const completed = {};
    const revA = {};
    const revS = {};
    const taskOf = {};
    let completedTotal = 0, failedTotal = 0;
    const startMs = Date.now();
    let snap = null;
    let aidN = 0;

    return {
      snap, taskOf, completedTotal, failedTotal, startMs,
      agentFor(sid) {
        if (!agentN[sid]) agentN[sid] = "executor-" + String(++aidN).padStart(2, "0");
        return agentN[sid];
      },
      energyOf(aid) { return energy[aid] != null ? energy[aid] : 85; },
      tokensOf(aid) { return tokens[aid] || 0; },
      addTokens(aid, n) { tokens[aid] = (tokens[aid] || 0) + n; },
      completedOf(aid) { return completed[aid] || 0; },
      addCompleted(aid, rec) { completed[aid] = (completed[aid] || 0) + 1; completedTotal++; },
      revOf(aid) { revA[aid] = (revA[aid] || 0) + 1; return revA[aid]; },
      stnRev(sid) { revS[sid] = (revS[sid] || 0) + 1; return revS[sid]; },
      get completedTotal() { return completedTotal; },
      set completedTotal(v) { completedTotal = v; },
      get failedTotal() { return failedTotal; },
      set failedTotal(v) { failedTotal = v; },
      get snap() { return snap; },
      set snap(v) { snap = v; },
    };
  }

  // ── JSONL parser ──────────────────────────────────────────────────────
  function parseJSONL(text) {
    return text.split("\n").filter(l => l.trim()).map(l => {
      try { return JSON.parse(l); } catch { return null; }
    }).filter(Boolean);
  }

  // ── File-drop adapter ─────────────────────────────────────────────────
  function createFileDropBackend(file, onEnvelope, speed) {
    speed = speed || 4;
    const state = makeState();
    const reader = new FileReader();
    reader.onload = function (e) {
      const lines = parseJSONL(e.target.result);
      let idx = 0;
      // Emit snapshot immediately
      const snap = makeSnapshot("file-drop");
      onEnvelope(envelope("snapshot", snap));
      state.snap = snap;

      function next() {
        if (idx >= lines.length) return;
        const evts = translate(lines[idx++], state);
        evts.forEach(onEnvelope);
        const delay = idx < lines.length ? Math.max(50, 200 / speed) : 0;
        if (idx < lines.length) setTimeout(next, delay);
      }
      setTimeout(next, 400);
    };
    reader.readAsText(file);
  }

  // ── SSE live adapter ──────────────────────────────────────────────────
  function createSSEBackend(apiUrl, onEnvelope) {
    const state = makeState();
    let connected = false;
    const sse = new EventSource(apiUrl + "/events");

    sse.onopen = function () {
      connected = true;
      console.log("[ROBOPORT adapter] SSE connected to", apiUrl);
    };

    sse.onmessage = function (e) {
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      // If the server already sends console envelopes, pass through directly
      if (ev.v === 1 && ev.type) { onEnvelope(ev); return; }
      // Otherwise translate from run_log format
      translate(ev, state).forEach(onEnvelope);
    };

    sse.onerror = function () {
      if (connected) {
        onEnvelope(envelope("alert.raised", {
          alert_id: "al_sse_err", kind: "critical",
          title: "SSE connection lost",
          body: "Bridge server disconnected. Check bridge.py.",
          target: { type: "port" }, raised_at: new Date().toISOString(), ttl_s: 10,
        }));
      }
    };

    return { close() { sse.close(); } };
  }

  // ── Public adapter backend (implements the console's backend contract) ─
  function createLiveBackend(opts) {
    opts = opts || {};
    const subs = [];
    let lastSeq = 0;

    function push(env) {
      lastSeq = env.seq;
      subs.forEach(cb => cb(env));
    }

    const apiUrl = opts.apiUrl || (new URLSearchParams(location.search).get("api") || "").replace(/\/$/, "");

    if (apiUrl) {
      createSSEBackend(apiUrl, push);
    }

    // File-drop wiring
    document.addEventListener("dragover", e => e.preventDefault());
    document.addEventListener("drop", function (e) {
      e.preventDefault();
      const file = Array.from(e.dataTransfer.files).find(f => f.name.endsWith(".jsonl") || f.name.endsWith(".log"));
      if (file) createFileDropBackend(file, push, opts.speed || 4);
    });

    return {
      subscribe(cb) { subs.push(cb); return () => { const i = subs.indexOf(cb); if (i >= 0) subs.splice(i, 1); }; },
      getSnapshot() { push(envelope("snapshot", makeSnapshot("live"))); },
      handleCommand(cmd) { return { command_id: cmd.command_id, status: "accepted", note: "live mode — commands are display-only" }; },
      tick() { /* driven by SSE / file replay */ },
      CONFIG: { stations: STATIONS, energy_low_threshold: 22, max_agents: 12, token_budget_default: 2048 },
      stationConfig: new Map(),
      get seq() { return lastSeq; },
    };
  }

  global.ROBOPORT_ADAPTER = createLiveBackend;
  global.ROBOPORT_translate = translate;
  global.ROBOPORT_parseJSONL = parseJSONL;
  global.ROBOPORT_makeSnapshot = makeSnapshot;

})(window);
