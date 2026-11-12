# Running a crew `--live`

Everything in ROBOPORT is proven **offline** (stub executor + governance + fault-injection
gates). This is the one step that drives a crew end-to-end against a real model. It is
written for the **DataOps crew on the synthetic `resources/datasets/sales_messy.csv`
fixture** — no production data, no PHI — but the launch pattern is the framework's default
for any crew.

## The designed launch — local Ollama + the operator surfaces

By design, ROBOPORT runs live on a **workstation against a local Ollama** and you watch it
through its **two operator surfaces**. This is the default: `get_provider()` returns the
`ollama` provider unless told otherwise (`scripts/roboport_runtime/providers.py`), and
onboarding's "run for real" hits local Ollama (`docs/onboarding.md`). This is how a hardware
tier A station runs (`qwen3:14b`, see `deploy/windows/hardware-tiers.json`).

**1. Bring up the model** (one-time pull, then serve):
```bash
ollama pull qwen3:14b      # ~9GB; matches the Crew Builder UI
ollama serve               # if not already running (defaults to 127.0.0.1:11434)
```
The `OllamaProvider` defaults to `qwen3.5:latest` (reasoning) / `gemma4:latest` (default);
override with `OLLAMA_MODEL_REASONING` / `OLLAMA_MODEL_DEFAULT` to match what you pulled,
or edit `config/agent_config.yaml`.

**2. Run the crew** — no `--provider` needed; `ollama` is the default:
```bash
python scripts/benchmark.py --target dataops_crew \
  --eval-set evals/dataops_evals.json --runs 1 \
  --live --run-log runs/ --feed-log /tmp/feed.jsonl --grade
```

**3. Watch it** on either operator surface (or both — different files, different ports; see
`docs/observability.md`):
```bash
# Ops Console — the wave-DAG view (typed contracts flowing station→station), port 4242
python dashboard/bridge.py --runs-dir runs
# open http://localhost:4242/?api=http://localhost:4242

# Control surface — the port/drones view (the fleet in motion), port 8000
ROBOPORT_FEED_SOURCE=runtime ROBOPORT_FEED_GLOB=/tmp/feed.jsonl \
  python control_surface/collector/server.py
```
The Ops Console derives DataOps' stations from the registry, so the crew renders without any
console change. The final report lands in `runs/<run_id>/`.

## Fallback — a keyed cloud / Anthropic run

Use this only when there is **no workstation / no local Ollama** (e.g. a cloud container).
It swaps the default provider for Anthropic; note the cloud container **cannot host the
:4242 / :8000 operator surfaces**, so you get the run artifacts but not the live view.

1. Add **`ANTHROPIC_API_KEY`** to the environment this session runs in — its *own* secrets,
   not another environment's and not the chat. A **new** session picks it up (it does not
   hot-load into a running container). Confirm the container actually sees it:
   `python -c "import os;print(bool(os.environ.get('ANTHROPIC_API_KEY')))"`.
2. `pip install -r requirements.txt` (brings in the `anthropic` SDK).
3. The `AnthropicProvider` defaults (`ANTHROPIC_MODEL_REASONING=claude-opus-4-7`,
   `ANTHROPIC_MODEL_DEFAULT=claude-sonnet-4-6`) may be **older ids** — export both to current
   Claude models your key can call before running.
4. Same command as above, with `--provider anthropic`:
   ```bash
   python scripts/benchmark.py --target dataops_crew \
     --eval-set evals/dataops_evals.json --runs 1 \
     --live --provider anthropic --run-log runs/ --grade
   ```

## What `--live` does

- `benchmark.py` runs a **preflight** (`health_check()`) and prints the active provider, then
  swaps the stub planner/executor/grader for the model-backed runtime
  (`scripts/roboport_runtime`).
- Each eval drives the crew `data_engineering → reporting`: ingest+clean the messy CSV into a
  typed `CleanedDataset`, then produce a `DataReport` grounded in it.
- The three-layer error stack is live: transient-retry + schema-repair (L1), per-agent budget
  (L2), unsafe-action escalation (L3). The DataOps crew is read-only, so L3 never fires here.

## Artifacts to expect

- `runs/<run_id>/run.log` — the Ops Console event stream (tailed by `dashboard/bridge.py`).
- `/tmp/feed.jsonl` — the Control surface feed (read by `control_surface/collector`).
- The benchmark output dir (timestamped, or `--label`/`--out`) — per-run `output.json`, `usage`
  telemetry (provider, model, tokens, `cost_usd`, latency; Ollama cost is 0.0), and, with
  `--grade`, `grading.json`.
- Compare two live runs with `python scripts/diff_runs.py`; summarize provider/model cost with
  `python scripts/aggregate.py`.

## Scope / safety

Run this against the **synthetic fixture only**. Do not point `--live` at a real line-of-business
system, data warehouse, Microsoft 365, or any sensitive or production source. Crews modeled in a
station pack (`stations/<id>/`) are offline records and must stay that way.
