"""
ROBOPORT — replay page generator
================================
Bakes a captured runtime-feed envelope stream into a single self-contained HTML
that plays a real crew run with NO backend — the dashboard's render layer
(`web/roboport-feed.html` PART C) is reused untouched; only the feed *source* is
swapped for a looped replay of the captured envelopes.

Why: a public, shareable demo of the runtime-native feed that runs anywhere —
double-click locally, or drop on any static host (Vercel/Netlify/Pages) for a
URL. No server, no egress, no model.

Usage
-----
Capture live from a running server, then write the page::

    # terminal 1
    ROBOPORT_FEED_SOURCE=runtime-demo ROBOPORT_SERVE_WEB=1 python server.py
    # terminal 2  (pip install websockets)
    python make_replay.py --from http://localhost:8000 --seconds 58 \\
        --out ../web/roboport-replay.html

Or bake from a previously saved capture (the {snapshot, deltas} JSON)::

    python make_replay.py --capture capture.json --out ../web/roboport-replay.html
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEED_HTML = HERE.parent / "web" / "roboport-feed.html"

_BACKEND_JS = """
/* ===== REPLAY BACKEND (baked real runtime-feed crew run; no server) =====
   Same interface createFeed() expects (subscribe/getSnapshot/handleCommand/tick).
   Plays the captured snapshot + seq-ordered deltas on their original timeline,
   then loops. The render layer (PART C) is a pure consumer and is unchanged. */
window.__REPLAY__ = __REPLAY_JSON__;
function createReplayBackend(R){
  let subs=[]; const snap=R.snapshot, deltas=R.deltas;
  let clock=0, idx=0, started=false, last=(deltas.length?deltas[deltas.length-1].t:0);
  const emit=env=>{ for(const s of subs) s(env); };
  function reset(){ idx=0; clock=0; emit(snap); }
  return {
    CONFIG: snap.data.config,
    subscribe(cb){ subs.push(cb); },
    getSnapshot(){ emit(snap); started=true; },
    handleCommand(cmd){ return {command_id:cmd.command_id, status:"accepted"}; }, // inert in replay
    tick(dt){
      if(!started) return;
      clock += dt*1000;
      while(idx < deltas.length && deltas[idx].t <= clock){ emit(deltas[idx].e); idx++; }
      if(idx >= deltas.length && clock > last + 1800){ reset(); }   // loop with a short beat
    }
  };
}
"""

_BOOT_OLD = """  const backend = LIVE ? null : createMockBackend();
  const feed = LIVE ? createLiveFeed(FEED_BASE, FEED_SCOPE) : createFeed(backend);"""
_BOOT_NEW = """  const backend = createReplayBackend(window.__REPLAY__);   // baked crew replay
  const feed = createFeed(backend);"""


def capture_live(base: str, seconds: float, scope: str = "all") -> dict:
    import asyncio
    import time
    import websockets  # type: ignore

    async def run() -> dict:
        uri = base.replace("http", "ws", 1).rstrip("/") + f"/api/feed?scope={scope}"
        async with websockets.connect(uri, max_size=2 ** 22) as ws:
            snapshot = json.loads(await ws.recv())
            t0 = time.time()
            deltas = []
            while time.time() - t0 < seconds:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                except asyncio.TimeoutError:
                    break
                deltas.append({"t": round((time.time() - t0) * 1000), "e": m})
        return {"snapshot": snapshot, "deltas": deltas}

    return asyncio.run(run())


def build(capture: dict, base_html: Path) -> str:
    src = base_html.read_text(encoding="utf-8")
    if "function createFeed(backend){" not in src or _BOOT_OLD not in src:
        raise SystemExit("base HTML doesn't match expected feed.html structure")
    inject = _BACKEND_JS.replace("__REPLAY_JSON__", json.dumps(capture, separators=(",", ":")))
    src = src.replace("function createFeed(backend){",
                      inject + "\nfunction createFeed(backend){", 1)
    src = src.replace(_BOOT_OLD, _BOOT_NEW, 1)
    src = src.replace("<title>ROBOPORT — feed-driven (mock backend)</title>",
                      "<title>ROBOPORT — runtime crew (replay)</title>")
    src = src.replace("wire · /api/feed", "wire · runtime crew · replay")
    return src


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from", dest="origin", help="capture live from a server origin")
    g.add_argument("--capture", help="use a saved {snapshot,deltas} JSON file")
    ap.add_argument("--seconds", type=float, default=58.0)
    ap.add_argument("--scope", default="all")
    ap.add_argument("--base", default=str(FEED_HTML))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cap = (capture_live(args.origin, args.seconds, args.scope)
           if args.origin else json.loads(Path(args.capture).read_text()))
    html = build(cap, Path(args.base))
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out}  ({len(html)} bytes, {len(cap['deltas'])} deltas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
