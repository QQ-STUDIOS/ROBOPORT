"""Verify-Roboport.ps1 depends on scripts/deployment_report.py."""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import deployment_report  # noqa: E402

EXAMPLE = json.loads((REPO / "deploy/windows/deployment.example.json").read_text(encoding="utf-8"))


class _Quiet(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


@pytest.fixture
def surfaces():
    servers = []
    ports = []
    for _ in range(2):
        httpd = HTTPServer(("127.0.0.1", 0), _Quiet)
        ports.append(httpd.server_address[1])
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
    yield tuple(ports)
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def build_install(tmp_path, ports, *, escape=False, machine="BOX"):
    install = tmp_path / "install"
    release = (tmp_path / "outside" / "rel") if escape else (install / "releases" / "1")
    data = install / "data"
    (release / "dashboard").mkdir(parents=True)
    (release / "dashboard" / "bridge.py").write_text("# bridge\n", encoding="utf-8")
    scripts = release / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")
    (data / "launchpad").mkdir(parents=True)
    policy = {"schema_version": 1, "environment_id": machine, "expected_machine": machine, "profiles": {}}
    (data / "launchpad" / "policy.yaml").write_text(yaml.safe_dump(policy), encoding="utf-8")
    config = dict(EXAMPLE)
    config["expected_machine"] = machine
    config["ops_port"], config["feed_port"] = ports
    manifest = {"schema_version": 1, "release_root": str(release.resolve()), "data_root": str(data.resolve()), "config": config}
    (install / "state").mkdir(parents=True)
    (install / "state" / "deployment.json").write_text(json.dumps(manifest), encoding="utf-8")
    return install, data


def test_live_pass_is_pending_until_recovery_and_rollback(tmp_path, surfaces, capsys):
    install, data = build_install(tmp_path, surfaces)
    assert deployment_report.main(["--install", str(install)]) == 2
    report = json.loads((data / "deployment-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "pending"
    assert report["pending"] == ["recovery", "rollback"]
    assert all(check["ok"] for check in report["checks"])
    assert "Pending observations" in capsys.readouterr().out


def test_recorded_observations_complete_verification(tmp_path, surfaces):
    install, data = build_install(tmp_path, surfaces)
    state = install / "state"
    (state / "startup-task.json").write_text(json.dumps({"task_name": "Roboport-test"}), encoding="utf-8")
    (state / "rolled-back-deployment.json").write_text(json.dumps({"release_root": "previous"}), encoding="utf-8")
    (state / "processes.json").write_text(json.dumps({"supervisor": {"pid": 1}}), encoding="utf-8")
    flags = ["--install", str(install), "--restart-check", "--recovery-check", "--rollback-check"]
    assert deployment_report.main(flags) == 0
    report = json.loads((data / "deployment-report.json").read_text(encoding="utf-8"))
    assert report["observations"] == {"restart": True, "recovery": True, "rollback": True}
    assert deployment_report.main(["--install", str(install)]) == 0


def _closed_ports():
    ports = []
    for _ in range(2):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            ports.append(sock.getsockname()[1])
    return tuple(ports)


def test_down_launch_pad_fails_and_still_writes_the_report(tmp_path, capsys):
    install, data = build_install(tmp_path, _closed_ports())
    assert deployment_report.main(["--install", str(install)]) == 1
    report = json.loads((data / "deployment-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert any(check["name"] == "launch_pad" and not check["ok"] for check in report["checks"])
    captured = capsys.readouterr().out
    assert "FAIL launch_pad:" in captured


def test_release_outside_the_install_fails(tmp_path, surfaces):
    install, data = build_install(tmp_path, surfaces, escape=True)
    assert deployment_report.main(["--install", str(install)]) == 1
    report = json.loads((data / "deployment-report.json").read_text(encoding="utf-8"))
    failed = {check["name"] for check in report["checks"] if not check["ok"]}
    assert "release_path" in failed
