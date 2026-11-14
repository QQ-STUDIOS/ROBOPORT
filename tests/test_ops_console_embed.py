"""The design's Ops Console is embedded in the Launch Pad Fleet view without
weakening the station's rules: no eval, no CDN, no network from the frame,
framing only by the Launch Pad, and input only from gateway observations."""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DASH = REPO / "dashboard"
sys.path.insert(0, str(DASH))
sys.path.insert(0, str(REPO / "scripts"))
import bridge  # noqa: E402


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    headers = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, headers, body


def directives(csp):
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in csp.split(";")) if d}


def test_ops_console_page_policy(server):
    status, headers, body = get(server, "/opsconsole")
    assert status == 200
    csp = directives(headers["content-security-policy"])
    assert csp["script-src"] == ["'self'"]
    assert csp["connect-src"] == ["'none'"]
    assert csp["default-src"] == ["'none'"]
    assert csp["frame-ancestors"] == ["'self'"]
    assert headers["x-frame-options"] == "SAMEORIGIN"
    html = body.decode("utf-8")
    assert '<script src="/assets/opsconsole.js" defer></script>' in html
    assert "<script>" not in html and "data-dc-script" not in html
    assert "http://" not in html and "https://" not in html


def test_console_build_reproduces_shipped_assets(tmp_path):
    subprocess.run([sys.executable, str(REPO / 'scripts/build_opsconsole.py'),
                    str(DASH / 'Roboport Ops Console.dc.html'), str(tmp_path)], check=True)
    for path in ('opsconsole.html', 'assets/opsconsole.css', 'assets/opsconsole.js'):
        assert (tmp_path / path).read_bytes() == (DASH / path).read_bytes(), path


def test_launch_pad_page_stays_unframeable(server):
    status, headers, _ = get(server, "/")
    assert status == 200
    csp = directives(headers["content-security-policy"])
    assert csp["frame-ancestors"] == ["'none'"]
    assert csp["script-src"] == ["'self'"]


@pytest.mark.parametrize("asset", ["assets/opsconsole.js", "assets/opsconsole.css", "assets/opsfeed.js"])
def test_console_assets_are_served(server, asset):
    status, _, body = get(server, "/" + asset)
    assert status == 200 and body


def test_console_script_needs_no_eval_or_network():
    js = (DASH / "assets" / "opsconsole.js").read_text(encoding="utf-8")
    for banned in ("new Function", "eval(", "EventSource", "fetch(", "XMLHttpRequest", "WebSocket",
                   "https://", "location.search", "unpkg", "googleapis"):
        assert banned not in js, banned
    assert "createGatewayBackend" in js
    assert "PROPS.embedded?createGatewayBackend():createMockBackend()" in js
    assert 'status:"rejected"' in js            # commands never act
    assert '"GATEWAY · observed agents"' in js     # no "simulated backend" badge
    css = (DASH / "assets" / "opsconsole.css").read_text(encoding="utf-8")
    assert "@import" not in css and "url(" not in css
    # Static design panels and synthetic energy are hidden when embedded.
    assert '#rbp.readonly .vt[data-view="apg"]' in css
    assert "#rbp.readonly #roster .agent .cell" in css


def test_fleet_frames_console_sandboxed_without_same_origin():
    fleet = (DASH / "assets" / "fleet.js").read_text(encoding="utf-8")
    assert 'frame.src = "/opsconsole"' in fleet
    assert '"sandbox", "allow-scripts allow-downloads"' in fleet
    assert "allow-same-origin" not in fleet
    page = (DASH / "launchpad.html").read_text(encoding="utf-8")
    assert page.index("/assets/opsfeed.js") < page.index("/assets/fleet.js")


NODE_DRIVER = r"""
const fs = require("fs");
const posted = [];
const listeners = [];
global.window = { addEventListener: (t, f) => listeners.push(f), removeEventListener() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const frame = { contentWindow: { postMessage: (m) => posted.push(m) } };
const feed = new window.RoboportOpsFeed.Feed(frame);
const data = (state) => ({
  agents: [{ agent_id: "a1<img>", mission_id: "m1", step_id: "investigate", state }],
  steps: [{ id: "m1:investigate", started_at: "2026-11-10T00:00:00Z" }],
});
feed.update(data("running"));               // not ready: nothing is sent
const before = posted.length;
for (const f of listeners) f({ source: frame.contentWindow, data: { channel: "roboport-ops-console", type: "resync" } });
feed.update(data("failed"));
for (const f of listeners) f({ source: {}, data: { channel: "roboport-ops-console", type: "resync" } }); // foreign source ignored
const envs = posted.flatMap((m) => m.envelopes);
console.log(JSON.stringify({ before, count: posted.length, envs }));
feed.close();
process.exit(0);
"""


def test_feed_derives_envelopes_from_observations(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    driver = tmp_path / "driver.js"
    driver.write_text(NODE_DRIVER, encoding="utf-8")
    out = subprocess.run([node, str(driver), str(DASH / "assets" / "opsfeed.js")],
                         capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(out.stdout)
    assert result["before"] == 0
    assert result["count"] == 2                 # one snapshot on resync, one update
    envs = result["envs"]
    seqs = [e["seq"] for e in envs]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    snap = envs[0]
    assert snap["type"] == "snapshot"
    assert [s["station_id"] for s in snap["data"]["config"]["stations"]] == ["investigate"]
    agent = snap["data"]["agents"][0]
    assert agent["agent_id"] == "a1_img_:m1"    # safe alphabet, one ID per mission
    assert agent["state"] == "working" and agent["task_progress"] == 0
    types = [e["type"] for e in envs[2:]]
    assert "task.failed" in types and "alert.raised" in types
    blob = json.dumps(envs)
    assert "<img" not in blob


def test_control_console_button_opens_fleet_topology():
    control = (REPO / "deploy" / "windows" / "Roboport-Control.ps1").read_text(encoding="utf-8-sig")
    assert "$btnConsole = New-Button 'CONSOLE' $false" in control
    assert "$btnConsole.Add_Click({ Start-Process ($launchPad + '#fleet') })" in control
    assert "$btnConsole.Enabled = $up -and -not $script:job" in control
    js = (DASH / "assets" / "launchpad.js").read_text(encoding="utf-8")
    assert "function applyLocationView()" in js
    assert 'if (view === "Fleet") fleetMode = "topology";' in js
    assert 'window.addEventListener("hashchange"' in js


DUP_DRIVER = r"""
const fs = require("fs");
const posted = [], listeners = [];
global.window = { addEventListener: (t, f) => listeners.push(f), removeEventListener() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const frame = { contentWindow: { postMessage: (m) => posted.push(m) } };
const feed = new window.RoboportOpsFeed.Feed(frame);
const data = { agents: [
  { agent_id: "investigator", mission_id: "aaaa1111", step_id: "investigate", state: "completed" },
  { agent_id: "investigator", mission_id: "bbbb2222", step_id: "investigate", state: "running" }], steps: [] };
feed.update(data);
for (const f of listeners) f({ source: frame.contentWindow, data: { channel: "roboport-ops-console", type: "resync" } });
const n = posted.length;
feed.update(data); feed.update(data);
console.log(JSON.stringify({ snap: posted[0].envelopes[0].data.agents.map((a) => a.agent_id), extra: posted.length - n }));
feed.close(); process.exit(0);
"""


def test_same_agent_in_two_missions_stays_distinct_and_stable(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    driver = tmp_path / "dup.js"
    driver.write_text(DUP_DRIVER, encoding="utf-8")
    out = subprocess.run([node, str(driver), str(DASH / "assets" / "opsfeed.js")],
                         capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(out.stdout)
    assert result["snap"] == ["investigator:aaaa1111", "investigator:bbbb2222"]
    assert result["extra"] == 0                 # unchanged observations post nothing


# ---- console test-run findings (2026-11-11) ----------------------------------

WAVE_DRIVER = r"""
const fs = require("fs");
const posted = [];
const listeners = [];
global.window = { addEventListener: (t, f) => listeners.push(f), removeEventListener() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const frame = { contentWindow: { postMessage: (m) => posted.push(m) } };
const feed = new window.RoboportOpsFeed.Feed(frame);
// A JD crew whose two middle steps share wave 1, observed one after another.
const order = [["scout", 0], ["technical", 1], ["compliance", 1], ["strategy", 2], ["synth", 3]];
const data = {
  agents: order.map(([s], i) => ({ agent_id: "a" + i, mission_id: "m1", step_id: s,
                                   state: s === "compliance" ? "failed" : "completed" })),
  steps: order.map(([s, w]) => ({ id: "m1:" + s, wave: w })),
};
const stations = window.RoboportOpsFeed.model(data).stations;
const legacy = window.RoboportOpsFeed.model({ agents: data.agents, steps: [] }).stations;
feed.update(data);
for (const f of listeners) f({ source: frame.contentWindow, data: { channel: "roboport-ops-console", type: "resync" } });
for (const f of listeners) f({ source: frame.contentWindow, data: { channel: "roboport-ops-console", type: "resync" } });
const snaps = posted.flatMap((m) => m.envelopes).filter((e) => e.type === "snapshot");
console.log(JSON.stringify({
  waves: Object.fromEntries(stations.map((s) => [s.station_id, s.wave])),
  legacy: Object.fromEntries(legacy.map((s) => [s.station_id, s.wave])),
  alerts: snaps.map((s) => s.data.alerts),
}));
feed.close();
process.exit(0);
"""


def run_node(tmp_path, source):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    driver = tmp_path / "driver.js"
    driver.write_text(source, encoding="utf-8")
    out = subprocess.run([node, str(driver), str(DASH / "assets" / "opsfeed.js")],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_dag_places_steps_by_gateway_wave(tmp_path):
    result = run_node(tmp_path, WAVE_DRIVER)
    assert result["waves"] == {"scout": 0, "technical": 1, "compliance": 1, "strategy": 2, "synth": 3}
    # Records without a wave keep the order-based layout.
    assert result["legacy"]["compliance"] == 2


def test_snapshot_keeps_incidents_across_resync(tmp_path):
    alerts = run_node(tmp_path, WAVE_DRIVER)["alerts"]
    assert len(alerts) == 2
    first, second = alerts
    assert [a["target"]["id"] for a in first] == ["compliance"]
    assert first == second                      # same id and time on every resync
    assert first[0]["alert_id"] == "al_a2:m1" and first[0]["kind"] == "critical"


def test_gateway_keeps_step_wave_after_completion():
    from launchpad.events import EventStore

    events = EventStore(store=None)
    events.ingest("m1", {"event": "step.start", "step_id": "technical", "agent": "t", "wave": 1})
    events.ingest("m1", {"event": "step.complete", "step_id": "technical", "agent": "t"})
    events.ingest("m1", {"event": "step.start", "step_id": "odd", "agent": "o", "wave": True})
    assert events.steps["m1:technical"]["wave"] == 1
    assert events.steps["m1:technical"]["state"] == "completed"
    assert events.steps["m1:odd"]["wave"] is None


def test_console_hides_unmeasured_readings_and_rebuilds_incidents():
    js = (DASH / "assets" / "opsconsole.js").read_text(encoding="utf-8")
    assert '(PROPS.embedded?"docked":"charge")' in js       # no "charge 100%" when docked
    assert "(PROPS.embedded?0:a.energy/100)" in js
    assert 'if(!PROPS.embedded)ctx.fillText("ctx "' in js     # token use is not measured
    assert "for(const al of d.alerts)pushIncident(al)" in js  # snapshot incidents survive a reload


def test_launch_form_defaults_match_the_gateway():
    js = (DASH / "assets" / "launchpad.js").read_text(encoding="utf-8")
    assert "context_tokens: 8192" not in js
    assert "context_tokens: 32768" in js
    assert "function contextShortfall(" in js and "contextShortfall(manifest)" in js
    http = (DASH / "launchpad" / "http.py").read_text(encoding="utf-8")
    assert '"context_tokens", 8192' not in http


def test_gateway_records_step_times_from_runtime_timestamps():
    from launchpad.events import EventStore

    events = EventStore(store=None)
    events.ingest("m1", {"event": "step.start", "step_id": "s", "agent": "a", "ts": "2026-11-11T02:09:07Z"})
    assert events.steps["m1:s"]["started_at"] == "2026-11-11T02:09:07Z"
    assert events.steps["m1:s"]["finished_at"] is None
    events.ingest("m1", {"event": "step.failed", "step_id": "s", "agent": "a", "ts": "2026-11-11T02:09:09Z",
                         "error": "x"})
    step = events.steps["m1:s"]
    assert step["started_at"] == "2026-11-11T02:09:07Z" and step["finished_at"] == "2026-11-11T02:09:09Z"
    events.ingest("m1", {"event": "step.start", "step_id": "t", "agent": "b", "ts": "<img src=x>"})
    assert events.steps["m1:t"]["started_at"] is None       # only well-formed timestamps pass


INCIDENT_TIME_DRIVER = r"""
const fs = require("fs");
const posted = [];
const listeners = [];
global.window = { addEventListener: (t, f) => listeners.push(f), removeEventListener() {} };
eval(fs.readFileSync(process.argv[2], "utf8"));
const frame = { contentWindow: { postMessage: (m) => posted.push(m) } };
const feed = new window.RoboportOpsFeed.Feed(frame);
feed.update({
  agents: [{ agent_id: "a", mission_id: "m1", step_id: "s", state: "failed" }],
  steps: [{ id: "m1:s", finished_at: "2026-11-11T02:09:09Z", error: "boom" }],
});
for (const f of listeners) f({ source: frame.contentWindow, data: { channel: "roboport-ops-console", type: "resync" } });
const snap = posted.flatMap((m) => m.envelopes).find((e) => e.type === "snapshot");
console.log(JSON.stringify(snap.data.alerts));
feed.close();
process.exit(0);
"""


def test_incident_time_is_when_the_step_failed(tmp_path):
    alerts = run_node(tmp_path, INCIDENT_TIME_DRIVER)
    assert [a["raised_at"] for a in alerts] == ["2026-11-11T02:09:09Z"]
