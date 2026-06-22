# ROBOPORT — control surface

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
```

## Wire it to real containers

The collector watches the Docker daemon and emits the same envelopes the mock
produces — so the UI swaps source with a one-flag change (see
[`collector/README.md`](collector/README.md)).

```bash
pip install -r control_surface/collector/requirements.txt
python control_surface/collector/server.py        # http://localhost:8000
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
emit the [`CONTRACT.md`](CONTRACT.md) envelopes (the Docker collector here, a
Kubernetes collector, or the runtime itself) lights up the dashboard. The
collector is the reference producer; it proves the contract end-to-end without
touching agent code.

## Status & next steps

Landed as the design package: working UIs + a reference Docker/logtail
collector + the full contract. Open threads (see `CONTRACT.md` §11): auth on the
socket (Entra bearer), task-payload schemas per station, completed-task history
endpoint, and a runtime-native feed producer so a live `benchmark.py` crew
streams straight onto the surface.
