# Running the DataOps crew `--live`

Everything in ROBOPORT is proven **offline** (stub executor + governance + fault-injection
gates). This runbook is the one step that needs a real model backend: running a crew
end-to-end against a live LLM. It is written for the **DataOps crew on the synthetic
`resources/datasets/sales_messy.csv` fixture** — no production data, no PHI.

> **Why it can't run in a stock cloud session.** The cloud container has no model
> backend: `api.anthropic.com` bypasses the egress proxy (so no credential is injected
> and `request_credential` won't store one for it), and there is no local Ollama. A real
> backend has to be supplied one of the two ways below.

## Option A — Anthropic (API key)

1. Add **`ANTHROPIC_API_KEY`** in the environment's settings (cloud environment menu in
   the session title bar → Edit → Network secrets / API credentials, or as an environment
   variable). **Never paste the key into chat.** A **new session picks it up** — it does
   not hot-load into a running container.
2. The `anthropic` SDK is declared in `requirements.txt`; install deps if the container is
   fresh: `pip install -r requirements.txt`.
3. Pick models the key can access. The provider defaults
   (`ANTHROPIC_MODEL_REASONING`, `ANTHROPIC_MODEL_DEFAULT` in
   `scripts/roboport_runtime/providers.py`) may be older IDs — override them to current
   Claude models, e.g.:
   ```bash
   export ANTHROPIC_MODEL_REASONING=claude-sonnet-4-6   # or a current Sonnet/Opus id
   export ANTHROPIC_MODEL_DEFAULT=claude-haiku-4-6       # or a current Haiku id
   ```
4. Run one pass over the proof set (4 evals), streaming to the Ops Console log:
   ```bash
   python scripts/benchmark.py --target dataops_crew \
     --eval-set evals/dataops_evals.json --runs 1 \
     --live --provider anthropic --run-log runs/
   ```

## Option B — Ollama (THERAGEN-STUDIO, the real Roboport host)

The production Roboport runs `qwen3:14b` on a local Ollama at `127.0.0.1:11434`. On that
host no key is needed:

```bash
export ROBOPORT_PROVIDER=ollama   # optional; --provider ollama does the same
python scripts/benchmark.py --target dataops_crew \
  --eval-set evals/dataops_evals.json --runs 1 \
  --live --provider ollama --run-log runs/
```

Set `OLLAMA_MODEL_REASONING` / `OLLAMA_MODEL_DEFAULT` if your pulled models differ from the
defaults in `providers.py`.

## What `--live` does

- `benchmark.py` runs a **preflight** (`health_check()`) and prints the active provider,
  then swaps the stub planner/executor/grader for the model-backed runtime
  (`scripts/roboport_runtime`).
- Each eval drives the crew `data_engineering → reporting`: ingest+clean the messy CSV into
  a typed `CleanedDataset`, then produce a `DataReport` grounded in it.
- The three-layer error stack is live: transient-retry + schema-repair (L1), per-agent
  budget (L2), unsafe-action escalation (L3). The DataOps crew is read-only, so L3 never
  fires here.

## Artifacts to expect

- `runs/<run_id>/run.log` — the Ops Console event stream. Watch it live with
  `python dashboard/serve.py` (the console derives DataOps stations from the registry).
- The benchmark output dir (timestamped, or `--label`/`--out`) — per-run `output.json`,
  `usage` telemetry (provider, model, tokens, `cost_usd`, latency), and, with `--grade`,
  `grading.json`.
- Compare two live runs with `python scripts/diff_runs.py` and summarize provider/model
  cost with `python scripts/aggregate.py`.

## Scope / safety

Run this against the **synthetic fixture only**. Do not point `--live` at real Brightree,
Snowflake, Microsoft 365, or any PHI/production source — the modeled Theragen crews
(`docs/THERAGEN_AGENTS.md`) are offline records and must stay that way.
