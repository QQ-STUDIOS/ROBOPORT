"""Authoritative runtime observations, bounded scoped subscriptions."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .store import digest

TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})")


class Subscription:
    def __init__(self, events, environment, scope, actor):
        self.events, self.environment, self.scope, self.actor = (
            events,
            environment,
            scope,
            actor,
        )
        self.stream_id, self.seq = uuid.uuid4().hex, 0
        self.queue = deque()
        self.overflow, self.closed = False, False
        self.queue.append(self.envelope("snapshot", events._data(scope, actor)))

    def envelope(self, type_, data):
        return {
            "v": 1,
            "environment_id": self.environment,
            "stream_id": self.stream_id,
            "scope": self.scope,
            "seq": self.seq,
            "ts": time.time(),
            "type": type_,
            "data": data,
        }

    def publish(self):
        if self.closed or self.overflow:
            return
        if len(self.queue) >= 256:
            self.queue.clear()
            self.overflow = True
            return
        self.seq += 1
        self.queue.append(
            self.envelope("state.updated", self.events._data(self.scope, self.actor))
        )

    def next(self):
        with self.events.lock:
            if self.overflow:
                self.closed = True
                return self.envelope("resync", {"reason": "subscriber queue overflow"})
            return self.queue.popleft() if self.queue else None

    def close(self):
        with self.events.lock:
            self.closed = True
            if self in self.events.subscribers:
                self.events.subscribers.remove(self)


class EventStore:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.subscribers = []
        self.steps, self.agents, self.runtime, self.seen, self.offsets = (
            {},
            {},
            {},
            {},
            {},
        )
        self.last_state = None
        self.stop = threading.Event()
        self.budgets = {}

    def _data(self, scope, actor=None):
        missions = self.store.list_missions()
        if actor:
            missions = [
                m
                for m in missions
                if m["manifest"].get("classification")
                in actor.get("classifications", [])
                and (
                    actor.get("role") != "operator"
                    or m["manifest"]["owner"] == actor["id"]
                )
            ]
        if scope.startswith("mission:"):
            missions = [m for m in missions if m["id"] == scope[8:]]
        elif scope != "network":
            raise ValueError("unsupported event scope")
        ids = {m["id"] for m in missions}
        return {
            "missions": missions,
            "steps": [v for v in self.steps.values() if v["mission_id"] in ids],
            "agents": [v for v in self.agents.values() if v["mission_id"] in ids],
            "runtime": {k: v for k, v in self.runtime.items() if k in ids},
            "budgets": {k: v for k, v in self.budgets.items() if k in ids},
            "source": "runtime",
            "api_cost_usd": None,
            "host_cost_usd": None,
        }

    def snapshot(self, environment_id, scope, actor=None):
        with self.lock:
            return {
                "v": 1,
                "environment_id": environment_id,
                "stream_id": uuid.uuid4().hex,
                "scope": scope,
                "seq": 0,
                "ts": time.time(),
                "type": "snapshot",
                "data": self._data(scope, actor),
            }

    def subscribe(self, environment_id, scope, actor=None):
        with self.lock:
            sub = Subscription(self, environment_id, scope, actor)
            self.subscribers.append(sub)
            return sub

    def ingest(self, mission_id, record):
        with self.lock:
            if not isinstance(record, dict):
                raise ValueError("telemetry record must be an object")  # noqa: TRY004 — one public event validation exception
            identity = digest(record)
            seen = self.seen.setdefault(mission_id, set())
            if identity in seen:
                return
            seen.add(identity)
            if len(seen) > 50000:
                raise ValueError("event record limit exceeded")
            event = record.get("event")
            if record.get("run_id") and record["run_id"] != mission_id:
                raise ValueError("mixed runtime run ID")
            sid, aid = record.get("step_id"), record.get("agent")
            if sid and aid and event in {"step.start", "step.complete", "step.failed"}:
                key = mission_id + ":" + sid
                state = {
                    "step.start": "running",
                    "step.complete": "completed",
                    "step.failed": "failed",
                }[event]
                old = self.steps.get(key, {})
                # The plan wave arrives on step.start only; keep it once known.
                wave = record.get("wave")
                if not isinstance(wave, int) or isinstance(wave, bool):
                    wave = old.get("wave")
                # Runtime timestamps (UTC ISO seconds); anything else is dropped.
                ts = record.get("ts")
                if not isinstance(ts, str) or not TIMESTAMP.fullmatch(ts):
                    ts = None
                started = ts if event == "step.start" else old.get("started_at")
                finished = None if event == "step.start" else ts
                step = {
                    "id": key,
                    "mission_id": mission_id,
                    "step_id": sid,
                    "agent_id": aid,
                    "state": state,
                    "rev": old.get("rev", 0) + 1,
                    "error": record.get("error"),
                    "llm_calls": record.get("llm_calls"),
                    "tool_calls": record.get("tool_calls"),
                    "cost_usd": record.get("cost_usd"),
                    "wave": wave,
                    "started_at": started,
                    "finished_at": finished,
                    "source": record,
                }
                self.steps[key] = step
                akey = mission_id + ":" + aid
                self.agents[akey] = {
                    "id": akey,
                    "mission_id": mission_id,
                    "agent_id": aid,
                    "step_id": sid,
                    "state": state,
                    "energy": None,
                    "rev": self.agents.get(akey, {}).get("rev", 0) + 1,
                }
            elif event == "run.complete":
                self.runtime[mission_id] = record.get("run_summary", {})
            elif event == "run.start":
                self.runtime[mission_id] = {
                    "status": "running",
                    "run_id": record.get("run_id"),
                }
            for sub in self.subscribers:
                sub.publish()

    def sync(self, data_root):
        with self.lock:
            missions = self.store.list_missions()
            from .artifacts import read_evidence

            for mission in missions:
                try:
                    evidence = read_evidence(
                        mission,
                        "artifacts/budget.json",
                        data_root,
                        {
                            "id": "gateway",
                            "role": "admin",
                            "classifications": ["public", "internal"],
                        },
                    )
                    if isinstance(evidence["content"], dict):
                        self.budgets[mission["id"]] = evidence["content"]
                except (ValueError, OSError, UnicodeError):
                    pass
                path = (
                    Path(data_root) / "missions" / mission["id"] / "telemetry/run.log"
                )
                if not path.is_file():
                    continue
                offset = self.offsets.get(str(path), 0)
                with path.open("rb") as f:
                    f.seek(offset)
                    while True:
                        line = f.readline(1024 * 1024 + 1)
                        if not line or not line.endswith(b"\n"):
                            break
                        offset = f.tell()
                        try:
                            self.ingest(mission["id"], json.loads(line))
                        except (ValueError, UnicodeError):
                            self.runtime[mission["id"]] = {
                                "status": "unknown",
                                "error": "Malformed or mixed telemetry record",
                            }
                self.offsets[str(path)] = offset
            fingerprint = digest({"missions": missions, "budgets": self.budgets})
            if fingerprint != self.last_state:
                self.last_state = fingerprint
                for sub in self.subscribers:
                    sub.publish()

    def start(self, data_root):
        def follow():
            while not self.stop.wait(0.5):
                self.sync(data_root)

        threading.Thread(target=follow, daemon=True).start()
