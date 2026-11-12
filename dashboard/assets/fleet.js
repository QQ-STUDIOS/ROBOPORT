/* Presentation uses observed agent/step relationships; it owns no commands.
   Layout follows the Claude Design Fleet section: a Topology / Table toggle,
   an explanatory note, and either the embedded Ops Console or the roster.
   Topology is the design's own Ops Console (Port / Flow / DAG), served from
   /opsconsole in a sandboxed frame and fed only by RoboportOpsFeed, which
   derives every envelope from the gateway observations held here. */
(function (root) {
  const STEP_STATES = {
    running: ["amber", "⚙", "working"],
    completed: ["green", "✓", "completed"],
    failed: ["red", "✕", "failed"],
  };
  function render(main, data, helpers, opts) {
    const { node, button, card, glyph, toned } = helpers;
    const all = data.agents || [],
      agents = all.slice(0, 100),
      steps = data.steps || [];
    const stepOf = (agent) =>
      steps.find((s) => s.id === agent.mission_id + ":" + agent.step_id);
    const narrow = Boolean(opts.narrow);
    const modeName = narrow ? "table" : opts.mode;
    const head = node("div", undefined, "section-head");
    const h1 = node("h1", "Fleet");
    h1.id = "h-fleet";
    const meta = node(
      "span",
      ` · ${all.length} observed agents · ${steps.length} steps · ${opts.source}`,
      "meta",
    );
    meta.dataset.source = opts.source;
    h1.append(meta);
    head.append(h1);
    const group = node("div", undefined, "fleet-toggle");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", "Fleet view");
    const topology = button("", () => opts.setMode("topology"));
    topology.append(glyph("⬡"), node("span", "Topology"));
    topology.setAttribute("aria-pressed", String(modeName === "topology"));
    topology.disabled = narrow;
    topology.title = narrow
      ? "Topology is desktop-only; narrow screens use the roster"
      : "Port / Flow / DAG views of observed ownership";
    const table = button("", () => opts.setMode("table"));
    table.append(glyph("▤"), node("span", "Table"));
    table.setAttribute("aria-pressed", String(modeName === "table"));
    table.title = "Accessible roster of the same observations";
    group.append(topology, table);
    head.append(group);
    main.append(head);
    main.append(
      node(
        "p",
        "Topology embeds the Roboport Ops Console (Port / Flow / DAG), fed only by the observed agent-to-step ownership on this page. The roster is the accessible equivalent of the same observations. Progress, ETA, queue, energy and token use are not measured in the runtime stream, so the console shows them as zero or hides them, and agent creation requires registration and evaluation gates outside this console.",
        "section-note",
      ),
    );
    const inspector = card("Agent inspector");
    inspector.append(
      node(
        "p",
        "Select an observed agent in the topology or roster to inspect its typed step state.",
      ),
    );
    function inspect(agent) {
      const step = stepOf(agent);
      inspector.replaceChildren(
        node("h2", "Agent inspector"),
        node(
          "p",
          "Observed step state is separate from mission grading and approval.",
        ),
        node(
          "pre",
          JSON.stringify(
            {
              agent_id: agent.agent_id,
              mission_id: agent.mission_id,
              step_id: agent.step_id,
              state: agent.state,
              calls: step?.llm_calls ?? null,
              tools: step?.tool_calls ?? null,
              api_cost_usd: step?.cost_usd ?? null,
              host_cost_usd: null,
              source: step?.source ?? "Imported or runtime observation",
            },
            null,
            2,
          ),
        ),
      );
      inspector.scrollIntoView({ block: "nearest" });
    }
    if (modeName === "topology") {
      const wrap = node("div", undefined, "fleet-canvas fleet-console");
      wrap.append(consoleFrame(data));
      main.append(wrap);
      main.append(
        node(
          "p",
          "Agents, stations and tasks in the console come only from the gateway observations above; commands are disabled there. Remote ports, clusters and infrastructure nodes are the design's illustrative scenery and are marked simulated in its inspector.",
          "source-note",
        ),
      );
      return;
    } else {
      const roster = node("div", undefined, "roster");
      roster.append(
        node(
          "div",
          `Roster · ${opts.source} · ${agents.length} agent${agents.length === 1 ? "" : "s"}`,
          "roster-head",
        ),
      );
      const cell = (k, value, cls = "v") => {
        const c = node("span", undefined, "cell");
        c.append(node("span", k, "k"));
        if (value instanceof Node) c.append(value);
        else c.append(node("span", value, cls));
        return c;
      };
      for (const agent of agents) {
        const step = stepOf(agent);
        const [tone, icon, label] = STEP_STATES[agent.state] || [
          "steel",
          "?",
          agent.state,
        ];
        const row = node("div", undefined, "roster-row");
        row.append(node("div", agent.agent_id, "roster-id"));
        const cells = node("div", undefined, "roster-cells");
        const stateEl = toned("span", undefined, tone, "state");
        stateEl.append(glyph(icon), node("span", label));
        cells.append(
          cell("state", stateEl),
          cell("station · step", agent.step_id),
          cell("mission", agent.mission_id.slice(0, 8), "m"),
          cell("model calls", step?.llm_calls ?? "Unknown", "m"),
          cell("tool calls", step?.tool_calls ?? "Unknown", "m"),
          cell(
            "api cost",
            step?.cost_usd === undefined || step?.cost_usd === null
              ? "Unknown"
              : "$" + step.cost_usd,
            "m",
          ),
        );
        const error = step?.error
          ? " · error: " +
            (typeof step.error === "string"
              ? step.error
              : JSON.stringify(step.error))
          : "";
        const rel = cell(
          "relationships",
          `owns step ${agent.step_id} of mission ${agent.mission_id.slice(0, 8)} · source: ${step?.source?.event || "runtime observation"}` +
            error,
          "m",
        );
        rel.classList.add("wide");
        const action = node("span", undefined, "cell");
        action.append(button("Inspect " + agent.agent_id, () => inspect(agent)));
        cells.append(rel, action);
        row.append(cells);
        roster.append(row);
      }
      if (!agents.length)
        roster.append(
          node(
            "div",
            "No execution observations. Registered agents are not assumed to be running.",
            "roster-foot",
          ),
        );
      if (all.length > agents.length)
        roster.append(
          node(
            "div",
            "Showing the first 100 agents. Filter the mission scope for a smaller observation set.",
            "roster-foot",
          ),
        );
      roster.append(
        node(
          "div",
          "Progress, energy and SLA are not displayed: no measured field exists for them in the runtime stream.",
          "roster-foot",
        ),
      );
      main.append(roster);
    }
    main.append(inspector);
  }
  let live = null;
  function consoleFrame(data) {
    if (!live) {
      const frame = document.createElement("iframe");
      frame.src = "/opsconsole";
      frame.title = "Roboport Ops Console · Port / Flow / DAG views of observed agents";
      frame.setAttribute("sandbox", "allow-scripts allow-downloads");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.className = "ops-console";
      // A reloaded or re-attached frame asks for a resync; the feed answers
      // with a snapshot, and any envelope lost in between triggers another.
      live = { frame, feed: new root.RoboportOpsFeed.Feed(frame) };
    }
    live.feed.data = data || {};
    return live.frame;
  }
  function update(data) {
    if (!live || !live.frame.isConnected) return false;
    live.feed.update(data || {});
    const meta = document.querySelector("#h-fleet .meta");
    if (meta) {
      const all = (data && data.agents) || [];
      const steps = (data && data.steps) || [];
      meta.textContent = ` · ${all.length} observed agents · ${steps.length} steps · ${meta.dataset.source || ""}`;
    }
    return true;
  }
  root.RoboportFleet = { render, update };
})(window);
