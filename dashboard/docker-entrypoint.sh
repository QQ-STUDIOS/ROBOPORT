#!/bin/sh
# ROBOPORT Ops Console container entrypoint.
#   ANTHROPIC_API_KEY set  → run a live jd_crew run, then serve its replay
#   no key                 → serve the bundled sample run (keyless demo)
# Either way the console + SSE bridge come up on :4242.
set -e
RUNS="${ROBOPORT_RUNS_DIR:-/data/runs}"
mkdir -p "$RUNS"

if [ -n "$ANTHROPIC_API_KEY" ]; then
  echo "[entrypoint] ANTHROPIC_API_KEY present — running a live jd_crew run (provider=anthropic)"
  python scripts/benchmark.py \
    --target jd_crew --live --provider anthropic \
    --runs "${ROBOPORT_RUNS:-1}" --run-log "$RUNS" \
    || echo "[entrypoint] benchmark exited non-zero — serving whatever was produced"
  echo "[entrypoint] serving Ops Console + bridge on :4242 (tailing $RUNS)"
  exec python dashboard/bridge.py --runs-dir "$RUNS" --port 4242
else
  echo "[entrypoint] No ANTHROPIC_API_KEY — serving the bundled sample run (keyless demo)"
  echo "[entrypoint] set a key in dashboard/.env to run a real crew instead"
  exec python dashboard/bridge.py --log-file dashboard/sample_run.jsonl --port 4242
fi
