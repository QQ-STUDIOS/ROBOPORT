#!/usr/bin/env python3
"""
ROBOPORT Ops Console — Bridge Server
======================================
Tails a ROBOPORT run_log.jsonl file and serves its events via
Server-Sent Events (SSE), so the Ops Console can connect live.

USAGE
-----
  # Watch the most-recent run in runs/ directory (auto-detects new runs)
  python dashboard/bridge.py --runs-dir runs/

  # Watch a specific run
  python dashboard/bridge.py --run-id abc123

  # Watch a specific log file
  python dashboard/bridge.py --log-file runs/abc123/run.log

  # Change port (default 4242)
  python dashboard/bridge.py --runs-dir runs/ --port 4242

Then open the console:
  Roboport Ops Console.dc.html?api=http://localhost:4242

REQUIRES
--------
  Python 3.11+  (stdlib only — no pip installs needed)
"""

import argparse
import json
import os
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional

# ── Global state ──────────────────────────────────────────────────────────────
_subscribers: list = []
_subscribers_lock = threading.Lock()
_history: list = []            # every broadcast envelope, replayed to new clients
_HISTORY_CAP = 8000
_seq = 0
_seq_lock = threading.Lock()

def next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq

def broadcast(envelope: dict) -> None:
    data = json.dumps(envelope)
    dead = []
    with _subscribers_lock:
        # Keep a bounded history so a client that connects after events were
        # emitted (a finished run, or a reconnect) still gets the full picture.
        _history.append(data)
        if len(_history) > _HISTORY_CAP:
            del _history[: len(_history) - _HISTORY_CAP]
        for q in _subscribers:
            try:
                q.append(data)
            except Exception:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)

def make_envelope(type_: str, data: dict) -> dict:
    return {
        "v": 1,
        "seq": next_seq(),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": type_,
        "data": data,
    }

# ── Translator: run_log event → console envelopes ────────────────────────────
STATION_HUES = {
    "job_scout": "#7ea6ff",
    "technical_analyst": "#36c6e0",
    "compliance_risk": "#c98bff",
    "application_strategist": "#f2b134",
    "synthesizer": "#4fd672",
    "salary_estimator": "#ff8f6b",
    "resume_tailor": "#ff5a8a",
    "cover_letter_writer": "#80d8c8",
}

STATIONS = [
    {"station_id": "job_scout",              "order": 0, "wave": 0},
    {"station_id": "technical_analyst",      "order": 1, "wave": 1},
    {"station_id": "compliance_risk",        "order": 2, "wave": 1},
    {"station_id": "application_strategist", "order": 3, "wave": 2},
    {"station_id": "synthesizer",            "order": 4, "wave": 3},
    {"station_id": "salary_estimator",       "order": 5, "wave": 4, "optional": True},
    {"station_id": "resume_tailor",          "order": 6, "wave": 4, "optional": True},
    {"station_id": "cover_letter_writer",    "order": 7, "wave": 4, "optional": True},
]

class RunState:
    def __init__(self):
        self.agent_map: dict[str, str] = {}
        self.agent_n = 0
        self.task_of: dict[str, str] = {}
        self.completed: dict[str, int] = {}
        self.tokens: dict[str, int] = {}
        self.energy: dict[str, float] = {}
        self.rev_a: dict[str, int] = {}
        self.rev_s: dict[str, int] = {}
        self.completed_total = 0
        self.failed_total = 0
        self.start_s = time.time()
        self.snapshot_sent = False

    def agent_for(self, sid: str) -> str:
        if sid not in self.agent_map:
            self.agent_n += 1
            self.agent_map[sid] = f"executor-{self.agent_n:02d}"
        return self.agent_map[sid]

    def rev_agent(self, aid: str) -> int:
        self.rev_a[aid] = self.rev_a.get(aid, 0) + 1
        return self.rev_a[aid]

    def rev_stn(self, sid: str) -> int:
        self.rev_s[sid] = self.rev_s.get(sid, 0) + 1
        return self.rev_s[sid]

    def task_id(self, sid: str) -> str:
        tid = f"t_{sid}_{next_seq() % 9999}"
        self.task_of[sid] = tid
        return tid


def make_snapshot(run_id: str | None = None, state: RunState | None = None) -> dict:
    agents = []
    for i in range(4):
        aid = f"executor-{i+1:02d}"
        agents.append({
            "agent_id": aid, "name": aid, "state": "docked",
            "energy": 88.0, "hold": False, "task_id": None,
            "station_id": None, "task_progress": 0, "eta_s": None,
            "completed_total": 0, "error": None, "rev": 0,
            "specialty": STATIONS[i % len(STATIONS)]["station_id"],
            "tokens_used": 0, "token_budget": 2048,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
    return {
        "config": {
            "stations": STATIONS,
            "energy_low_threshold": 22,
            "max_agents": 12,
            "token_budget_default": 2048,
            "run_id": run_id,
        },
        "agents": agents,
        "stations": [
            {"station_id": s["station_id"], "name": s["station_id"],
             "order": s["order"], "state": "idle", "worker_agent_id": None,
             "queue_depth": 0, "drain": False, "rev": 0}
            for s in STATIONS
        ],
        "tasks": [],
        "alerts": [],
        "metrics": {
            "tasks_per_min": 0, "completed_total": 0, "failed_total": 0,
            "success_rate": 1.0, "p95_ms": 0, "active_agents": 0,
            "total_agents": 4, "queued": 0,
            "uptime_s": 0, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }


def hue(sid: str) -> str:
    return STATION_HUES.get(sid, "#8a9da8")


def translate(ev: dict, state: RunState) -> list[dict]:
    out = []
    ts = ev.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def log(html: str):
        out.append(make_envelope("log.appended", {"html": html, "ts": ts}))

    etype = ev.get("event", "")

    if etype == "run.start":
        snap = make_snapshot(ev.get("run_id"), state)
        out.append(make_envelope("snapshot", snap))
        state.snapshot_sent = True
        log(f'<b style="color:#4fd672">RUN STARTED</b> · {ev.get("run_id","?")} · {ev.get("crew","jd_crew")}')

    elif etype == "plan.created":
        if not state.snapshot_sent:
            snap = make_snapshot(ev.get("run_id"), state)
            out.append(make_envelope("snapshot", snap))
            state.snapshot_sent = True
        waves = len((ev.get("plan") or {}).get("waves") or [])
        steps = len((ev.get("plan") or {}).get("steps") or [])
        log(f'<b style="color:#6c8cff">PLAN CREATED</b> · {waves} waves · {steps} steps')

    elif etype == "step.start":
        sid = ev.get("agent") or ev.get("step_id") or ev.get("owner") or "unknown"
        aid = state.agent_for(sid)
        tid = state.task_id(sid)
        for env in [
            make_envelope("task.enqueued", {
                "task_id": tid, "station_id": sid, "status": "queued",
                "priority": 100, "work_estimate_s": 5.0,
                "assigned_agent_id": None, "enqueued_at": ts, "rev": 0,
            }),
            make_envelope("task.assigned", {
                "task_id": tid, "station_id": sid, "status": "assigned",
                "priority": 100, "work_estimate_s": 5.0,
                "assigned_agent_id": aid, "enqueued_at": ts, "started_at": ts, "rev": 1,
            }),
            make_envelope("agent.updated", {
                "agent_id": aid, "name": aid, "state": "dispatched",
                "energy": state.energy.get(aid, 85.0), "hold": False,
                "task_id": tid, "station_id": sid, "task_progress": 0,
                "eta_s": 2.0, "completed_total": state.completed.get(aid, 0),
                "rev": state.rev_agent(aid), "specialty": sid,
                "tokens_used": state.tokens.get(aid, 0), "token_budget": 2048,
                "updated_at": ts,
            }),
            make_envelope("station.updated", {
                "station_id": sid, "name": sid, "state": "busy",
                "worker_agent_id": aid, "queue_depth": 0, "drain": False,
                "rev": state.rev_stn(sid),
            }),
        ]:
            out.append(env)
        log(f'<b style="color:#36c6e0">{aid}</b> → <b style="color:{hue(sid)}">{sid}</b> · {tid}')

    elif etype == "tool.call":
        sid = ev.get("step_id") or ev.get("agent") or ""
        log(f'<b style="color:#5f717c">tool</b> <span style="color:#8a9da8">{ev.get("tool","?")}</span>{" · " + sid if sid else ""}')

    elif etype == "step.complete":
        sid = ev.get("agent") or ev.get("step_id") or ev.get("owner") or "unknown"
        aid = state.agent_for(sid)
        tid = state.task_of.get(sid, "t_done")
        lat = ev.get("duration_ms") or 3000
        state.completed[aid] = state.completed.get(aid, 0) + 1
        state.tokens[aid] = state.tokens.get(aid, 0) + 2000
        state.energy[aid] = max(20.0, state.energy.get(aid, 85.0) - 8.0)
        state.completed_total += 1
        uptime = int(time.time() - state.start_s)
        total = state.completed_total + state.failed_total
        for env in [
            make_envelope("task.completed", {
                "task_id": tid, "station_id": sid, "status": "completed",
                "assigned_agent_id": aid, "enqueued_at": ts,
                "started_at": ts, "finished_at": ts,
                "result": {"ok": True}, "rev": 2,
            }),
            make_envelope("agent.updated", {
                "agent_id": aid, "name": aid, "state": "returning",
                "energy": state.energy[aid], "hold": False, "task_id": None,
                "station_id": None, "task_progress": 1.0, "eta_s": 1.0,
                "completed_total": state.completed[aid],
                "rev": state.rev_agent(aid), "specialty": sid,
                "tokens_used": state.tokens[aid], "token_budget": 2048,
                "updated_at": ts,
            }),
            make_envelope("station.updated", {
                "station_id": sid, "name": sid, "state": "idle",
                "worker_agent_id": None, "queue_depth": 0, "drain": False,
                "rev": state.rev_stn(sid),
            }),
            make_envelope("metrics.tick", {
                "tasks_per_min": state.completed_total,
                "completed_total": state.completed_total,
                "failed_total": state.failed_total,
                "success_rate": state.completed_total / total if total else 1.0,
                "p95_ms": lat, "active_agents": 1, "total_agents": 4,
                "queued": 0, "uptime_s": uptime,
                "ts": ts,
            }),
        ]:
            out.append(env)
        llm = f" · {ev['llm_calls']} LLM" if ev.get("llm_calls") is not None else ""
        tools = f" · {ev['tool_calls']} tools" if ev.get("tool_calls") is not None else ""
        ms = f" · {lat}ms" if lat else ""
        log(f'<b style="color:#4fd672">{aid}</b> ✓ <b style="color:{hue(sid)}">{sid}</b>{llm}{tools}{ms}')

    elif etype == "step.failed":
        sid = ev.get("agent") or ev.get("step_id") or ev.get("owner") or "unknown"
        aid = state.agent_for(sid)
        tid = state.task_of.get(sid, "t_fail")
        layer = ev.get("layer") or "criterion_failed"
        kind = "critical" if layer in ("budget_exceeded", "unsafe") else "warning"
        state.failed_total += 1
        for env in [
            make_envelope("task.failed", {
                "task_id": tid, "station_id": sid, "status": "failed",
                "assigned_agent_id": aid,
                "error": ev.get("error") or layer, "rev": 2,
            }),
            make_envelope("alert.raised", {
                "alert_id": f"al_{next_seq()}",
                "kind": kind, "title": f"{sid} step failed",
                "body": f"{ev.get('error') or layer} [layer: {layer}]",
                "target": {"type": "station", "id": sid},
                "raised_at": ts, "ttl_s": 8,
            }),
            make_envelope("station.updated", {
                "station_id": sid, "name": sid, "state": "idle",
                "worker_agent_id": None, "queue_depth": 0, "drain": False,
                "rev": state.rev_stn(sid),
            }),
        ]:
            out.append(env)
        log(f'<b style="color:#ff5a52">{aid}</b> ✗ <b style="color:{hue(sid)}">{sid}</b> · {ev.get("error") or layer}')

    elif etype == "retry":
        sid = ev.get("step_id") or ev.get("agent") or "?"
        attempt = ev.get("attempt", "?")
        out.append(make_envelope("alert.raised", {
            "alert_id": f"al_{next_seq()}", "kind": "warning",
            "title": f"{sid} retry #{attempt}",
            "body": ev.get("reason") or "step retry",
            "target": {"type": "station", "id": sid},
            "raised_at": ts, "ttl_s": 6,
        }))
        log(f'<b style="color:#f2b134">retry</b> {sid} attempt {attempt} · {ev.get("reason","") or ""}')

    elif etype == "critic.review":
        sid = ev.get("step_id") or "?"
        verdict = ev.get("verdict") or "?"
        col = "#4fd672" if verdict == "pass" else "#f2b134" if verdict == "fix" else "#ff5a52"
        repair = ev.get("suggested_repair") or ""
        log(f'<b style="color:#c98bff">critic</b> {sid} → <b style="color:{col}">{verdict}</b>{" · " + repair[:60] if repair else ""}')

    elif etype == "run.complete":
        s = ev.get("run_summary") or {}
        uptime = int(time.time() - state.start_s)
        total = state.completed_total + state.failed_total
        out.append(make_envelope("metrics.tick", {
            "tasks_per_min": state.completed_total,
            "completed_total": state.completed_total,
            "failed_total": state.failed_total,
            "success_rate": state.completed_total / total if total else 1.0,
            "p95_ms": s.get("p95_ms") or 0,
            "active_agents": 0, "total_agents": 4, "queued": 0,
            "uptime_s": uptime, "ts": ts,
        }))
        wall = f" · {s['wall_ms']}ms" if s.get("wall_ms") is not None else ""
        log(f'<b style="color:#4fd672">RUN COMPLETE</b> · {s.get("steps","?")} steps · {s.get("llm_calls","?")} LLM{wall}')

    else:
        if etype:
            sid = ev.get("step_id") or ""
            log(f'<span style="color:#5f717c">{etype}</span>{" · " + sid if sid else ""}')

    return out


# ── Diff overlay: diff_runs.py output → console envelopes ─────────────────────
# Renders a cross-run regression diff (scripts/diff_runs.py) onto the same
# console, reusing the existing envelope vocabulary: a flagged agent becomes a
# station-targeted alert (red = regression, amber = warning/inconclusive) — the
# same ring mechanism step.failed uses — plus a readable log of every signal.
STATION_IDS = {s["station_id"] for s in STATIONS}
DIFF_KIND = {"regression": "critical", "warning": "warning",
             "inconclusive": "warning", "info": "warning"}
DIFF_COLOR = {"pass": "#4fd672", "warning": "#f2b134",
              "regression": "#ff5a52", "inconclusive": "#8a9da8", "info": "#4fd672"}


def diff_to_envelopes(diff: dict) -> list[dict]:
    """Translate a diff_runs envelope into console envelopes (snapshot first)."""
    out: list[dict] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def log(html: str):
        out.append(make_envelope("log.appended", {"html": html, "ts": ts}))

    out.append(make_envelope("snapshot", make_snapshot(diff.get("candidate") or "diff")))

    verdict = diff.get("verdict") or "pass"
    log(f'<b style="color:{DIFF_COLOR.get(verdict, "#8a9da8")}">RUN DIFF · {verdict.upper()}</b>')
    log(f'<span style="color:#8a9da8">baseline</span> {diff.get("baseline", "?")}')
    log(f'<span style="color:#8a9da8">candidate</span> {diff.get("candidate", "?")}')
    s = diff.get("summary") or {}
    if s:
        log('changed: '
            f'{", ".join(s.get("changed_agents") or []) or "(none)"} · '
            f'blocker fails: {s.get("new_blocker_failures", 0)} · '
            f'schema regs: {s.get("schema_regressions", 0)} · '
            f'Δllm {s.get("cost_delta_llm_calls", 0):+d} · '
            f'Δtool {s.get("cost_delta_tool_calls", 0):+d}')

    for d in diff.get("agent_diffs", []):
        sid = d.get("agent") or "?"
        sev = d.get("severity") or "info"
        contract = d.get("contract")
        signals = d.get("signals") or []
        body = ("; ".join(sig.get("message", "") for sig in signals)) or sev
        if contract:
            body = f"[{contract}] {body}"
        if d.get("recommended_next_action"):
            body += f" — next: {d['recommended_next_action']}"
        target = {"type": "station", "id": sid} if sid in STATION_IDS else {"type": "port"}
        if sev in ("regression", "warning", "inconclusive"):
            out.append(make_envelope("alert.raised", {
                "alert_id": f"al_diff_{next_seq()}",
                "kind": DIFF_KIND.get(sev, "warning"),
                "title": f"{sid} · {sev}",
                "body": body,
                "target": target,
                "raised_at": ts, "ttl_s": 600,
            }))
            if sid in STATION_IDS:
                out.append(make_envelope("station.updated", {
                    "station_id": sid, "name": sid,
                    "state": "drain" if sev == "regression" else "busy",
                    "worker_agent_id": None, "queue_depth": 0,
                    "drain": sev == "regression", "rev": 1,
                }))
        col = DIFF_COLOR.get(sev, "#8a9da8")
        for sig in signals:
            log(f'<b style="color:{col}">{sig.get("kind", "?")}</b> · '
                f'<b style="color:{hue(sid)}">{sid}</b> · {sig.get("message", "")}')
    if not diff.get("agent_diffs"):
        log('<span style="color:#4fd672">no differences detected</span>')
    return out


def load_or_compute_diff(diff_file: str | None,
                         baseline: str | None, candidate: str | None) -> dict:
    """A precomputed diff JSON (--diff), or compute one via scripts/diff_runs.py."""
    if diff_file:
        return json.loads(Path(diff_file).read_text(encoding="utf-8"))
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import diff_runs  # type: ignore
    return diff_runs.diff_runs(diff_runs.Run(Path(baseline)), diff_runs.Run(Path(candidate)))


# ── File tailer ───────────────────────────────────────────────────────────────
def tail_log(log_path: Path, state: RunState, stop_event: threading.Event):
    """Tail a JSONL run log and broadcast translated envelopes."""
    print(f"[bridge] Tailing {log_path}", flush=True)
    with open(log_path, "r") as fh:
        # Send snapshot first
        snap = make_snapshot(log_path.parent.name, state)
        broadcast(make_envelope("snapshot", snap))
        state.snapshot_sent = True

        # Replay existing content
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            for env in translate(ev, state):
                broadcast(env)

        # Tail new lines
        while not stop_event.is_set():
            line = fh.readline()
            if not line:
                time.sleep(0.1)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            for env in translate(ev, state):
                broadcast(env)


def find_latest_run(runs_dir: Path) -> Optional[Path]:
    """Return the run.log of the most-recently modified run in runs_dir."""
    candidates = []
    for d in runs_dir.iterdir():
        if not d.is_dir():
            continue
        for name in ("run.log", "run_log.jsonl"):
            f = d / name
            if f.exists():
                candidates.append((f.stat().st_mtime, f))
    if not candidates:
        return None
    return max(candidates)[1]


# ── HTTP + SSE handler ────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence default access log

    def do_GET(self):
        if self.path == "/events" or self.path.startswith("/events?"):
            self._handle_sse()
        elif self.path in ("/", "/index.html"):
            self._serve_console()
        elif self._serve_static(self.path.split("?", 1)[0]):
            pass
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _serve_static(self, url_path: str) -> bool:
        """Serve sibling assets (support.js, feed_adapter.js, …) from this
        script's directory so the console loads standalone from this server.
        Returns True if handled."""
        import mimetypes
        from urllib.parse import unquote
        here = Path(__file__).resolve().parent
        name = unquote(url_path.lstrip("/"))
        target = (here / name).resolve()
        if here not in target.parents or not target.is_file():   # no traversal
            return False
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)
        return True

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()

        queue = []
        with _subscribers_lock:
            # Atomically snapshot history + register, so no envelope is both
            # replayed and queued (the lock blocks broadcast() in between).
            backlog = list(_history)
            _subscribers.append(queue)

        try:
            for data in backlog:          # replay history first
                self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()
            while True:
                if queue:
                    data = queue.pop(0)
                    msg = f"data: {data}\n\n"
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                else:
                    # heartbeat
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _subscribers_lock:
                if queue in _subscribers:
                    _subscribers.remove(queue)

    def _serve_console(self):
        # Try to find the console HTML relative to this script
        here = Path(__file__).parent
        candidates = [
            here / "Roboport Ops Console.dc.html",
            here.parent / "Roboport Ops Console.dc.html",
        ]
        for p in candidates:
            if p.exists():
                body = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Console HTML not found. Serve it separately.")


# ── Compatibility layer for serve.py ──────────────────────────────────────────
# serve.py is an alternative server (run list, replay pacing, --watch, landing
# page) written against a slightly different bridge API than the one above.
# These thin wrappers expose that API over the functions already defined here,
# so `serve.py` imports cleanly and both servers share one translator.

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# serve.py imports `envelope`; it's our make_envelope.
envelope = make_envelope

def build_snapshot(run_id: str | None = None) -> dict:
    """serve.py alias for make_snapshot."""
    return make_snapshot(run_id)

# A shared RunState for serve.py's --watch stream (one growing log → one state).
_watch_state = RunState()

def _single_event(ev: dict, run_dir=None) -> list[dict]:
    """Translate one run_log event against the shared watch state (serve.py)."""
    return translate(ev, _watch_state)

def _find_run_log(run_dir) -> Optional[Path]:
    run_dir = Path(run_dir)
    for name in ("run.log", "run_log.jsonl"):
        p = run_dir / name
        if p.exists():
            return p
    return None

def convert_run(run_dir) -> list[dict]:
    """Read a finished run dir and return the full envelope list (snapshot first),
    for serve.py's replay / /api/run endpoints."""
    run_dir = Path(run_dir)
    state = RunState()
    out: list[dict] = [make_envelope("snapshot", make_snapshot(run_dir.name, state))]
    state.snapshot_sent = True
    log = _find_run_log(run_dir)
    if log:
        for raw in log.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            out.extend(translate(ev, state))
    return out


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ROBOPORT Ops Console bridge server")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--runs-dir", help="Directory containing run subdirectories")
    group.add_argument("--run-id",   help="Specific run ID under runs/")
    group.add_argument("--log-file", help="Path to a run_log.jsonl / run.log file")
    group.add_argument("--diff", help="Render a precomputed diff_runs JSON as a regression overlay")
    parser.add_argument("--baseline", help="Baseline run dir (with --candidate: compute + render a diff)")
    parser.add_argument("--candidate", help="Candidate run dir (with --baseline: compute + render a diff)")
    parser.add_argument("--port", type=int, default=4242, help="HTTP port (default 4242)")
    parser.add_argument("--runs-base", default="runs", help="Base runs directory (default: runs)")
    args = parser.parse_args()

    # ── Diff overlay mode: render a regression comparison, then serve it. ──
    if args.diff or (args.baseline and args.candidate):
        try:
            diff = load_or_compute_diff(args.diff, args.baseline, args.candidate)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            sys.exit(f"[bridge] could not load diff: {e}")
        for env in diff_to_envelopes(diff):
            broadcast(env)
        server = HTTPServer(("0.0.0.0", args.port), Handler)
        print(f"[bridge] Diff overlay · verdict={diff.get('verdict')} · "
              f"{len(diff.get('agent_diffs', []))} agent diff(s)", flush=True)
        print(f"[bridge] Listening on http://localhost:{args.port}", flush=True)
        print(f"[bridge] Open: Roboport Ops Console.dc.html?api=http://localhost:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[bridge] Stopped.")
        return

    log_path: Optional[Path] = None

    if args.log_file:
        log_path = Path(args.log_file)
    elif args.run_id:
        base = Path(args.runs_base)
        for name in ("run.log", "run_log.jsonl"):
            p = base / args.run_id / name
            if p.exists():
                log_path = p
                break
        if not log_path:
            sys.exit(f"[bridge] No log found for run {args.run_id} in {args.runs_base}/")
    elif args.runs_dir:
        log_path = find_latest_run(Path(args.runs_dir))
        if not log_path:
            sys.exit(f"[bridge] No run logs found in {args.runs_dir}")
    else:
        # Default: look for runs/ next to this script
        runs = Path(__file__).parent.parent / "runs"
        if runs.exists():
            log_path = find_latest_run(runs)
        if not log_path:
            print("[bridge] No run logs found. Starting in snapshot-only mode.")

    state = RunState()
    stop = threading.Event()

    if log_path:
        t = threading.Thread(target=tail_log, args=(log_path, state, stop), daemon=True)
        t.start()

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[bridge] Listening on http://localhost:{args.port}", flush=True)
    print(f"[bridge] Open: Roboport Ops Console.dc.html?api=http://localhost:{args.port}", flush=True)
    if log_path:
        print(f"[bridge] Tailing: {log_path}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        stop.set()
        print("\n[bridge] Stopped.")


if __name__ == "__main__":
    main()
