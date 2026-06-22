"""
ROBOPORT — feed server  (FastAPI)
=================================
Mounts the contract endpoints on top of the collector:

    GET  /api/state?scope=…   -> one scope-shaped snapshot (cold load / resync)
    WS   /api/feed?scope=…     -> snapshot on connect, then seq-ordered deltas
                                  filtered to that scope
    POST /api/commands         -> idempotent command envelope; ack only,
                                  the state change comes back through the feed

SCOPE
-----
`scope` is "network" (zone rollups + routes + handoffs only) or "zone:<id>"
(that zone's full firehose). Each connection subscribes to ONE scope, so
bandwidth stays flat as zones multiply — the same model the in-browser mock
proves. The collector must expose `snapshot(scope)` and tag zone-bound
envelopes with `data.zone_id`; routes/handoffs need no tag.

SEQ
---
Deltas are stamped with a PER-CONNECTION counter (not the global plane seq),
because a scoped client deliberately never sees the envelopes its scope
dropped. That keeps every client's stream contiguous, so the artifact's gap
detector (`seq !== last+1 -> resync`) works unchanged.

Going from the in-browser mock to this is the LIVE flip in the artifact's
createLiveFeed() (see README.md).

Run:
    pip install docker fastapi "uvicorn[standard]"
    python server.py            # serves on http://localhost:8000
"""

from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="ROBOPORT feed")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---- feed source selection (env-driven; default Docker) -----------------
# ROBOPORT_FEED_SOURCE = docker | runtime | runtime-demo
#   docker        -> collector.py (the Docker daemon; requires docker-py)
#   runtime       -> runtime_feed.py tailing ROBOPORT_FEED_GLOB (a real crew run)
#   runtime-demo  -> runtime_feed.py running a synthetic crew (no model, no deps)
# Imports are lazy so runtime mode never needs docker-py installed.
_SOURCE = os.environ.get("ROBOPORT_FEED_SOURCE", "docker")


def _make_collector():
    if _SOURCE in ("runtime", "runtime-demo"):
        from runtime_feed import RuntimeCrewCollector
        registry = os.environ.get(
            "ROBOPORT_REGISTRY",
            str(Path(__file__).resolve().parents[2] / "agents" / "registry.json"))
        return RuntimeCrewCollector(
            registry_path=registry,
            crew=os.environ.get("ROBOPORT_CREW", "jd_crew"),
            feed_glob=os.environ.get("ROBOPORT_FEED_GLOB"),
            simulate=(_SOURCE == "runtime-demo"))
    from collector import DockerCollector
    return DockerCollector()


collector = _make_collector()
_loop: Optional[asyncio.AbstractEventLoop] = None
_clients: "Set[_Client]" = set()


class _Client:
    """One feed connection: its own scope + monotonic seq counter."""
    def __init__(self, scope: str) -> None:
        self.q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.scope = scope
        self.seq = 0


# ---- which envelopes a scope subscribes to (mirrors the mock's relevant()) ----
_ALWAYS = ("snapshot", "metrics.tick", "alert.raised", "log.appended")
_NETWORK = ("zone.updated", "route.updated", "task.handoff")
# Single-plane firehose: forward everything. Used by the single-host surfaces
# (the runtime crew feed has no zones to roll up).
_FIREHOSE = ("all", "crew", "host")

def _relevant(env: dict, scope: str) -> bool:
    t = env.get("type", "")
    if scope in _FIREHOSE:
        return True
    if t in _ALWAYS:
        return True
    if scope == "network":
        return t in _NETWORK
    if scope.startswith("zone:"):
        return (env.get("data") or {}).get("zone_id") == scope[5:]
    return False


def _snapshot(scope: str) -> dict:
    """Scope-shaped snapshot. The scope-aware collector implements snapshot(scope);
    a plain single-host collector ignores the arg."""
    try:
        return collector.snapshot(scope)
    except TypeError:
        return collector.snapshot()


def _deliver(c: "_Client", env: dict) -> None:
    """Runs ON the event loop -> per-client seq stamping is race-free."""
    c.seq += 1
    out = dict(env)
    out["seq"] = c.seq
    try:
        c.q.put_nowait(out)
    except asyncio.QueueFull:
        pass  # slow consumer; it will gap-detect and resync on next snapshot


def _fanout(env: dict) -> None:
    """Called from the collector's background threads -> hop to the asyncio loop."""
    if _loop is None:
        return
    for c in list(_clients):
        if _relevant(env, c.scope):
            _loop.call_soon_threadsafe(_deliver, c, env)


@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    collector.subscribe(_fanout)
    collector.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    collector.stop()


@app.get("/api/state")
async def get_state(scope: str = "network") -> JSONResponse:
    snap = _snapshot(scope)
    ts = (snap.get("metrics") or {}).get("ts")
    return JSONResponse({"v": 1, "seq": 0, "ts": ts, "type": "snapshot", "data": snap})


@app.post("/api/commands")
async def post_command(cmd: dict) -> JSONResponse:
    # NOTE: enforce auth (Entra bearer) + per-operator audit here in production.
    ack = collector.handle_command(cmd)
    return JSONResponse(ack)


@app.websocket("/api/feed")
async def feed(ws: WebSocket) -> None:
    # NOTE: validate ?token= here in production.
    scope = ws.query_params.get("scope", "network")
    await ws.accept()
    c = _Client(scope)
    _clients.add(c)
    try:
        # snapshot first (seq 0), so a fresh client is immediately consistent;
        # deltas for this connection then stream as seq 1, 2, 3, …
        snap = _snapshot(scope)
        ts = (snap.get("metrics") or {}).get("ts")
        await ws.send_text(json.dumps({"v": 1, "seq": 0, "ts": ts,
                                       "type": "snapshot", "data": snap}))
        while True:
            env = await c.q.get()
            await ws.send_text(json.dumps(env))
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(c)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
