# ROBOPORT — control surface

> One of ROBOPORT's two operator surfaces (the port/drones view). Its sibling is
> the **Ops Console** ([`../dashboard/`](../dashboard/README.md), the wave DAG).
> How they relate and how a run feeds both: [`../docs/observability.md`](../docs/observability.md).

The operator-facing layer for ROBOPORT: an animated agent dashboard plus an
**agentless** feed source. It renders the runtime as a port full of drones —
agents dock, dispatch to stations, work, and return — without the runtime ever
shipping a pixel. The backend emits *logical* state; the client owns all motion.

> **One rule above all:** the wire never carries `x/y`. The server says *what* an
> agent is doing; the client decides *where on screen*. See [`CONTRACT.md`](CONTRACT.md) §1.

---

## Layout

```
control_surface/
  CONTRACT.md            the wire protocol (client ↔ runtime) — read this first
  web/                   self-contained HTML surfaces (no build step)
    roboport.html          the agent control surface (flagship)
    roboport-control.html  control-surface variant
    roboport-feed.html     feed-driven against an in-page mock backend
    roboport-network.html  control plane: network → zone drill-through
    roboport-mcp-network.html  MCP control plane (zones, routes, handoffs)
    roboport-topology.html scaling topology diagram
    roboport-docker-demo.html  fake Docker daemon preview (no daemon needed)
  collector/             agentless feed source (FastAPI + docker-py)
    server.py              mounts /api/state, /api/feed, /api/commands
    collector.py           Docker daemon → drones (topology + health)
    logtail.py             agent JSONL logs → task.* progress envelopes
    README.md              collector run + wiring notes
    requirements.txt       docker, fastapi, uvicorn
```

## See it with zero setup

Every surface is a single static file — open it in a browser, no build, no
server. The mock-backed ones run the *exact* envelope contract against an
in-page fake feed:

```bash
# pick any surface
open control_surface/web/roboport.html
open control_surface/web/roboport-docker-demo.html   # fake Docker daemon
open control_surface/web/roboport-mcp-network.html   # MCP zones + drill-through
open control_surface/web/roboport-replay.html        # a REAL jd_crew run, baked in (no server)
```

`roboport-replay.html` is a self-contained recording of an actual runtime-feed
crew run — open it locally or drop it on any static host (Vercel/Netlify/Pages)
for a public URL. Regenerate it from a fresh run with
[`collector/make_replay.py`](collector/make_replay.py).

## Wire it to a live ROBOPORT crew  (runtime-native feed)

The dashboard lights up from the framework's **own** agents — a real
`benchmark.py` crew run, not just Docker. The runtime emits logical lifecycle
telemetry; the runtime feed producer projects it onto the contract.

**One command (Docker) — live & interactive:**

```bash
docker compose -f control_surface/docker-compose.yml up --build
# open http://localhost:8000/roboport-feed.html?live=1
```

Drones fly to the crew stations and the command buttons actually mutate the live
view (the change returns through the feed). Drive it from a *real* crew run
instead of the synthetic demo by setting `ROBOPORT_FEED_SOURCE=runtime` +
`ROBOPORT_FEED_GLOB` and mounting a `--feed-log` dir — see the comments in
[`docker-compose.yml`](docker-compose.yml).

**Or run it directly:**

```bash
pip install -r control_surface/collector/requirements.txt

# zero setup — a synthetic jd_crew, no model, no deps beyond FastAPI:
ROBOPORT_FEED_SOURCE=runtime-demo python control_surface/collector/server.py

# or a REAL crew run: serve the feed, then run the crew with --feed-log
ROBOPORT_FEED_SOURCE=runtime ROBOPORT_FEED_GLOB=/tmp/feed.jsonl \
  python control_surface/collector/server.py            # terminal 1
python scripts/benchmark.py --target jd_crew --live --feed-log /tmp/feed.jsonl   # terminal 2
```

Then open `web/roboport-feed.html` and flip the switch at the top of PART B:

```js
const LIVE = true;                            // was false (in-page mock)
const FEED_BASE = "http://localhost:8000";    // server.py origin
```

Each crew agent becomes a station around the hub; each plan step becomes a task;
a drone flies to its station, fills the work ring, and returns. See
[`collector/README.md`](collector/README.md#runtime-native-feed-a-crew-run-as-drones).

## Wire it to real containers (Docker)

The collector watches the Docker daemon and emits the same envelopes — so the UI
swaps source with a one-flag change (see [`collector/README.md`](collector/README.md)).

```bash
python control_surface/collector/server.py        # default ROBOPORT_FEED_SOURCE=docker
```

Then flip the live switch at the top of `web/roboport-mcp-network.html`:

```js
const LIVE = true;
const FEED_BASE = "http://localhost:8000";
```

## How it relates to the rest of ROBOPORT

The core repo (`agents/`, `workflows/`, `scripts/roboport_runtime/`) is the
*runtime* — typed agents executing crews. This directory is the *control
surface* over a running fleet. They meet at the feed contract: anything that can
emit the [`CONTRACT.md`](CONTRACT.md) envelopes lights up the dashboard. Two
reference producers ship today:

- **`collector/runtime_feed.py`** — a ROBOPORT crew run (the framework's own
  agents). Sourced from the lifecycle telemetry `scripts/benchmark.py --feed-log`
  writes (`scripts/roboport_runtime/feed_log.py`), or a synthetic crew in
  `simulate` mode. **This is the runtime-native path.**
- **`collector/collector.py`** — the Docker daemon (agentless, containers as drones).

Both implement the same producer interface, so `server.py` mounts either via
`ROBOPORT_FEED_SOURCE`.

## Status & next steps

Landed: working UIs + the full contract + two reference producers (runtime crew
+ Docker). Open threads (see `CONTRACT.md` §11): auth on the socket (Entra
bearer), per-station task-payload schemas, and a completed-task history
endpoint for the activity log.
