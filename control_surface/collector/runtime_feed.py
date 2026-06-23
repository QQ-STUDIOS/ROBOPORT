"""
ROBOPORT — runtime feed producer  (a crew run → the control-surface contract)
=============================================================================
The Docker collector turns *containers* into drones. This turns a ROBOPORT
**crew run** into drones — so the dashboard lights up from the framework's own
agents (`agents/`, `scripts/benchmark.py`) rather than only from Docker.

It implements the exact producer interface `server.py` mounts
(`subscribe / start / stop / snapshot / handle_command`) and emits the §6
single-host contract envelopes the single-host UIs consume
(`web/roboport-feed.html`): `config.stations`, `agents`, `stations`, `tasks`,
`metrics`, plus `agent.* / task.* / station.updated / metrics.tick /
log.appended / alert.raised`. The wire never carries x/y.

Mapping (faithful to a sequential crew pipeline)::

    crew agent (job_scout, …) ──▶ STATION   (a capability around the hub)
    plan step                 ──▶ TASK      (queued → assigned → running → done)
    the worker on a step      ──▶ AGENT     (a drone that flies to the station)

Two sources, one model:

* **tail** — point it at the JSONL `benchmark.py --feed-log` writes; it projects
  those lifecycle lines onto the wire as a real `--live` crew executes.
* **simulate** — no model, no log, no deps: it runs a synthetic `jd_crew`
  on a background thread so the surface is watchable immediately. This is the
  runtime analog of the in-browser `*-demo.html` fakes.

Stdlib only. Run via `server.py` (set `ROBOPORT_FEED_SOURCE=runtime` or
`runtime-demo`), or stand-alone for a quick check::

    python runtime_feed.py --simulate --frames 60     # print envelopes
    python runtime_feed.py --replay path/to/feed.jsonl # project a saved log
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

_PALETTE = ["#36c6e0", "#f2b134", "#4fd672", "#6c8cff", "#ff5a8a", "#c98bff", "#ff8f6b"]
ENERGY_LOW = 22
MAX_AGENTS = 24
RETURN_FLIGHT_S = 1.0          # how long a drone shows "returning" before it docks


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _station_hue(order: int) -> str:
    return _PALETTE[order % len(_PALETTE)]


# ---- crew topology (registry → ordered stations) ------------------------
def stations_from_registry(registry: dict, crew_id: str) -> List[dict]:
    """Order a crew's agents into pipeline stations by longest-path depth from
    the entry node (so parallel agents share a wave)."""
    crew = (registry.get("crews") or {}).get(crew_id)
    if not crew:
        # fall back: every crew_builder agent, registry order
        names = [k for k, v in registry.get("agents", {}).items()
                 if str(v.get("role", "")).startswith("domain.crew_builder")]
        return [{"station_id": f"stn.{n}", "name": n, "order": i,
                 "hue": _station_hue(i), "wave": i} for i, n in enumerate(names)]
    entry = crew["entry"]
    edges = crew.get("edges", [])
    depth: Dict[str, int] = {entry: 0}
    changed = True
    while changed:
        changed = False
        for e in edges:
            if e["from"] in depth:
                d = depth[e["from"]] + 1
                if depth.get(e["to"], -1) < d:
                    depth[e["to"]] = d
                    changed = True
    for e in edges:                      # any orphan defaults to wave 0
        depth.setdefault(e["from"], 0)
        depth.setdefault(e["to"], 0)
    ordered = sorted(depth.items(), key=lambda kv: (kv[1], kv[0]))
    return [{"station_id": f"stn.{name}", "name": name, "order": i,
             "hue": _station_hue(i), "wave": wave}
            for i, (name, wave) in enumerate(ordered)]


class RuntimeCrewCollector:
    """Owns the live crew model and emits contract envelopes. A single lock
    guards all mutation + emission, so the tail thread, the ticker thread, and
    operator commands compose safely."""

    def __init__(self, registry_path: str | Path, crew: str = "jd_crew",
                 feed_glob: Optional[str] = None, simulate: bool = False) -> None:
        self.crew = crew
        self.simulate = simulate
        self.feed_glob = feed_glob
        registry = json.loads(Path(registry_path).read_text(encoding="utf-8"))
        self._station_cfg = stations_from_registry(registry, crew)
        self._waves: Dict[int, List[dict]] = {}
        for s in self._station_cfg:
            self._waves.setdefault(s.get("wave", 0), []).append(s)

        self._subs: List[Callable[[dict], None]] = []
        self._seq = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._born = time.time()
        self._completed = 0
        self._completions: List[float] = []        # epoch secs, for tasks/min
        self._offsets: Dict[str, int] = {}
        self._partial: Dict[str, str] = {}

        self.agents: Dict[str, dict] = {}
        self.stations: Dict[str, dict] = {}
        self.tasks: Dict[str, dict] = {}
        for s in self._station_cfg:
            self.stations[s["station_id"]] = {
                "station_id": s["station_id"], "name": s["name"], "order": s["order"],
                "hue": s["hue"], "state": "idle", "worker_agent_id": None,
                "queue_depth": 0, "drain": False, "rev": 0}

    # ---- pub/sub --------------------------------------------------------
    def subscribe(self, cb: Callable[[dict], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)
        def off() -> None:
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return off

    def _emit(self, type_: str, data: dict) -> None:
        # caller holds (or doesn't need) the lock; snapshot the subs under lock
        with self._lock:
            self._seq += 1
            env = {"v": 1, "seq": self._seq, "ts": _now(), "type": type_, "data": data}
            subs = list(self._subs)
        for s in subs:
            try:
                s(env)
            except Exception:
                pass

    def _log(self, html: str) -> None:
        self._emit("log.appended", {"html": html, "ts": _now()})

    def _alert(self, kind: str, title: str, body: str, target: dict) -> None:
        self._emit("alert.raised", {
            "alert_id": f"al_{int(time.time() * 1000) % 100000}",
            "kind": kind, "title": title, "body": body,
            "target": target, "raised_at": _now(), "ttl_s": 6})

    # ---- dtos -----------------------------------------------------------
    def _bump(self, d: dict) -> dict:
        d["rev"] = d.get("rev", 0) + 1
        return d

    def _hue_of(self, station_id: Optional[str]) -> str:
        st = self.stations.get(station_id or "")
        return st["hue"] if st else "#6c8cff"

    # ---- model mutations (each emits the relevant envelope) -------------
    def _ensure_agent(self, agent_id: str, name: Optional[str] = None) -> dict:
        a = self.agents.get(agent_id)
        if a is None:
            a = {"agent_id": agent_id, "name": name or agent_id, "state": "docked",
                 "energy": 100.0, "hold": False, "task_id": None, "station_id": None,
                 "task_progress": 0.0, "eta_s": None, "completed_total": 0,
                 "error": None, "rev": 0, "updated_at": _now(), "_return_at": None}
            self.agents[agent_id] = a
            self._emit("agent.added", self._agent_dto(a))
        return a

    def _agent_dto(self, a: dict) -> dict:
        return {k: v for k, v in a.items() if not k.startswith("_")}

    def _emit_agent(self, a: dict) -> None:
        a["updated_at"] = _now()
        self._bump(a)
        self._emit("agent.updated", self._agent_dto(a))

    def _emit_station(self, st: dict) -> None:
        self._emit("station.updated", self._bump(st))

    def enqueue(self, task_id: str, station_id: str, agent_id: str,
                wave: int = 0, work_estimate_s: float = 1.5,
                agent_name: Optional[str] = None, priority: int = 100) -> None:
        with self._lock:
            if station_id not in self.stations or len(self.agents) >= MAX_AGENTS:
                return
            self._ensure_agent(agent_id, agent_name)
            self.tasks[task_id] = {
                "task_id": task_id, "station_id": station_id, "status": "queued",
                "priority": priority, "work_estimate_s": work_estimate_s,
                "assigned_agent_id": agent_id, "enqueued_at": _now(),
                "started_at": None, "finished_at": None, "result": None,
                "error": None, "rev": 0, "_wave": wave}
            st = self.stations[station_id]
            st["queue_depth"] = st.get("queue_depth", 0) + 1
            self._emit("task.enqueued", {k: v for k, v in self.tasks[task_id].items()
                                         if not k.startswith("_")})
            self._emit_station(st)

    def begin(self, task_id: str, agent_id: str, station_id: str,
              eta_s: float = 1.2) -> None:
        with self._lock:
            t = self.tasks.get(task_id)
            a = self._ensure_agent(agent_id)
            if t is None:
                return
            t["status"] = "assigned"
            t["started_at"] = _now()
            a.update(state="dispatched", station_id=station_id, task_id=task_id,
                     task_progress=0.0, eta_s=eta_s, _return_at=None)
            self._emit("task.assigned", {k: v for k, v in t.items()
                                         if not k.startswith("_")})
            self._emit_agent(a)

    def progress(self, task_id: str, agent_id: str, p: float,
                 eta_s: Optional[float] = None) -> None:
        with self._lock:
            t = self.tasks.get(task_id)
            a = self.agents.get(agent_id)
            if t is None or a is None:
                return
            first = a["state"] != "working"
            a.update(state="working", task_progress=max(0.0, min(1.0, p)), eta_s=eta_s)
            if first:
                t["status"] = "running"
                st = self.stations.get(a["station_id"])
                if st:
                    st.update(state="busy", worker_agent_id=agent_id)
                    self._emit_station(st)
            self._emit("task.progress", {"task_id": task_id, "assigned_agent_id": agent_id,
                                         "progress": a["task_progress"], "eta_s": eta_s})
            self._emit_agent(a)

    def complete(self, task_id: str, agent_id: str, status: str = "ok",
                 error: Optional[str] = None) -> None:
        with self._lock:
            t = self.tasks.get(task_id)
            a = self.agents.get(agent_id)
            ok = status == "ok"
            if t is not None:
                t.update(status="completed" if ok else "failed",
                         finished_at=_now(), error=error)
                self._emit("task.completed" if ok else "task.failed",
                           {"task_id": task_id, "assigned_agent_id": agent_id,
                            "status": "completed" if ok else "failed", "error": error})
                st = self.stations.get(t["station_id"])
                if st:
                    st["queue_depth"] = max(0, st.get("queue_depth", 0) - 1)
                    if st.get("worker_agent_id") == agent_id:
                        st.update(state="idle", worker_agent_id=None)
                    self._emit_station(st)
                self.tasks.pop(task_id, None)
            if a is not None:
                a.update(state="returning", task_id=None, task_progress=0.0,
                         eta_s=RETURN_FLIGHT_S, completed_total=a["completed_total"] + (1 if ok else 0),
                         error=None if ok else (error or "step failed"),
                         _return_at=time.time() + RETURN_FLIGHT_S)
                self._emit_agent(a)
            self._completed += 1 if ok else 0
            self._completions.append(time.time())
            if not ok:
                self._alert("critical", f"{agent_id} step failed",
                            error or "A crew step failed.", {"type": "agent", "id": agent_id})
                self._log(f'<b style="color:#ff5a52">{agent_id}</b> failed at '
                          f'{t["station_id"] if t else "?"}')

    # ---- ticker: dock returned drones + emit metrics --------------------
    def _ticker(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                for a in self.agents.values():
                    if a["state"] == "returning" and a.get("_return_at") and now >= a["_return_at"]:
                        a.update(state="docked", station_id=None, eta_s=None, _return_at=None)
                        self._emit_agent(a)
            self._emit("metrics.tick", self._metrics())
            self._stop.wait(1.0)

    def _metrics(self) -> dict:
        now = time.time()
        self._completions = [t for t in self._completions if now - t <= 60]
        with self._lock:
            agents = list(self.agents.values())
            queued = sum(1 for t in self.tasks.values() if t["status"] == "queued")
        return {
            "tasks_per_min": len(self._completions),
            "completed_total": self._completed,
            "active_agents": sum(1 for a in agents if a["state"] != "docked"),
            "total_agents": len(agents),
            "queued": queued,
            "uptime_s": int(now - self._born),
            "ts": _now(),
        }

    # ---- snapshot -------------------------------------------------------
    def snapshot(self, scope: str = "all") -> dict:
        with self._lock:
            agents = [self._agent_dto(a) for a in self.agents.values()]
            stations = [dict(s) for s in self.stations.values()]
            tasks = [{k: v for k, v in t.items() if not k.startswith("_")}
                     for t in self.tasks.values()
                     if t["status"] not in ("completed", "cancelled")]
        return {
            "config": {
                "stations": [{"station_id": s["station_id"], "name": s["name"],
                              "order": s["order"], "hue": s["hue"]}
                             for s in self._station_cfg],
                "crew": self.crew,
                "energy_low_threshold": ENERGY_LOW,
                "max_agents": MAX_AGENTS,
            },
            "agents": agents,
            "stations": stations,
            "tasks": tasks,
            "alerts": [],
            "metrics": self._metrics(),
        }

    # ---- lifecycle ------------------------------------------------------
    def start_source(self) -> None:
        threading.Thread(target=self._ticker, daemon=True).start()
        if self.simulate:
            threading.Thread(target=self._simulate_loop, daemon=True).start()
            self._log(f'<b style="color:#4fd672">runtime feed (simulate)</b> · '
                      f'{len(self._station_cfg)} stations · crew {self.crew}')
        elif self.feed_glob:
            threading.Thread(target=self._tail_loop, daemon=True).start()
            self._log(f'<b style="color:#4fd672">runtime feed online</b> · tailing '
                      f'{self.feed_glob} · {len(self._station_cfg)} stations')
        else:
            self._log(f'<b style="color:#f2b134">runtime feed idle</b> · no source '
                      f'(set ROBOPORT_FEED_GLOB or ROBOPORT_FEED_SOURCE=runtime-demo)')

    # server.py calls .start()/.stop()
    def start(self) -> None:  # noqa: D401 - matches DockerCollector interface
        self.start_source()

    def stop(self) -> None:
        self._stop.set()

    # ---- tail a JSONL feed log (benchmark.py --feed-log) ----------------
    def _tail_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for path in _glob.glob(self.feed_glob or ""):
                    self._drain(path)
            except Exception:
                pass
            self._stop.wait(0.3)

    def _drain(self, path: str) -> None:
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        off = self._offsets.get(path, 0)        # start at BOF so a finished run replays
        if off > size:                          # truncated/rotated
            off, self._partial[path] = 0, ""
        if off == size:
            return
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(off)
            chunk = f.read()
            self._offsets[path] = f.tell()
        buf = self._partial.get(path, "") + chunk
        lines = buf.split("\n")
        self._partial[path] = lines.pop()
        for line in lines:
            line = line.strip()
            if line:
                self.ingest(line)

    def ingest(self, line: str) -> None:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            return
        ev = r.get("event")
        if ev == "crew_start":
            self._reconcile_stations(r.get("stations") or [])
        elif ev == "task_enqueue":
            self.enqueue(r["task_id"], r["station_id"], r["agent_id"],
                         wave=int(r.get("wave", 0)),
                         work_estimate_s=float(r.get("work_estimate_s", 1.5)),
                         agent_name=r.get("agent_name"))
        elif ev == "task_start":
            self.begin(r["task_id"], r["agent_id"], r["station_id"],
                       eta_s=float(r.get("eta_s", 1.2)))
        elif ev == "task_progress":
            self.progress(r["task_id"], r["agent_id"], float(r.get("progress", 0.0)),
                          eta_s=r.get("eta_s"))
        elif ev == "task_end":
            self.complete(r["task_id"], r["agent_id"],
                          status="ok" if r.get("status") == "ok" else "error",
                          error=r.get("error"))

    def _reconcile_stations(self, stations: List[dict]) -> None:
        """Honor a crew_start roster: add any station the registry didn't predict."""
        with self._lock:
            known = {s["station_id"] for s in self._station_cfg}
            for s in stations:
                sid = s.get("station_id")
                if sid and sid not in known:
                    order = len(self._station_cfg)
                    cfg = {"station_id": sid, "name": s.get("name", sid),
                           "order": order, "hue": s.get("hue", _station_hue(order)),
                           "wave": s.get("wave", order)}
                    self._station_cfg.append(cfg)
                    self.stations[sid] = {**{k: cfg[k] for k in
                                             ("station_id", "name", "order", "hue")},
                                          "state": "idle", "worker_agent_id": None,
                                          "queue_depth": 0, "drain": False, "rev": 0}
                    known.add(sid)

    # ---- simulate a synthetic jd_crew (no model, no log) ---------------
    def _simulate_loop(self) -> None:
        run = 0
        while not self._stop.is_set():
            run += 1
            self._log(f'<b style="color:#6c8cff">crew run #{run}</b> dispatched · {self.crew}')
            for wave in sorted(self._waves):
                if self._stop.is_set():
                    return
                stations = self._waves[wave]
                live = []
                for s in stations:                       # dispatch the whole wave
                    tid = f"t_r{run}_{s['order']}"
                    aid = f"drone-{s['order']}"
                    est = 1.2 + 0.5 * s["order"] % 2 + 1.4
                    self.enqueue(tid, s["station_id"], aid, wave=wave,
                                 work_estimate_s=est, agent_name=aid)
                    self.begin(tid, aid, s["station_id"], eta_s=1.2)
                    live.append((tid, aid, est))
                self._stop.wait(1.2)                      # flight time
                for frac in (0.25, 0.55, 0.8, 1.0):       # fill the work rings
                    if self._stop.is_set():
                        return
                    for tid, aid, est in live:
                        self.progress(tid, aid, frac, eta_s=round(est * (1 - frac), 2))
                    self._stop.wait(0.7)
                for i, (tid, aid, _est) in enumerate(live):
                    fail = (run % 4 == 0 and wave == 1 and i == 0)   # an occasional miss
                    self.complete(tid, aid, status="error" if fail else "ok",
                                  error="schema-invalid output (simulated)" if fail else None)
                self._stop.wait(0.6)                      # handoff to next wave
            self._stop.wait(4.0)                          # idle, then run again

    # ---- commands (operator intents on the live surface) ---------------
    def handle_command(self, cmd: dict) -> dict:
        t = cmd.get("type")
        target = cmd.get("target") or {}
        tid = target.get("id")
        args = cmd.get("args") or {}
        try:
            with self._lock:
                if t == "agent.hold":
                    a = self.agents.get(tid)
                    if not a:
                        return _reject(cmd, "unknown agent")
                    a["hold"] = bool(args.get("hold", True))
                    self._emit_agent(a)
                    self._log(f'<b>{tid}</b> {"held" if a["hold"] else "resumed"}')
                elif t == "agent.resume":
                    a = self.agents.get(tid)
                    if not a:
                        return _reject(cmd, "unknown agent")
                    a["hold"] = False
                    self._emit_agent(a)
                elif t in ("agent.recall", "agent.retire"):
                    a = self.agents.get(tid)
                    if not a:
                        return _reject(cmd, "unknown agent")
                    if t == "agent.retire":
                        self.agents.pop(tid, None)
                        self._emit("agent.removed", {"agent_id": tid})
                    else:
                        a.update(state="returning", task_id=None, eta_s=RETURN_FLIGHT_S,
                                 _return_at=time.time() + RETURN_FLIGHT_S)
                        self._emit_agent(a)
                    self._log(f'<b style="color:#6c8cff">{tid}</b> {t.split(".")[1]}')
                elif t == "station.drain":
                    st = self.stations.get(tid)
                    if not st:
                        return _reject(cmd, "unknown station")
                    st["drain"] = bool(args.get("drain", True))
                    self._emit_station(st)
                elif t == "fleet.hold":
                    h = bool(args.get("hold", True))
                    for a in self.agents.values():
                        a["hold"] = h
                        self._emit_agent(a)
                elif t == "fleet.recall":
                    for a in self.agents.values():
                        if a["state"] != "docked":
                            a.update(state="returning", task_id=None,
                                     eta_s=RETURN_FLIGHT_S, _return_at=time.time() + RETURN_FLIGHT_S)
                            self._emit_agent(a)
                elif t in ("station.prioritize", "task.enqueue", "queue.clear",
                           "fleet.spawn_agent"):
                    self._log(f"operator: {t}")   # accepted, advisory on a live run
                else:
                    return _reject(cmd, f"unsupported command {t}")
            return _ok(cmd)
        except Exception as e:  # noqa: BLE001
            return _reject(cmd, str(e))


def _ok(cmd: dict) -> dict:
    return {"command_id": cmd.get("command_id"), "status": "accepted"}


def _reject(cmd: dict, reason: str) -> dict:
    return {"command_id": cmd.get("command_id"), "status": "rejected", "reason": reason}


# ---- stand-alone: print envelopes (verify without a browser/server) -----
def _repo_registry() -> Path:
    return Path(__file__).resolve().parents[2] / "agents" / "registry.json"


def _main() -> int:
    ap = argparse.ArgumentParser(description="ROBOPORT runtime feed — stand-alone check")
    ap.add_argument("--registry", default=str(_repo_registry()))
    ap.add_argument("--crew", default="jd_crew")
    ap.add_argument("--simulate", action="store_true", help="run a synthetic crew")
    ap.add_argument("--replay", metavar="GLOB", help="project a saved feed JSONL log")
    ap.add_argument("--frames", type=int, default=40, help="envelopes to print then exit")
    args = ap.parse_args()

    seen = {"types": {}, "n": 0}
    done = threading.Event()

    def sink(env: dict) -> None:
        if done.is_set():
            return
        seen["types"][env["type"]] = seen["types"].get(env["type"], 0) + 1
        seen["n"] += 1
        d = env.get("data", {})
        tag = d.get("agent_id") or d.get("task_id") or d.get("station_id") or ""
        print(f'{env["seq"]:>4} {env["type"]:<16} {tag}')
        if seen["n"] >= args.frames:
            done.set()

    c = RuntimeCrewCollector(args.registry, crew=args.crew,
                             feed_glob=args.replay, simulate=args.simulate)
    c.subscribe(sink)
    print(f"# crew={args.crew} stations={[s['name'] for s in c._station_cfg]}")
    c.start()
    done.wait(timeout=30)
    c.stop()
    print(f"# {seen['n']} envelopes  types={seen['types']}")
    # snapshot() is served on connect (GET /api/state, WS open), not emitted here;
    # a healthy run produces the task/agent lifecycle deltas.
    ok = seen["n"] > 0 and "agent.updated" in seen["types"] and "task.progress" in seen["types"]
    print("# OK" if ok else "# INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
