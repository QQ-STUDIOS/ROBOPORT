#!/usr/bin/env python3
"""
ROBOPORT Dashboard Server
=========================
Lightweight HTTP + SSE server that bridges the ROBOPORT runner to the
Ops Console in your browser.  Zero external dependencies — stdlib only.

Usage
-----
# Serve the console and stream the latest completed run:
python dashboard/serve.py

# Point at a specific run directory:
python dashboard/serve.py --run runs/abc123

# Watch for a live run (polls run.log as benchmark.py writes to it):
python dashboard/serve.py --run runs/abc123 --watch

# Change port (default 7474):
python dashboard/serve.py --port 8080

Then open the console at:
  http://localhost:5500/Roboport%20Ops%20Console.dc.html?api=http://localhost:7474
or just open:
  http://localhost:7474/
(the server also serves the console HTML directly)

Endpoints
---------
GET /                   → serves dashboard/index.html (redirect to console)
GET /api/snapshot       → JSON snapshot of the selected run
GET /api/feed           → SSE stream of all events from the run
GET /api/runs           → JSON list of available run directories
GET /api/run/<run_id>   → JSON full event array for a specific run
POST /api/command       → forward a command to the mock backend (no-op stub)

CORS
----
All responses carry Access-Control-Allow-Origin: * so the console served
from a different port (e.g. a VS Code Live Server on :5500) can connect.

Live mode
---------
When --watch is passed, the server polls the run directory's run.log file
every 300 ms and emits new events to all connected SSE clients as they appear.
This gives you a live view while benchmark.py is running.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

# Make bridge importable when running from repo root or dashboard/
_THIS = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS))
from bridge import convert_run, build_snapshot, STATIONS, _now_iso, envelope  # noqa: E402

REPO_ROOT = _THIS.parent
RUNS_DIR  = REPO_ROOT / "runs"
DEFAULT_PORT = 7474

# ---------------------------------------------------------------------------
# SSE client registry
# ---------------------------------------------------------------------------

_clients: list[Any] = []   # list of wfile handles
_clients_lock = threading.Lock()


def _broadcast(event: dict) -> None:
    payload = f"data: {json.dumps(event, default=str)}\n\n"
    dead = []
    with _clients_lock:
        for wf in list(_clients):
            try:
                wf.write(payload.encode())
                wf.flush()
            except (BrokenPipeError, OSError):
                dead.append(wf)
        for wf in dead:
            _clients.remove(wf)


def _add_client(wf: Any) -> None:
    with _clients_lock:
        _clients.append(wf)


def _remove_client(wf: Any) -> None:
    with _clients_lock:
        if wf in _clients:
            _clients.remove(wf)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_current_run_dir: Path | None = None
_cached_events:   list[dict]  = []
_watch_mode:      bool        = False
_watch_seen:      int         = 0
_watch_agent_map: dict[str, str] = {}
_watch_aid_n:     int         = 0


def _latest_run() -> Path | None:
    """Return the most recently modified run directory under runs/."""
    if not RUNS_DIR.is_dir():
        return None
    runs = [p for p in RUNS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if not runs:
        return None
    return max(runs, key=lambda p: p.stat().st_mtime)


def _load_run(run_dir: Path) -> list[dict]:
    global _cached_events
    try:
        _cached_events = convert_run(run_dir)
        print(f"[serve] loaded {len(_cached_events)} events from {run_dir.name}", flush=True)
    except Exception as e:
        print(f"[serve] ERROR loading {run_dir}: {e}", flush=True)
        _cached_events = []
    return _cached_events


# ---------------------------------------------------------------------------
# Watch thread — polls run.log and broadcasts new events
# ---------------------------------------------------------------------------

def _watch_thread(run_dir: Path) -> None:
    global _watch_seen, _watch_agent_map, _watch_aid_n
    from bridge import _single_event  # type: ignore
    log_path = run_dir / "run.log"
    print(f"[serve] watching {log_path} …", flush=True)

    # emit initial snapshot
    _broadcast(envelope("snapshot", build_snapshot(run_dir.name)))

    while True:
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
            for raw in lines[_watch_seen:]:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for feed_ev in _single_event(ev, run_dir):
                    _broadcast(feed_ev)
                _watch_seen += 1
        time.sleep(0.3)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
        pass

    def _send(self, status: int, content_type: str, body: bytes | str) -> None:
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, status: int = 200) -> None:
        self._send(status, "application/json", json.dumps(data, default=str))

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        # ── / → landing page ─────────────────────────────────────────────────
        if path in ("", "/"):
            body = _landing_html()
            self._send(200, "text/html; charset=utf-8", body)
            return

        # ── /api/runs ─────────────────────────────────────────────────────────
        if path == "/api/runs":
            runs: list[dict] = []
            if RUNS_DIR.is_dir():
                for p in sorted(RUNS_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
                    if p.is_dir():
                        runs.append({
                            "run_id":   p.name,
                            "mtime":    datetime.fromtimestamp(p.stat().st_mtime,
                                                               tz=timezone.utc).isoformat(),
                            "has_log":  (p / "run.log").exists(),
                            "has_plan": (p / "plan.json").exists(),
                            "has_final":(p / "final_output.json").exists()
                                        or (p / "final.json").exists(),
                        })
            self._send_json({"runs": runs})
            return

        # ── /api/snapshot ─────────────────────────────────────────────────────
        if path == "/api/snapshot":
            qs    = parse_qs(parsed.query)
            rid   = (qs.get("run") or [""])[0]
            rdir  = (RUNS_DIR / rid) if rid else _current_run_dir
            if rdir and rdir.is_dir():
                evts  = _load_run(rdir)
                # Return the data from the first snapshot envelope
                snap  = next((e["data"] for e in evts if e["type"] == "snapshot"), None)
                self._send_json(snap or build_snapshot(rdir.name))
            else:
                self._send_json(build_snapshot("no-run"))
            return

        # ── /api/run/<run_id> ─────────────────────────────────────────────────
        if path.startswith("/api/run/"):
            rid  = path[len("/api/run/"):]
            rdir = RUNS_DIR / rid
            if not rdir.is_dir():
                self._send_json({"error": f"run {rid!r} not found"}, 404)
                return
            evts = convert_run(rdir)
            self._send_json(evts)
            return

        # ── /api/feed  (SSE) — also answer /events so the console's ?api= seam
        #    (feed_adapter.js connects to <api>/events) works against this server.
        if path in ("/api/feed", "/events"):
            self.send_response(200)
            self.send_header("Content-Type",  "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection",    "keep-alive")
            for k, v in CORS_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()

            if _watch_mode:
                # Watch mode: keep the connection alive; _watch_thread broadcasts
                _add_client(self.wfile)
                try:
                    while True:
                        # heartbeat comment to keep connection alive
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        time.sleep(15)
                except (BrokenPipeError, OSError, ConnectionResetError):
                    pass
                finally:
                    _remove_client(self.wfile)
            else:
                # Replay mode: stream all cached events then close
                evts = _cached_events or []
                for ev in evts:
                    try:
                        payload = f"data: {json.dumps(ev, default=str)}\n\n"
                        self.wfile.write(payload.encode())
                        self.wfile.flush()
                        time.sleep(0.02)   # 20 ms pacing — smooth animation
                    except (BrokenPipeError, OSError):
                        break
            return

        # ── /api/station-config ───────────────────────────────────────────────
        if path == "/api/station-config":
            self._send_json({"stations": STATIONS})
            return

        # ── 404 ──────────────────────────────────────────────────────────────
        self._send_json({"error": f"not found: {path}"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/")

        if path == "/api/command":
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            try:
                cmd = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"status": "rejected", "reason": "invalid JSON"}, 400)
                return
            # Stub — echo acceptance; wire to real Orchestrator when ready
            print(f"[serve] command: {cmd.get('type', '?')} target={cmd.get('target', {})}", flush=True)
            self._send_json({"command_id": cmd.get("command_id", "?"), "status": "accepted"})
            return

        self._send_json({"error": f"not found: {path}"}, 404)


# ---------------------------------------------------------------------------
# Landing page HTML
# ---------------------------------------------------------------------------

def _landing_html() -> str:
    run_name = _current_run_dir.name if _current_run_dir else "no run loaded"
    mode_tag = "👁 watch" if _watch_mode else "▶ replay"
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ROBOPORT Dashboard Server</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0d1318;color:#e8f1f5;font-family:'JetBrains Mono',monospace;padding:48px;line-height:1.6}}
  h1{{font-size:22px;color:#7ea6ff;margin-bottom:8px;letter-spacing:1px}}
  .sub{{color:#5f717c;font-size:12px;margin-bottom:36px}}
  .card{{background:#111c24;border:1px solid #1e2d38;border-radius:6px;padding:24px;margin-bottom:20px;max-width:680px}}
  .card h2{{font-size:12px;letter-spacing:1px;color:#36c6e0;text-transform:uppercase;margin-bottom:12px}}
  a{{color:#7ea6ff;text-decoration:none}}a:hover{{text-decoration:underline}}
  .tag{{display:inline-block;font-size:10px;padding:2px 8px;border-radius:3px;border:1px solid #1e2d38;color:#8a9da8;margin-right:6px}}
  .tag.green{{color:#4fd672;border-color:#1a3d28}}
  .tag.cyan{{color:#36c6e0;border-color:#12323b}}
  code{{background:#0a1015;padding:2px 6px;border-radius:3px;font-size:12px;color:#c98bff}}
  pre{{background:#0a1015;padding:16px;border-radius:6px;font-size:11px;color:#8a9da8;overflow-x:auto;margin-top:8px}}
</style>
</head>
<body>
<h1>ROBOPORT · Ops Console Server</h1>
<div class="sub">Serving on port {DEFAULT_PORT} &nbsp;·&nbsp; <span class="tag {'green' if _watch_mode else 'cyan'}">{mode_tag}</span> <span class="tag">{run_name}</span></div>

<div class="card">
  <h2>Open Console</h2>
  <p>Open the Ops Console and connect it to this server by appending <code>?api=http://localhost:{DEFAULT_PORT}</code>:</p>
  <pre><a href="http://localhost:5500/Roboport%20Ops%20Console.dc.html?api=http://localhost:{DEFAULT_PORT}" target="_blank">http://localhost:5500/Roboport%20Ops%20Console.dc.html?api=http://localhost:{DEFAULT_PORT}</a></pre>
  <p style="margin-top:10px;font-size:11px;color:#5f717c">Adjust the port if your Live Server is on a different one.</p>
</div>

<div class="card">
  <h2>API Endpoints</h2>
  <p><a href="/api/runs">/api/runs</a> — list available run directories</p>
  <p><a href="/api/snapshot">/api/snapshot</a> — current run snapshot</p>
  <p>/api/feed — SSE event stream (connect from the console)</p>
  <p>/api/station-config — station registry JSON</p>
</div>

<div class="card">
  <h2>Quick Start</h2>
  <pre>
# Replay a finished run
python dashboard/serve.py --run runs/&lt;run_id&gt;

# Watch a live run as benchmark.py writes it
python dashboard/serve.py --run runs/&lt;run_id&gt; --watch

# Use the latest run automatically
python dashboard/serve.py
</pre>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _current_run_dir, _watch_mode, _cached_events

    ap = argparse.ArgumentParser(description="ROBOPORT Ops Console dashboard server")
    ap.add_argument("--run",   default=None, help="Path to runs/<run_id>/ to serve")
    ap.add_argument("--watch", action="store_true",
                    help="Watch run.log as it grows (live run mode)")
    ap.add_argument("--port",  type=int, default=DEFAULT_PORT,
                    help=f"Port to listen on (default {DEFAULT_PORT})")
    args = ap.parse_args()

    # Resolve run directory
    if args.run:
        rd = Path(args.run)
        if not rd.is_dir():
            sys.exit(f"Run directory not found: {rd}")
        _current_run_dir = rd.resolve()
    else:
        _current_run_dir = _latest_run()
        if not _current_run_dir:
            print("[serve] No run directories found under runs/ — serving in demo mode")

    _watch_mode = args.watch

    # Preload events (non-watch mode) or start watcher thread
    if _current_run_dir:
        if _watch_mode:
            t = threading.Thread(target=_watch_thread, args=(_current_run_dir,), daemon=True)
            t.start()
        else:
            _load_run(_current_run_dir)
    else:
        # No run — just emit empty snapshot
        _cached_events = [envelope("snapshot", build_snapshot("demo"))]

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", args.port), Handler)
    mode_str = f"watch · {_current_run_dir.name}" if _watch_mode else \
               (f"replay · {_current_run_dir.name}" if _current_run_dir else "demo")
    print(f"[serve] http://localhost:{args.port}/  [{mode_str}]", flush=True)
    print(f"[serve] Console URL: http://localhost:5500/"
          f"Roboport%20Ops%20Console.dc.html?api=http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
