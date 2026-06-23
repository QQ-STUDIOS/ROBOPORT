"""ROBOPORT — runtime feed log (crew → control-surface telemetry).

The control surface (`control_surface/`) renders a running crew as a port full
of drones. Per the data contract, the runtime emits *logical* lifecycle events;
the dashboard owns all motion. This writer is the cheapest honest seam: it
appends one self-describing JSON object per line to a log that
`control_surface/collector/runtime_feed.py` tails and projects onto the wire
envelopes. The runtime never ships a pixel — only what an agent is doing.

It is intentionally dependency-free (stdlib only) and side-effect-light: if no
feed-log path is configured, `benchmark.py` never constructs one, so normal
runs are untouched.

Line schema (one JSON object per line; unknown fields ignored downstream):

    {"event":"crew_start","run_id":..,"crew":"jd_crew","input":..,
     "stations":[{"station_id":"stn.job_scout","name":"job_scout","order":0,"hue":"#36c6e0"}, ...]}
    {"event":"task_enqueue","run_id":..,"task_id":"t_s1","station_id":"stn.job_scout",
     "agent_id":"drone-s1","agent_name":"drone-s1","wave":0,"work_estimate_s":1.5,"priority":100}
    {"event":"task_start","run_id":..,"task_id":..,"agent_id":..,"station_id":..,"eta_s":1.2}
    {"event":"task_progress","run_id":..,"task_id":..,"agent_id":..,"progress":0.5,"eta_s":0.6}
    {"event":"task_end","run_id":..,"task_id":..,"agent_id":..,"status":"ok"|"error",
     "error":null,"llm_calls":4,"tool_calls":2,"deterministic":false}
    {"event":"crew_end","run_id":..,"status":"ok"}
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Station hue palette — mirrors the docker collector so the two feed sources
# look like one product. Stations cycle through it in pipeline order.
_PALETTE = ["#36c6e0", "#f2b134", "#4fd672", "#6c8cff", "#ff5a8a", "#c98bff", "#ff8f6b"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def station_hue(order: int) -> str:
    return _PALETTE[order % len(_PALETTE)]


def stations_from_plan(plan: dict) -> list[dict]:
    """Derive the station roster from a plan's steps: one station per distinct
    `owner` (the crew agent that does the work), ordered by first appearance /
    wave. This is what the dashboard lays out around the hub."""
    seen: dict[str, int] = {}
    for step in plan.get("steps", []):
        owner = step.get("owner", "agent")
        wave = int(step.get("wave", 0))
        if owner not in seen or wave < seen[owner]:
            seen[owner] = wave
    ordered = sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))
    return [
        {"station_id": f"stn.{owner}", "name": owner, "order": i, "hue": station_hue(i)}
        for i, (owner, _wave) in enumerate(ordered)
    ]


class FeedLog:
    """Append-only JSONL writer for crew lifecycle telemetry. Thread-safe
    enough for the sequential runtime: a single lock guards the file handle."""

    def __init__(self, path: str | Path, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    # ---- low-level ------------------------------------------------------
    def _write(self, event: str, **fields: Any) -> None:
        rec = {"event": event, "ts": _now(), "run_id": self.run_id, **fields}
        line = json.dumps(rec, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    # ---- lifecycle events ----------------------------------------------
    def crew_start(self, crew: str, stations: list[dict],
                   input: Optional[str] = None) -> None:
        self._write("crew_start", crew=crew, stations=stations, input=input)

    def task_enqueue(self, task_id: str, station_id: str, agent_id: str,
                     wave: int = 0, work_estimate_s: float = 1.5,
                     priority: int = 100, agent_name: Optional[str] = None) -> None:
        self._write("task_enqueue", task_id=task_id, station_id=station_id,
                    agent_id=agent_id, agent_name=agent_name or agent_id,
                    wave=wave, work_estimate_s=work_estimate_s, priority=priority)

    def task_start(self, task_id: str, agent_id: str, station_id: str,
                   eta_s: float = 1.2) -> None:
        self._write("task_start", task_id=task_id, agent_id=agent_id,
                    station_id=station_id, eta_s=eta_s)

    def task_progress(self, task_id: str, agent_id: str, progress: float,
                      eta_s: Optional[float] = None) -> None:
        self._write("task_progress", task_id=task_id, agent_id=agent_id,
                    progress=round(float(progress), 3), eta_s=eta_s)

    def task_end(self, task_id: str, agent_id: str, status: str = "ok",
                 error: Optional[str] = None, **meta: Any) -> None:
        self._write("task_end", task_id=task_id, agent_id=agent_id,
                    status=status, error=error, **meta)

    def crew_end(self, status: str = "ok") -> None:
        self._write("crew_end", status=status)

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.close()
            except Exception:
                pass
