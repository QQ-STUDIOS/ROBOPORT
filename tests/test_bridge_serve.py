"""The Windows supervisor's Launch Pad command has to stay up on loopback."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dashboard"))
import bridge  # noqa: E402


def _bind(monkeypatch, argv):
    bound = {}

    class FakeServer:
        def __init__(self, address, handler):
            bound["address"] = address

        def serve_forever(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(bridge, "HTTPServer", FakeServer)
    monkeypatch.setattr(sys, "argv", argv)
    bridge.main()
    return bound["address"]


def test_empty_telemetry_stays_up_on_the_requested_host(tmp_path, monkeypatch, capsys):
    address = _bind(monkeypatch, [
        "bridge.py", "--runs-dir", str(tmp_path), "--host", "127.0.0.1", "--port", "4242",
        "--data-root", str(tmp_path / "data"), "--policy", str(tmp_path / "policy.yaml"),
    ])
    assert address == ("127.0.0.1", 4242)
    assert "snapshot-only" in capsys.readouterr().out


def test_omitted_host_keeps_the_existing_bind(tmp_path, monkeypatch):
    assert _bind(monkeypatch, ["bridge.py", "--runs-dir", str(tmp_path)]) == ("0.0.0.0", 4242)
