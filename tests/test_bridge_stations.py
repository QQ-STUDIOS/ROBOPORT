"""Generic per-crew dashboard: stations derive from the registry crew, not a
hardcoded JD list, so any crew renders on the Ops Console."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "dashboard"))
sys.path.insert(0, str(REPO / "scripts"))
import bridge  # noqa: E402

REG = json.loads((REPO / "agents" / "registry.json").read_text())


def test_jd_layout_derived_from_registry():
    st = bridge.derive_stations("jd_crew", REG)
    ids = [s["station_id"] for s in st]
    assert ids[0] == "job_scout"                       # entry at wave 0
    wave = {s["station_id"]: s["wave"] for s in st}
    assert wave["job_scout"] == 0
    assert wave["technical_analyst"] == 1 and wave["compliance_risk"] == 1
    assert wave["application_strategist"] == 2
    assert wave["synthesizer"] == 3
    # the three terminal fan-out agents are optional (every incoming edge optional)
    optional = {s["station_id"] for s in st if s.get("optional")}
    assert optional == {"salary_estimator", "resume_tailor", "cover_letter_writer"}


def test_dataops_layout_generalizes():
    st = bridge.derive_stations("dataops_crew", REG)
    assert [s["station_id"] for s in st] == ["data_engineering", "reporting"]
    assert [s["wave"] for s in st] == [0, 1]
    assert not any(s.get("optional") for s in st)      # no optional stations


def test_unknown_crew_falls_back_to_jd():
    st = bridge.derive_stations("no_such_crew", REG)
    assert st[0]["station_id"] == "job_scout"          # the JD fallback renders


def test_snapshot_is_crew_aware():
    snap = bridge.make_snapshot("r1", crew="dataops_crew")
    assert snap["config"]["crew"] == "dataops_crew"
    assert [s["station_id"] for s in snap["config"]["stations"]] == ["data_engineering", "reporting"]


def test_convert_run_detects_crew_from_run_log(tmp_path):
    run_dir = tmp_path / "dataops_run"
    run_dir.mkdir()
    (run_dir / "run.log").write_text("\n".join([
        json.dumps({"event": "run.start", "run_id": "dataops_run", "crew": "dataops_crew"}),
        json.dumps({"event": "run.complete", "run_summary": {"steps": 2}}),
    ]))
    envelopes = bridge.convert_run(run_dir)
    snap = next(e["data"] for e in envelopes if e["type"] == "snapshot")
    assert snap["config"]["crew"] == "dataops_crew"
    assert {s["station_id"] for s in snap["stations"]} == {"data_engineering", "reporting"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
