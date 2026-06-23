# ROBOPORT Ops Console — Integration Guide

> One of ROBOPORT's two operator surfaces (the wave-DAG view). Its sibling is the
> **control surface** ([`../control_surface/`](../control_surface/README.md), the
> port/drones view). How they relate and how a run feeds both:
> [`../docs/observability.md`](../docs/observability.md).

Live operational dashboard for ROBOPORT runs. Shows the JD-Crew
pipeline executing in real time: agents dispatching through wave
stages, typed data contracts flowing station-to-station, latency
heatmaps, alert correlation rings, and a session debrief overlay.

```
Orchestrator ──► run_log.jsonl ──► bridge.py (SSE) ──► feed_adapter.js ──► Ops Console
```

> **Imported from Claude Design.** `Roboport Ops Console.dc.html` is a Claude
> Design document: `support.js` (the dc-runtime) parses its `<x-dc>` block,
> loads React from unpkg, and renders the console. The live-feed seam is already
> wired (`feed_adapter.js` is loaded in `<head>`, and the backend line selects
> live-vs-mock from the URL), so no edits are needed to run it.

## One command (Docker)

```bash
cp dashboard/.env.example dashboard/.env      # add your ANTHROPIC_API_KEY
docker compose -f dashboard/docker-compose.yml up --build
# open http://localhost:4242/?api=http://localhost:4242
```

With a key the container runs a real `jd_crew` run (provider=anthropic) and
serves its replay; **without** a key it serves the bundled sample run — either
way the console + SSE bridge come up on `:4242`. The key is read from
`dashboard/.env` (gitignored) — never bake it into an image.

## Try it now (zero setup)

A self-contained sample run (`sample_run.jsonl` — a jd_crew run with a
compliance failure → retry → critic → recovery) ships alongside the kit.

```bash
# 1) serve the console + stream the sample over SSE
python dashboard/bridge.py --log-file dashboard/sample_run.jsonl

# 2) open the console pointed at the bridge
#    http://localhost:4242/?api=http://localhost:4242
```

Modes, selected by URL (no source edits):

| URL | Backend |
|---|---|
| *(none)* | in-page **mock** — fully interactive |
| `?api=http://localhost:4242` | **SSE** live feed from `bridge.py` (replays history on connect, then tails) |
| `?drop` (or `?live`) | **file-drop** — drag any `run.log` / `*.jsonl` onto the canvas, no server |

`serve.py` is an alternative server with a run browser and replay/`--watch`
modes; it answers both `/api/feed` and `/events`, so the console's `?api=` seam
works against it too:

```bash
python dashboard/serve.py --run runs/<run_id>            # replay a finished run
python dashboard/serve.py --run runs/<run_id> --watch    # follow a live run
```

---

## Quick start (live mode)

**1 — Start a ROBOPORT run, emitting the Ops Console event stream**

`--run-log` writes `runs/<run_id>/run.log` (the `run_log.jsonl` this bridge
tails). It's opt-in via `scripts/roboport_runtime/run_log.py`; normal runs are
untouched.

```bash
python scripts/benchmark.py \
  --target jd_crew --live --runs 1 \
  --input "Senior backend engineer, remote-US, posted last 14 days" \
  --run-log runs
```

> Use `--live` for the real 8-station crew (needs Ollama or `ANTHROPIC_API_KEY`).
> Without `--live` the stub planner emits a single `stub` step — the event
> pipeline still works, but you won't see the full DAG.

**2 — Launch the bridge server**

```bash
# Auto-detects the most-recent run in runs/ and tails it as it grows
python dashboard/bridge.py --runs-dir runs/

# Or point at a specific run
python dashboard/bridge.py --run-id <run_id>

# Bridge is now live at http://localhost:4242 (also serves the console)
```

**3 — Open the console**

Open `Roboport Ops Console.dc.html` in your browser, then add `?api=`:

```
file:///path/to/Roboport Ops Console.dc.html?api=http://localhost:4242
```

The console switches from mock backend to the live SSE feed automatically.

---

## File-drop mode (no server)

1. Run any ROBOPORT job so `runs/<run_id>/run.log` exists.
2. Open the console normally (no `?api=` param).
3. Drag `run.log` or any `run_log.jsonl` onto the canvas.
4. The run replays at 4× speed with full interactivity.
5. Use the Replay scrubber to step through frames.

---

## Regression overlay (cross-run diff)

Beyond watching one run, the console can render a **cross-run regression diff**
from [`scripts/diff_runs.py`](../scripts/diff_runs.py) — *which agent/contract
regressed?* — using the same station/alert vocabulary: a flagged agent lights its
station with an alert ring (**red** = regression, **amber** = warning) and every
signal lands in the log.

```bash
# bridge computes the diff and serves it as an overlay
python dashboard/bridge.py --baseline runs/<base> --candidate runs/<cand>
# ...or render a precomputed diff JSON
python scripts/diff_runs.py --baseline runs/<base> --candidate runs/<cand> --out diff.json
python dashboard/bridge.py --diff diff.json
```

No server? Drag a `diff_against_baseline.json` (the `--out` of `diff_runs.py`)
straight onto the canvas — the adapter detects a diff document and overlays it
offline, just like a `run.log`.

---

## File layout

```
dashboard/
  feed_adapter.js    Browser-side adapter — SSE client + file-drop parser
  bridge.py          Python SSE server — tails run_log.jsonl, translates events
  README.md          This file

Roboport Ops Console.dc.html   The dashboard (place anywhere accessible from a browser)
```

To add the feed adapter to the console, load it before the closing `</body>` tag:

```html
<script src="dashboard/feed_adapter.js"></script>
```

Then in the main script, replace:

```js
const backend = createMockBackend();
```

with:

```js
const apiUrl = new URLSearchParams(location.search).get("api");
const backend = apiUrl
  ? window.ROBOPORT_ADAPTER({ apiUrl })
  : createMockBackend();
```

---

## Station → agent mapping

| Console station    | ROBOPORT agent          | Wave | Input contract               | Output contract     |
|--------------------|-------------------------|:----:|------------------------------|---------------------|
| `job_scout`        | job_scout               | 0    | `search_query`               | `list[Job]`         |
| `technical_analyst`| technical_analyst       | 1 ∥  | `list[Job]`                  | `TechnicalAnalysis` |
| `compliance_risk`  | compliance_risk         | 1 ∥  | `list[Job]`                  | `ComplianceAnalysis`|
| `application_strategist` | application_strategist | 2 | `TechnicalAnalysis + ComplianceAnalysis` | `CandidateMatch` |
| `synthesizer`      | synthesizer             | 3    | `CandidateMatch`             | `FinalReport`       |
| `salary_estimator` | salary_estimator        | 4 ⌥  | `FinalReport`                | `SalaryBand`        |
| `resume_tailor`    | resume_tailor           | 4 ⌥  | `FinalReport`                | `TailoredResume`    |
| `cover_letter_writer` | cover_letter_writer  | 4 ⌥  | `FinalReport`                | `CoverLetter`       |

∥ = runs in parallel with wave-mate  
⌥ = optional, gated on `match.priority ≤ 2`

---

## run_log.jsonl event format

The bridge expects JSONL in `runs/<run_id>/run.log`. Supported events:

```jsonl
{"event":"run.start","run_id":"abc123","crew":"jd_crew","ts":"2026-06-22T10:00:00Z"}
{"event":"plan.created","run_id":"abc123","plan":{"waves":[...],"steps":[...]},"ts":"..."}
{"event":"step.start","step_id":"scout","agent":"job_scout","wave":0,"ts":"..."}
{"event":"tool.call","step_id":"scout","tool":"search_linkedin","ts":"..."}
{"event":"tool.call","step_id":"scout","tool":"dedupe_jobs","ts":"..."}
{"event":"step.complete","step_id":"scout","agent":"job_scout","duration_ms":3421,"llm_calls":1,"tool_calls":5,"ts":"..."}
{"event":"step.start","step_id":"technical","agent":"technical_analyst","wave":1,"ts":"..."}
{"event":"step.start","step_id":"compliance","agent":"compliance_risk","wave":1,"ts":"..."}
{"event":"step.complete","step_id":"technical","agent":"technical_analyst","duration_ms":6102,"llm_calls":1,"tool_calls":3,"ts":"..."}
{"event":"step.failed","step_id":"compliance","agent":"compliance_risk","error":"criterion: all findings have evidence","layer":"criterion_failed","ts":"..."}
{"event":"retry","step_id":"compliance","attempt":1,"reason":"criterion_failed","ts":"..."}
{"event":"critic.review","step_id":"compliance","verdict":"fix","suggested_repair":"re-run with evidence requirement explicit in prompt","ts":"..."}
{"event":"step.complete","step_id":"compliance","agent":"compliance_risk","duration_ms":4850,"llm_calls":1,"tool_calls":2,"ts":"..."}
{"event":"step.complete","step_id":"strategy","agent":"application_strategist","duration_ms":5210,"llm_calls":1,"tool_calls":2,"ts":"..."}
{"event":"step.complete","step_id":"synth","agent":"synthesizer","duration_ms":180,"llm_calls":0,"tool_calls":0,"ts":"..."}
{"event":"run.complete","run_summary":{"steps":5,"llm_calls":4,"tool_calls":12,"wall_ms":22000,"p95_ms":6102},"ts":"..."}
```

Emit these events from the Orchestrator using standard Python `logging` with a
JSON formatter, or add `run_log_event(type, payload)` calls directly:

```python
# In orchestrator.py (example)
import json, sys
def log_event(event: str, **kwargs):
    line = json.dumps({"event": event, "ts": datetime.utcnow().isoformat()+"Z", **kwargs})
    print(line, file=sys.stderr)   # or write to runs/<run_id>/run.log
```

---

## Error taxonomy → visual signals

| ROBOPORT layer   | Error type          | Console display                          |
|-----------------|---------------------|------------------------------------------|
| Layer 1 (call)  | `transient`         | ⚠ warning toast · amber station ring    |
| Layer 1 (call)  | `schema_invalid`    | ⚠ warning toast · amber station ring    |
| Layer 2 (step)  | `criterion_failed`  | 🔴 critical toast · red station ring    |
| Layer 2 (step)  | `budget_exceeded`   | 🔴 critical toast · red station ring    |
| Layer 3 (user)  | `plan_invalid`      | 🔴 critical toast · hub ring            |
| Layer 3 (user)  | `unsafe`            | 🔴 critical toast · hub ring            |

The three-layer error stack from `docs/architecture.md` maps directly
onto the console's incident feed and alert-correlation ring system.

---

## Adding a new crew

1. Add agents to `agents/registry.json` under a new `crews` entry.
2. In `feed_adapter.js`, update the `STATIONS` array to match the new
   crew's pipeline order and wave structure.
3. In `bridge.py`, update `STATIONS` and `STATION_HUES` to match.
4. The DAG view auto-lays out stations by `wave` + position-within-wave —
   no other changes needed.

---

## Development tips

```bash
# Run bridge in watch mode, auto-picking the latest run
python dashboard/bridge.py --runs-dir runs/ --port 4242

# Replay a finished run by dropping it on the canvas
# (no bridge needed — file-drop mode works offline)

# Validate the run log format
python -c "
import json, sys
for i, line in enumerate(open(sys.argv[1])):
    try: json.loads(line)
    except: print(f'line {i+1}: invalid JSON')
" runs/<run_id>/run.log
```

---

## Repo placement

Suggested location: `dashboard/` at the repo root.

```
ROBOPORT/
  agents/
  config/
  dashboard/         ← place here
    feed_adapter.js
    bridge.py
    README.md
    Roboport Ops Console.dc.html
  docs/
  runs/
  scripts/
  workflows/
```
