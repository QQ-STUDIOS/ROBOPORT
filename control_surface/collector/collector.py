"""
ROBOPORT — Docker collector  (agentless feed source)
=====================================================
Watches the local Docker daemon and translates container lifecycle + resource
telemetry into the ROBOPORT feed contract (the §4 envelopes the UI consumes).

It changes NOTHING about your agents. It only observes:
    compose project ........ -> ZONE   (label com.docker.compose.project)
    container .............. -> AGENT  (a "drone")
    image / service ........ -> STATION (the capability it embodies)
    CPU% ................... -> load   (high CPU == "working")
    1 - CPU/ceiling ........ -> energy (head-room left; low == throttled)
    start / die / oom ...... -> agent.added / .removed / alert.raised

The contract is identical to the in-browser mock, so the existing ROBOPORT
surface renders this with a 6-line change (see README). The wire never carries
pixels — only logical state.

Dependencies:  pip install docker
Run it via server.py (which mounts the FastAPI feed); this file is import-only.
"""

from __future__ import annotations
import threading, time, datetime, queue
from typing import Callable, Dict, List, Optional

try:
    import docker  # docker-py
except ImportError as e:  # pragma: no cover
    raise SystemExit("pip install docker   # docker-py SDK is required") from e


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# CPU% above this is treated as "working"; energy is head-room below the ceiling.
CPU_WORKING_THRESHOLD = 12.0
CPU_CEILING = 100.0
STATS_POLL_SECONDS = 2.0


class DockerCollector:
    """Owns the live model and emits envelopes. Thread-safe-ish: a single
    background thread mutates state and calls subscribers synchronously."""

    def __init__(self) -> None:
        self.client = docker.from_env()
        self._subs: List[Callable[[dict], None]] = []
        self._seq = 0
        self._lock = threading.RLock()
        # model
        self.agents: Dict[str, dict] = {}     # container_id -> agent dto
        self.zones: Dict[str, dict] = {}       # project -> zone state
        self.stations: Dict[str, dict] = {}    # image/service -> station
        self._completed = 0
        self._born = time.time()
        self._stop = threading.Event()

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
        with self._lock:
            self._seq += 1
            env = {"v": 1, "seq": self._seq, "ts": _now(), "type": type_, "data": data}
            subs = list(self._subs)
        for s in subs:
            try:
                s(env)
            except Exception:  # a slow/broken consumer must not kill the collector
                pass

    # ---- mapping helpers ------------------------------------------------
    @staticmethod
    def _project_of(c) -> str:
        labels = c.labels or {}
        return labels.get("com.docker.compose.project") or "default"

    @staticmethod
    def _service_of(c) -> str:
        labels = c.labels or {}
        return (labels.get("com.docker.compose.service")
                or (c.image.tags[0].split(":")[0] if c.image and c.image.tags else "image"))

    def _zone_id(self, project: str) -> str:
        return f"zone.{project}"

    def _station_id(self, service: str) -> str:
        return f"stn.{service}"

    def _state_for(self, status: str, cpu: float) -> str:
        # docker status -> roboport agent state
        if status in ("created", "restarting"):
            return "dispatched"
        if status in ("removing", "paused"):
            return "returning"
        if status == "exited" or status == "dead":
            return "docked"
        # running:
        return "working" if cpu >= CPU_WORKING_THRESHOLD else "docked"

    def _agent_dto(self, c, cpu: float = 0.0, mem: float = 0.0) -> dict:
        project = self._project_of(c)
        service = self._service_of(c)
        status = c.status  # created|restarting|running|paused|exited|dead
        state = self._state_for(status, cpu)
        return {
            "agent_id": c.id[:12],
            "name": c.name,
            "zone_id": self._zone_id(project),
            "station_id": self._station_id(service),
            "state": state,
            "energy": round(max(0.0, CPU_CEILING - cpu), 1),   # head-room
            "hold": status == "paused",
            "task_id": None,
            "task_progress": 0.0,
            "eta_s": None,
            "completed_total": 0,
            "error": None,
            "cpu_pct": round(cpu, 1),
            "mem_pct": round(mem, 1),
            "image": (c.image.tags[0] if c.image and c.image.tags else c.image.short_id),
            "status": status,
            "rev": 0,
            "updated_at": _now(),
        }

    # ---- snapshot -------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            zones = list(self.zones.values())
            agents = list(self.agents.values())
        active = sum(1 for a in agents if a["state"] != "docked")
        return {
            "config": {"network": "Docker daemon", "source": "docker", "cpu_ceiling": CPU_CEILING},
            "zones": zones,
            "agents": agents,
            "stations": list(self.stations.values()),
            "routes": [],
            "metrics": {
                "tasks_per_min": 0,
                "total_agents": len(agents),
                "active_agents": active,
                "zones": len(zones),
                "uptime_s": int(time.time() - self._born),
                "ts": _now(),
            },
        }

    def emit_snapshot(self) -> None:
        self._emit("snapshot", self.snapshot())

    def _rebuild_zone(self, project: str) -> dict:
        zid = self._zone_id(project)
        members = [a for a in self.agents.values() if a["zone_id"] == zid]
        active = sum(1 for a in members if a["state"] != "docked")
        low = any(a["energy"] < 22 and a["state"] != "docked" for a in members)
        util = (active / len(members)) if members else 0.0
        health = "ok"
        if low or any(a["status"] in ("dead", "restarting") for a in members):
            health = "critical"
        elif util > 0.85:
            health = "warn"
        z = {
            "zone_id": zid, "name": project, "color": _zone_color(project),
            "tag": "docker project", "policy": "elastic",
            "agents_total": len(members), "agents_active": active,
            "agents_busy": sum(1 for a in members if a["state"] in ("working", "dispatched")),
            "queued": 0, "tasks_per_min": 0,
            "utilization": round(util, 2), "health": health, "rev": 0,
        }
        self.zones[project] = z
        return z

    # ---- event ingestion ------------------------------------------------
    def _index_container(self, c) -> None:
        project, service = self._project_of(c), self._service_of(c)
        sid = self._station_id(service)
        if sid not in self.stations:
            self.stations[sid] = {"station_id": sid, "name": service, "order": len(self.stations),
                                   "hue": _station_hue(service), "state": "idle",
                                   "worker_agent_id": None, "queue_depth": 0, "drain": False, "rev": 0}
        dto = self._agent_dto(c)
        self.agents[c.id[:12]] = dto

    def _bootstrap(self) -> None:
        for c in self.client.containers.list(all=True):
            self._index_container(c)
        for project in {self._project_of(c) for c in self.client.containers.list(all=True)}:
            self._rebuild_zone(project)

    def _handle_event(self, ev: dict) -> None:
        if ev.get("Type") != "container":
            return
        action = ev.get("Action", "")
        cid = ev.get("id", "")[:12]
        attrs = (ev.get("Actor") or {}).get("Attributes", {})
        name = attrs.get("name", cid)
        try:
            c = self.client.containers.get(cid)
        except Exception:
            c = None

        if action in ("start", "create", "unpause", "restart"):
            if c:
                self._index_container(c)
                self._emit("agent.added" if action in ("start", "create") else "agent.updated",
                           self.agents[cid])
                self._rebuild_and_emit_zone(self._project_of(c))
                self._log(f'<b style="color:#4fd672">{name}</b> {action}')
        elif action in ("die", "stop", "kill", "pause"):
            if cid in self.agents:
                if c:
                    self.agents[cid] = self._agent_dto(c)
                self.agents[cid]["state"] = "docked"
                self._emit("agent.updated", self.agents[cid])
                kind = "critical" if action in ("die", "kill") else "warning"
                self._alert(kind, f"{name} {action}", f"Container {name} {action}.",
                            {"type": "agent", "id": cid})
                if c:
                    self._rebuild_and_emit_zone(self._project_of(c))
                self._log(f'<b style="color:#ff5a52">{name}</b> {action}')
        elif action == "oom":
            self._alert("critical", f"{name} OOM", "Container hit its memory limit.",
                        {"type": "agent", "id": cid})
            self._log(f'<b style="color:#ff5a52">{name}</b> out-of-memory')
        elif action == "destroy":
            self.agents.pop(cid, None)
            self._emit("agent.removed", {"agent_id": cid})

    def _rebuild_and_emit_zone(self, project: str) -> None:
        self._emit("zone.updated", self._rebuild_zone(project))

    def _log(self, html: str) -> None:
        self._emit("log.appended", {"html": html, "ts": _now()})

    def _alert(self, kind: str, title: str, body: str, target: dict) -> None:
        self._emit("alert.raised", {
            "alert_id": f"al_{int(time.time()*1000)%100000}",
            "kind": kind, "title": title, "body": body,
            "target": target, "raised_at": _now(), "ttl_s": 6,
        })

    # ---- stats polling (CPU/mem -> energy/load) ------------------------
    def _poll_stats(self) -> None:
        while not self._stop.is_set():
            for cid, dto in list(self.agents.items()):
                try:
                    c = self.client.containers.get(cid)
                    if c.status != "running":
                        continue
                    s = c.stats(stream=False)
                    cpu = _cpu_percent(s)
                    mem = _mem_percent(s)
                    new = self._agent_dto(c, cpu, mem)
                    new["rev"] = dto["rev"] + 1
                    self.agents[cid] = new
                    self._emit("agent.updated", new)
                except Exception:
                    continue
            # zone rollups + metrics ~every poll
            for project in {a["zone_id"].split(".", 1)[1] for a in self.agents.values()}:
                self._rebuild_and_emit_zone(project)
            self._emit("metrics.tick", self.snapshot()["metrics"])
            self._stop.wait(STATS_POLL_SECONDS)

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        self._bootstrap()
        threading.Thread(target=self._poll_stats, daemon=True).start()
        threading.Thread(target=self._event_loop, daemon=True).start()
        self._log(f'<b style="color:#4fd672">Docker collector online</b> · {len(self.agents)} containers')

    def _event_loop(self) -> None:
        for ev in self.client.events(decode=True):
            if self._stop.is_set():
                break
            try:
                self._handle_event(ev)
            except Exception:
                continue

    def stop(self) -> None:
        self._stop.set()

    # ---- commands (real control, no agent code) ------------------------
    def handle_command(self, cmd: dict) -> dict:
        t, target = cmd.get("type"), cmd.get("target") or {}
        cid = target.get("id")
        try:
            if t in ("agent.recall", "agent.hold"):
                self.client.containers.get(cid).pause(); return _ok(cmd)
            if t == "agent.resume":
                self.client.containers.get(cid).unpause(); return _ok(cmd)
            if t == "agent.retire":
                self.client.containers.get(cid).stop(); return _ok(cmd)
            if t == "agent.restart":
                self.client.containers.get(cid).restart(); return _ok(cmd)
            return _reject(cmd, f"unsupported command {t}")
        except Exception as e:
            return _reject(cmd, str(e))


# ---- module helpers -----------------------------------------------------
def _ok(cmd):     return {"command_id": cmd.get("command_id"), "status": "accepted"}
def _reject(cmd, r): return {"command_id": cmd.get("command_id"), "status": "rejected", "reason": r}

def _cpu_percent(s: dict) -> float:
    try:
        cpu = s["cpu_stats"]; pre = s["precpu_stats"]
        cd = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sd = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage", []) or [1])
        return (cd / sd) * ncpu * 100.0 if sd > 0 and cd > 0 else 0.0
    except Exception:
        return 0.0

def _mem_percent(s: dict) -> float:
    try:
        usage = s["memory_stats"]["usage"] - s["memory_stats"].get("stats", {}).get("cache", 0)
        return usage / s["memory_stats"]["limit"] * 100.0
    except Exception:
        return 0.0

_PALETTE = ["#36c6e0", "#f2b134", "#4fd672", "#6c8cff", "#ff5a8a", "#c98bff", "#ff8f6b"]
def _zone_color(project: str) -> str:
    return _PALETTE[hash(project) % len(_PALETTE)]
def _station_hue(service: str) -> str:
    return _PALETTE[hash(service) % len(_PALETTE)]
