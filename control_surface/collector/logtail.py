"""
ROBOPORT — log-tail collector  (task-level progress, agentless)
===============================================================
collector.py gives you topology + health from Docker. It cannot see what a
task *is* or how far along it is — that lives inside the process. This adds
that layer the cheapest honest way: tail the JSONL logs your agents already
write, and project task lifecycle lines onto the SAME feed.

It owns NO containers. It only emits task.* envelopes and thin agent.updated
*patches* (task_id / task_progress / station_id / eta_s / state). The consumer
merges patches by agent_id, so collector.py (truth about the container) and
logtail.py (truth about the task) compose into one agent on screen.

------------------------------------------------------------------
Expected log line (one JSON object per line; unknown fields ignored)
------------------------------------------------------------------
  {"ts":"2026-06-22T01:00:00Z","agent_id":"a1b2c3","event":"task_start",
   "task_id":"t_8f2a","zone_id":"mcp-snowflake","tool":"query","eta_s":12}
  {... "event":"task_progress","task_id":"t_8f2a","progress":0.41}
  {... "event":"task_end","task_id":"t_8f2a","status":"ok"}        # or "error"
  {... "event":"task_enqueue","task_id":"t_9c1d","zone_id":"mcp-github"}
  {... "event":"task_handoff","task_id":"t_8f2a","from_zone":"mcp-snowflake",
        "to_zone":"mcp-filesystem"}                                # cross-zone

event ∈ {task_enqueue, task_start, task_progress, task_end, task_handoff}
  agent_id  required for start/progress/end
  zone_id   = the MCP server the work runs against (your "zone")
  tool      = the MCP tool invoked (your "station")
  progress  0..1 ; eta_s seconds remaining (optional but recommended)

------------------------------------------------------------------
Wiring (in server.py)
------------------------------------------------------------------
  from logtail import LogTailCollector
  lt = LogTailCollector(emit=collector._emit, glob="/var/log/agents/*.jsonl")
  lt.start()                       # shares collector's sequenced emitter

It deliberately uses the collector's _emit so task + container deltas ride one
seq stream — the client never sees a gap between the two sources.

No third-party deps; plain stdlib polling tail (swap for watchdog if you like).
"""

from __future__ import annotations
import glob as _glob
import json
import os
import threading
import time
import datetime
from typing import Callable, Dict, Optional


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


POLL_SECONDS = 0.4


class LogTailCollector:
    def __init__(self, emit: Callable[[str, dict], None],
                 glob: str = "/var/log/agents/*.jsonl") -> None:
        # emit(type, data) — pass collector._emit so the seq stream is shared.
        self._emit = emit
        self._glob = glob
        self._stop = threading.Event()
        self._offsets: Dict[str, int] = {}      # file -> byte offset
        self._tasks: Dict[str, dict] = {}        # task_id -> task state
        self._partial: Dict[str, str] = {}       # file -> trailing partial line

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for path in _glob.glob(self._glob):
                    self._drain(path)
            except Exception:
                pass
            self._stop.wait(POLL_SECONDS)

    # ---- tail one file --------------------------------------------------
    def _drain(self, path: str) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        off = self._offsets.get(path, size)        # start at EOF on first sight
        if off > size:                              # truncated/rotated -> reset
            off, self._partial[path] = 0, ""
        if off == size:
            self._offsets[path] = size
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(off)
            chunk = f.read()
            self._offsets[path] = f.tell()
        buf = self._partial.get(path, "") + chunk
        lines = buf.split("\n")
        self._partial[path] = lines.pop()           # keep trailing partial
        for line in lines:
            line = line.strip()
            if line:
                self._ingest(line)

    # ---- project one log line onto the feed -----------------------------
    def _ingest(self, line: str) -> None:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            return
        ev = r.get("event")
        tid = r.get("task_id")
        if not ev or not tid:
            return
        aid = r.get("agent_id")
        zone = r.get("zone_id")
        tool = r.get("tool")
        sid = f"{zone}:{tool}" if zone and tool else r.get("station_id")

        if ev == "task_enqueue":
            self._tasks[tid] = {"task_id": tid, "zone_id": zone, "tool": tool,
                                "state": "queued", "progress": 0.0, "agent_id": None}
            self._emit("task.enqueued", self._tasks[tid])

        elif ev == "task_start":
            t = self._tasks.setdefault(tid, {"task_id": tid})
            t.update(zone_id=zone, tool=tool, agent_id=aid, state="working",
                     progress=0.0, eta_s=r.get("eta_s"))
            self._emit("task.assigned", t)
            self._patch_agent(aid, zone, sid, tid, 0.0, r.get("eta_s"), "working")

        elif ev == "task_progress":
            t = self._tasks.setdefault(tid, {"task_id": tid})
            p = float(r.get("progress", 0.0))
            t.update(progress=p, eta_s=r.get("eta_s"))
            self._emit("task.progress", {"task_id": tid, "progress": p,
                                         "eta_s": r.get("eta_s"), "agent_id": aid or t.get("agent_id")})
            self._patch_agent(aid or t.get("agent_id"), t.get("zone_id"),
                              sid or _sid(t), tid, p, r.get("eta_s"), "working")

        elif ev == "task_end":
            t = self._tasks.get(tid, {"task_id": tid, "agent_id": aid})
            status = r.get("status", "ok")
            t.update(state="completed" if status == "ok" else "failed",
                     progress=1.0, error=r.get("error"))
            self._emit("task.completed" if status == "ok" else "task.failed",
                       {"task_id": tid, "agent_id": t.get("agent_id"),
                        "status": status, "error": r.get("error")})
            # free the agent (container keeps running; collector still owns it)
            self._patch_agent(t.get("agent_id"), t.get("zone_id"), None, None,
                              0.0, None, "running")
            self._tasks.pop(tid, None)

        elif ev == "task_handoff":
            self._emit("task.handoff", {"task_id": tid, "from_zone": r.get("from_zone"),
                                        "to_zone": r.get("to_zone"), "tool": tool})

    def _patch_agent(self, aid: Optional[str], zone, sid, tid,
                     progress: float, eta_s, state: str) -> None:
        if not aid:
            return
        # thin patch — only the task-owned fields; collector owns the rest
        self._emit("agent.updated", {
            "agent_id": aid, "zone_id": zone, "station_id": sid,
            "task_id": tid, "task_progress": round(progress, 3),
            "eta_s": eta_s, "state": state, "updated_at": _now(),
            "_patch": True,                       # hint: merge, don't replace
        })


def _sid(t: dict):
    z, tool = t.get("zone_id"), t.get("tool")
    return f"{z}:{tool}" if z and tool else None
