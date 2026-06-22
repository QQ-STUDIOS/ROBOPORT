# ROBOPORT — Docker collector

Make ROBOPORT show your **actual running containers** as live drones — agentless,
no changes to the containers themselves. The collector watches the Docker daemon
and emits the exact feed envelopes the UI already consumes.

```
compose project ──▶ ZONE          container ──▶ AGENT (drone)
image / service ──▶ STATION       CPU% ──▶ load / energy head-room
start / die / oom ──▶ agent.added / .removed / alert.raised
```

## Run

```bash
pip install docker fastapi "uvicorn[standard]"
python server.py          # http://localhost:8000
```

Endpoints (the contract):

| | |
|---|---|
| `GET /api/state` | one snapshot (cold load / resync) |
| `WS  /api/feed` | snapshot on connect, then `seq`-ordered deltas |
| `POST /api/commands` | command envelope; **ack only** — the change returns via the feed |

Commands wired to real Docker control (no agent code needed):
`agent.hold`/`agent.recall` → `pause`, `agent.resume` → `unpause`,
`agent.retire` → `stop`, `agent.restart` → `restart`.

## Point the UI at it — the swap

Both artifacts ship with the live client already written, gated by one flag.
Nothing in the render layer (Part C) changes.

**`roboport-mcp-network.html`** — flip the switch at the top of Part B:

```js
const LIVE = true;                          // was false (in-page mock)
const FEED_BASE = "http://localhost:8000";  // your server.py origin
```

`createLiveFeed()` then opens `ws(s)://host/api/feed?scope=<scope>`, POSTs
commands to `/api/commands`, and — crucially — re-opens the socket on
`setScope()` so the server replies with a fresh **scope-shaped** snapshot
(network rollups vs one zone's firehose). The mock (`createFeed`) and the live
feed expose the same interface, so the drill-through, inspector, and commands
all work untouched.

Two contract points the server upholds (see `server.py`):

- **Scope filtering** — a connection subscribes to ONE scope; `/api/feed?scope=`
  only forwards the envelopes that scope cares about (`_relevant()` mirrors the
  mock's rule). Bandwidth stays flat as zones multiply.
- **Per-connection `seq`** — because a scoped client never sees the envelopes
  its scope dropped, the server stamps a per-connection counter (not the global
  plane seq). Each stream stays contiguous, so the client's
  `seq !== last+1 → resync` gap detector works as-is.

(The single-host `roboport-docker-demo.html` swaps the same way — `scope` is
just always `network` there.)

## Task-level progress — `logtail.py`

`collector.py` sees containers; it can't see what a task *is* or how far along
it is. **`logtail.py`** adds that layer the cheapest honest way: it tails the
JSONL logs your agents already write and projects task lifecycle lines
(`task_enqueue` / `task_start` / `task_progress` / `task_end` / `task_handoff`)
onto the SAME feed — emitting `task.*` envelopes and thin `agent.updated`
*patches* (task_id / task_progress / station_id / eta_s). It owns no containers;
wire it to the collector's sequenced emitter so both sources share one seq
stream (see the docstring for the log-line schema + wiring). The consumer
merges patches by `agent_id`, so the container (collector) and the task
(logtail) compose into one drone on screen.

## See it first, without Docker

Two in-browser previews run the *exact* envelope contract against a fake
backend, so you can watch the feed-driven UI behave before any daemon exists:

- **`roboport-docker-demo.html`** — a fake Docker daemon (containers starting,
  churning CPU, OOM-ing, restarting) on one host.
- **`roboport-mcp-network.html`** — the two-level **MCP control plane**: MCP
  servers as zones on a network field, routes carrying cross-zone
  `task.handoff`s (the HIPAA zone is data-only, no agent transfer), and
  **click-to-drill** into a feed-scoped zone surface where drones pick tasks,
  fly to the MCP tool, and fill a progress ring (the logtail payoff). The feed
  is scoped per view: `scope=network` ships only zone rollups; `scope=zone:<id>`
  ships that zone's firehose — exactly how bandwidth stays flat as zones grow.

## What it can and can't see (agentless honesty)

- **Sees:** which containers exist, per-container CPU/mem, start/stop/die/OOM/restart,
  compose grouping. → topology + health, automatically.
- **Can't see from outside:** task identity or `task_progress` % inside a process.
  Those need a thin add-on — tail the agent's JSONL logs, or one in-code hook —
  layered on top of this same feed.

## Production checklist

- Auth on the socket (`?token=` / Entra bearer) + per-operator audit on `/api/commands`.
- Read-only Docker socket mount; never expose `/api/commands` unauthenticated.
- For multi-host / Kubernetes: swap the docker-py calls for the kube API
  (pod → drone, namespace → zone) — the envelope mapping is identical.
- Real `eta_s` if you add task-level events (the demo/mock fakes it).
