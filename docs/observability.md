# Observability — watching a ROBOPORT run

ROBOPORT ships **two operator surfaces** over the *same* runtime. They are
different views of one crew run, not two systems:

| Surface | Lives in | Shows | What you watch |
|---|---|---|---|
| **Control surface** | [`control_surface/`](../control_surface/README.md) | a port full of **drones** — agents dock → dispatch → work → return | the fleet in motion, energy, queue, alerts |
| **Ops Console** | [`dashboard/`](../dashboard/README.md) | the **wave DAG** — typed contracts flowing station→station | the pipeline executing, latency, retries, the error stack |

Both obey the same rule the wire protocol is built on
([`control_surface/CONTRACT.md`](../control_surface/CONTRACT.md)):

> **The backend owns truth; the client owns motion.** The runtime emits *logical*
> state — *what* each agent is doing — and the browser decides *where on screen*.
> The wire never carries x/y.

---

## One run feeds them

A normal `scripts/benchmark.py` run is untouched until you opt in to telemetry.
Two independent, stdlib-only emitters turn a run into each surface's feed — and
you can pass **both** to light up both views from a single crew:

| Surface | Emitter flag | Writer | Consumer |
|---|---|---|---|
| Control surface | `--feed-log PATH` | `scripts/roboport_runtime/feed_log.py` | `control_surface/collector/runtime_feed.py` |
| Ops Console | `--run-log DIR` | `scripts/roboport_runtime/run_log.py` | `dashboard/bridge.py` |

```
                          scripts/benchmark.py --target jd_crew --live
                                    │  (emits logical telemetry, opt-in)
              ┌─────────────────────┴─────────────────────┐
       --feed-log feed.jsonl                       --run-log runs/
              │                                            │
   control_surface/collector  ──ws──►  port view   dashboard/bridge.py ──sse──► Ops Console
   (runtime_feed.py)                                (tails runs/<id>/run.log)
```

---

## Run the live stack

Pick a surface (or run both — they read different files and ports):

**Control surface** — the port/drones view:
```bash
# terminal 1 — serve the surface, tailing the feed log
ROBOPORT_FEED_SOURCE=runtime ROBOPORT_FEED_GLOB=/tmp/feed.jsonl \
  python control_surface/collector/server.py
# terminal 2 — run the crew, emitting the feed
python scripts/benchmark.py --target jd_crew --live --feed-log /tmp/feed.jsonl
# open control_surface/web/roboport-feed.html?live=1
```

**Ops Console** — the wave-DAG view:
```bash
# terminal 1 — run the crew, emitting the Ops Console event stream
python scripts/benchmark.py --target jd_crew --live --run-log runs
# terminal 2 — serve the console + bridge, tailing the newest run
python dashboard/bridge.py --runs-dir runs
# open http://localhost:4242/?api=http://localhost:4242
```

**Both at once** — one run, both surfaces:
```bash
python scripts/benchmark.py --target jd_crew --live \
  --feed-log /tmp/feed.jsonl --run-log runs
# then start both servers above (ports 8000 and 4242 don't collide)
```

No model handy? Each surface has a zero-setup path — `runtime-demo` /
sample-replay / file-drop — documented in its own README.

## One command (Docker)

Each surface ships a container that runs the whole thing on one port:

```bash
docker compose -f control_surface/docker-compose.yml up --build   # → :8000
docker compose -f dashboard/docker-compose.yml up --build         # → :4242
```

The Ops Console image takes an `ANTHROPIC_API_KEY` (in `dashboard/.env`) to run a
real `--live` crew; without one it serves the bundled sample. See each surface's
README for the details.

---

## Which view when

- **Control surface** when you care about the *fleet* — throughput, which agents
  are saturated, energy/queue pressure, where drones pile up.
- **Ops Console** when you care about the *pipeline* — which station a run is in,
  typed-contract handoffs, latency per step, and the three-layer error stack
  (retry → critic → escalation) rendered as incident toasts and alert rings.

Both are pure feed consumers: anything that emits the contract envelopes (the
runtime here, a Docker collector, a future Kubernetes collector) lights them up.
