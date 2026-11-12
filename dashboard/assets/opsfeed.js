/* Feeds the embedded Ops Console from gateway observations.
   The console page is a sandboxed frame with no network access and no
   commands; this adapter is its only input. Every envelope is derived from
   the agents and steps the Launch Pad already holds: one station per observed
   step, one agent per observed step owner. Nothing is invented: progress,
   ETA, queue and token use are unknown and sent as such, and the console's
   energy readout is fixed at full and hidden. Text that reaches the console's
   HTML is escaped here, and identifiers are reduced to a safe alphabet. */
(function (root) {
  const CHANNEL = "roboport-ops-console";
  const PALETTE = ["#7ea6ff", "#36c6e0", "#c98bff", "#f2b134", "#4fd672", "#ff8f6b", "#ff5a8a", "#80d8c8"];
  const TRAVEL_S = 1.2;
  const safeId = (s) => String(s ?? "unknown").replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, 80) || "unknown";
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const iso = () => new Date().toISOString();
  // The gateway keeps agents in insertion order, oldest first. Show running
  // agents, then the most recent others, up to the limit, keeping their
  // original order. The Fleet roster uses the same selection.
  const visibleAgents = (agents, limit) => {
    const all = agents || [];
    if (all.length <= limit) return all.slice();
    const keep = new Set(all.filter((a) => a.state === "running").slice(-limit));
    for (let i = all.length - 1; i >= 0 && keep.size < limit; i--) keep.add(all[i]);
    return all.filter((a) => keep.has(a));
  };

  function model(data) {
    const observed = visibleAgents(data.agents, 100);
    const steps = data.steps || [];
    // Wave = the plan wave the gateway observed on step.start. Steps from
    // older records without one fall back to their position within the
    // mission, in observation order. No dependency edge is inferred.
    const stationIds = [];
    const wave = new Map();
    const perMission = new Map();
    const planned = new Set();
    const planWave = (a) => {
      const step = steps.find((s) => s.id === a.mission_id + ":" + a.step_id);
      const w = step && step.wave;
      return Number.isInteger(w) && w >= 0 ? w : null;
    };
    for (const a of observed) {
      const sid = safeId(a.step_id);
      const seen = perMission.get(a.mission_id) || [];
      if (!seen.includes(sid)) seen.push(sid);
      perMission.set(a.mission_id, seen);
      if (!stationIds.includes(sid)) stationIds.push(sid);
      // The first observed plan wave wins over any order-based fallback.
      const w = planWave(a);
      if (w !== null && !planned.has(sid)) {
        planned.add(sid);
        wave.set(sid, Math.min(4, w));
      } else if (!wave.has(sid)) wave.set(sid, Math.min(4, seen.indexOf(sid)));
    }
    const stations = stationIds.map((sid, i) => ({
      station_id: sid,
      name: sid,
      label: sid.replace(/_/g, " "),
      order: i,
      wave: wave.get(sid),
      hue: PALETTE[i % PALETTE.length],
      det: false,
      model: null,
      sources: [],
    }));
    const agents = observed.map((a) => {
      const step = steps.find((s) => s.id === a.mission_id + ":" + a.step_id) || {};
      // The same agent runs in many missions; the console needs one ID per
      // mission, or two missions would overwrite each other's state.
      return {
        agent_id: safeId(a.agent_id + ":" + String(a.mission_id).slice(0, 8)),
        station_id: safeId(a.step_id),
        task_id: safeId(a.mission_id + ":" + a.step_id),
        observed: a.state,
        started_at: step.started_at || null,
        finished_at: step.finished_at || null,
        error: step.error ? (typeof step.error === "string" ? step.error : JSON.stringify(step.error)) : null,
      };
    });
    return { stations, agents };
  }

  function Feed(frame) {
    this.frame = frame;
    this.seq = 0;
    this.rev = 0;
    this.signature = null;
    this.agents = new Map(); // agent_id -> { obs, state, timer }
    this.raised = new Map(); // agent_id -> when its failure was first seen
    this.data = {};
    this.ready = false;
    this.onMessage = (ev) => {
      if (ev.source !== frame.contentWindow) return;
      const m = ev.data;
      if (!m || m.channel !== CHANNEL || m.type !== "resync") return;
      this.ready = true;
      this.snapshot();
    };
    root.addEventListener("message", this.onMessage);
  }
  Feed.prototype.post = function (envelopes) {
    if (!this.ready || !this.frame.contentWindow || !envelopes.length) return;
    // The frame is sandboxed (opaque origin), so "*" is the only target that
    // reaches it; the message carries observations the page already displays.
    this.frame.contentWindow.postMessage({ channel: CHANNEL, envelopes }, "*");
  };
  Feed.prototype.env = function (type, data) {
    return { v: 1, seq: ++this.seq, ts: iso(), type, data };
  };
  Feed.prototype.agentDTO = function (a, state) {
    const working = state === "dispatched" || state === "working";
    return {
      agent_id: a.agent_id,
      name: a.agent_id,
      state,
      energy: 100,
      hold: false,
      task_id: working ? a.task_id : null,
      station_id: working || state === "returning" ? a.station_id : null,
      task_progress: 0,
      eta_s: state === "dispatched" || state === "returning" ? TRAVEL_S : null,
      completed_total: a.observed === "completed" ? 1 : 0,
      error: a.error == null ? null : esc(a.error),  // the console renders it as HTML
      rev: ++this.rev,
      specialty: a.station_id,
      tokens_used: 0,
      token_budget: 60000,
      updated_at: iso(),
    };
  };
  Feed.prototype.taskDTO = function (a, status) {
    return {
      task_id: a.task_id,
      station_id: a.station_id,
      status,
      priority: 100,
      work_estimate_s: null,
      assigned_agent_id: a.agent_id,
      enqueued_at: a.started_at,
      started_at: a.started_at,
      finished_at: a.finished_at,
      error: a.error == null ? null : esc(a.error),  // the console renders it as HTML
      rev: ++this.rev,
    };
  };
  // One incident per observed failed step. Snapshots resend them, so a
  // reloaded or resynced console keeps its incident log.
  Feed.prototype.alertDTO = function (a) {
    if (!this.raised.has(a.agent_id)) this.raised.set(a.agent_id, a.finished_at || iso());
    return {
      alert_id: "al_" + a.agent_id,
      kind: "critical",
      title: esc(a.agent_id + " failed"),
      body: esc(a.error || "Step failed at " + a.station_id),
      target: { type: "station", id: a.station_id },
      raised_at: this.raised.get(a.agent_id),
      ttl_s: 7,
    };
  };
  Feed.prototype.stationDTOs = function (m) {
    return m.stations.map((s) => {
      const worker = m.agents.find((a) => a.station_id === s.station_id && a.observed === "running");
      return {
        station_id: s.station_id,
        name: s.name,
        order: s.order,
        state: worker ? "busy" : "idle",
        worker_agent_id: worker ? worker.agent_id : null,
        queue_depth: 0,
        drain: false,
        rev: ++this.rev,
      };
    });
  };
  Feed.prototype.metrics = function (m) {
    const done = m.agents.filter((a) => a.observed === "completed").length;
    const failed = m.agents.filter((a) => a.observed === "failed").length;
    return {
      tasks_per_min: 0,
      completed_total: done,
      failed_total: failed,
      success_rate: done + failed ? done / (done + failed) : 1,
      p95_ms: 0,
      active_agents: m.agents.filter((a) => a.observed === "running").length,
      total_agents: m.agents.length,
      queued: 0,
      uptime_s: 0,
      ts: iso(),
    };
  };
  Feed.prototype.snapshot = function () {
    const m = model(this.data);
    for (const s of this.agents.values()) clearTimeout(s.timer);
    this.agents.clear();
    this.signature = m.stations.map((s) => s.station_id).join("|");
    const agents = m.agents.map((a) => {
      const state = a.observed === "running" ? "working" : "docked";
      this.agents.set(a.agent_id, { obs: a.observed, state, timer: null });
      return this.agentDTO(a, state);
    });
    const tasks = m.agents.filter((a) => a.observed === "running").map((a) => this.taskDTO(a, "running"));
    const snap = {
      config: { stations: m.stations, energy_low_threshold: 0, max_agents: 0, token_budget_default: 60000 },
      agents,
      stations: this.stationDTOs(m),
      tasks,
      alerts: m.agents.filter((a) => a.observed === "failed").slice(-40).map((a) => this.alertDTO(a)),
      metrics: this.metrics(m),
    };
    this.post([
      this.env("snapshot", snap),
      this.env("log.appended", {
        html: "Gateway observations · " + esc(m.agents.length) + " agents · " + esc(m.stations.length) + " steps",
        ts: iso(),
      }),
    ]);
  };
  Feed.prototype.later = function (agentId, fn) {
    const s = this.agents.get(agentId);
    if (!s) return;
    clearTimeout(s.timer);
    s.timer = setTimeout(() => {
      s.timer = null;
      const out = fn(s);
      if (out && out.length) this.post(out);
    }, TRAVEL_S * 1000 + 100);
  };
  Feed.prototype.update = function (data) {
    this.data = data || {};
    if (!this.ready) return;
    const m = model(this.data);
    if (m.stations.map((s) => s.station_id).join("|") !== this.signature) return this.snapshot();
    // An agent left the visible set: rebuild so its drone, task and timer go.
    const visible = new Set(m.agents.map((a) => a.agent_id));
    for (const id of this.agents.keys()) if (!visible.has(id)) return this.snapshot();
    const out = [];
    for (const a of m.agents) {
      const prev = this.agents.get(a.agent_id);
      if (!prev) {
        this.agents.set(a.agent_id, { obs: a.observed, state: "docked", timer: null });
        out.push(this.env("agent.added", this.agentDTO(a, "docked")));
      }
      const s = this.agents.get(a.agent_id);
      if (s.obs === a.observed && prev) continue;
      s.obs = a.observed;
      if (a.observed === "running") {
        s.state = "dispatched";
        out.push(this.env("task.assigned", this.taskDTO(a, "assigned")));
        out.push(this.env("agent.updated", this.agentDTO(a, "dispatched")));
        out.push(this.env("log.appended", { html: "<b>" + esc(a.agent_id) + "</b> → " + esc(a.station_id), ts: iso() }));
        this.later(a.agent_id, (st) => {
          if (st.obs !== "running") return [];
          st.state = "working";
          return [this.env("agent.updated", this.agentDTO(a, "working")), this.env("task.progress", this.taskDTO(a, "running"))];
        });
      } else if (a.observed === "completed" || a.observed === "failed") {
        const ok = a.observed === "completed";
        out.push(this.env(ok ? "task.completed" : "task.failed", this.taskDTO(a, ok ? "completed" : "failed")));
        if (!ok) out.push(this.env("alert.raised", this.alertDTO(a)));
        if (s.state === "dispatched" || s.state === "working") {
          s.state = "returning";
          out.push(this.env("agent.updated", this.agentDTO(a, "returning")));
          this.later(a.agent_id, (st) => {
            st.state = "docked";
            return [this.env("agent.updated", this.agentDTO(a, "docked"))];
          });
        }
        out.push(
          this.env("log.appended", {
            html: "<b>" + esc(a.agent_id) + "</b> " + (ok ? "completed " : "failed ") + esc(a.station_id),
            ts: iso(),
          }),
        );
      } else if (s.state === "dispatched" || s.state === "working") {
        // Cancelled or unknown: the work stopped without completing. The
        // console drops a cancelled task without counting success or failure.
        s.state = "docked";
        out.push(this.env("task.cancelled", this.taskDTO(a, "cancelled")));
        out.push(this.env("agent.updated", this.agentDTO(a, "docked")));
        out.push(this.env("log.appended", { html: "<b>" + esc(a.agent_id) + "</b> " + esc(a.observed) + " " + esc(a.station_id), ts: iso() }));
      }
    }
    if (out.length) {
      for (const s of this.stationDTOs(m)) out.push(this.env("station.updated", s));
      out.push(this.env("metrics.tick", this.metrics(m)));
    }
    this.post(out);
  };
  Feed.prototype.close = function () {
    root.removeEventListener("message", this.onMessage);
    for (const s of this.agents.values()) clearTimeout(s.timer);
  };

  root.RoboportOpsFeed = { Feed, model, safeId, esc, visibleAgents, CHANNEL };
})(window);
