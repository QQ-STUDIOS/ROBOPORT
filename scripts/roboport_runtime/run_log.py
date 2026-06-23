"""ROBOPORT — run-log emitter (crew run → Ops Console).

Writes the `run_log.jsonl` event stream that `dashboard/bridge.py` tails and
projects onto the Ops Console (`dashboard/`). One self-describing JSON object
per line, flushed as it happens, to `runs/<run_id>/run.log` — so the bridge can
follow a live `benchmark.py` run as it executes.

Stdlib only. Opt-in: `benchmark.py` constructs this only with `--run-log`, so
normal runs are untouched.

Event schema (consumed by dashboard/bridge.py and feed_adapter.js):

    {"event":"run.start","run_id":..,"crew":"jd_crew","ts":..}
    {"event":"plan.created","run_id":..,"plan":{"waves":[[..]],"steps":[..]},"ts":..}
    {"event":"step.start","step_id":..,"agent":"job_scout","wave":0,"ts":..}
    {"event":"step.complete","step_id":..,"agent":..,"duration_ms":3421,"llm_calls":1,"tool_calls":5,"provider":"ollama","model":"qwen3:14b","cost_usd":0.0,"latency_ms":3380,"ts":..}
    {"event":"step.failed","step_id":..,"agent":..,"error":..,"layer":"criterion_failed","ts":..}
    {"event":"run.complete","run_summary":{"steps":5,"llm_calls":4,"tool_calls":12,"wall_ms":..,"p95_ms":..},"ts":..}

`agent` is the crew agent id (a step's `owner`) so it maps onto the console's
stations. Optional `retry` / `critic.review` events are emitted if the runtime
ever produces them.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def waves_from_steps(steps: list[dict]) -> list[list[str]]:
    """Group step ids by their `wave` (ascending) for the plan.created payload."""
    by_wave: dict[int, list[str]] = {}
    for s in steps:
        by_wave.setdefault(int(s.get("wave", 0)), []).append(s.get("id", "?"))
    return [by_wave[w] for w in sorted(by_wave)]


class RunLog:
    """Append-only JSONL writer for the Ops Console event stream."""

    def __init__(self, run_dir: str | Path, run_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.path = self.run_dir / "run.log"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def _write(self, event: str, **fields: Any) -> None:
        rec = {"event": event, "ts": _now(), **fields}
        line = json.dumps(rec, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    # ---- lifecycle events ----------------------------------------------
    def run_start(self, crew: str) -> None:
        self._write("run.start", run_id=self.run_id, crew=crew)

    def plan_created(self, plan: dict) -> None:
        steps = plan.get("steps", [])
        self._write("plan.created", run_id=self.run_id,
                    plan={"waves": waves_from_steps(steps),
                          "steps": [s.get("id", "?") for s in steps]})

    def step_start(self, step_id: str, agent: str, wave: int = 0) -> None:
        self._write("step.start", step_id=step_id, agent=agent, wave=wave)

    def tool_call(self, step_id: str, tool: str) -> None:
        self._write("tool.call", step_id=step_id, tool=tool)

    def step_complete(self, step_id: str, agent: str, duration_ms: int,
                      llm_calls: int = 0, tool_calls: int = 0,
                      provider: Optional[str] = None, model: Optional[str] = None,
                      cost_usd: Optional[float] = None,
                      latency_ms: Optional[int] = None) -> None:
        # Phase 4: optional routing telemetry — only emitted when present, so the
        # event stays backward-compatible with the stub runtime and the dashboard.
        extra: dict[str, Any] = {}
        for key, val in (("provider", provider), ("model", model),
                         ("cost_usd", cost_usd), ("latency_ms", latency_ms)):
            if val is not None:
                extra[key] = val
        self._write("step.complete", step_id=step_id, agent=agent,
                    duration_ms=duration_ms, llm_calls=llm_calls, tool_calls=tool_calls,
                    **extra)

    def step_failed(self, step_id: str, agent: str, error: Optional[str],
                    layer: str = "criterion_failed") -> None:
        self._write("step.failed", step_id=step_id, agent=agent,
                    error=error or layer, layer=layer)

    def retry(self, step_id: str, attempt: int, reason: str) -> None:
        self._write("retry", step_id=step_id, attempt=attempt, reason=reason)

    def critic_review(self, step_id: str, verdict: str,
                      suggested_repair: Optional[str] = None) -> None:
        self._write("critic.review", step_id=step_id, verdict=verdict,
                    suggested_repair=suggested_repair)

    def run_complete(self, run_summary: dict) -> None:
        self._write("run.complete", run_summary=run_summary)

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass
