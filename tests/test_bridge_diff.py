"""Phase-2: diff_runs output -> Ops Console envelopes (dashboard/bridge.py)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "dashboard"))
import diff_runs  # noqa: E402
import bridge  # noqa: E402

RUNS = REPO / "tests" / "fixtures" / "runs"


def _diff(base: str, cand: str) -> dict:
    return diff_runs.diff_runs(diff_runs.Run(RUNS / base), diff_runs.Run(RUNS / cand))


def _valid_envelope(e: dict) -> bool:
    return e.get("v") == 1 and isinstance(e.get("type"), str) and isinstance(e.get("data"), dict)


def test_snapshot_is_first_and_all_envelopes_valid():
    envs = bridge.diff_to_envelopes(_diff("base", "cand_schema"))
    assert envs[0]["type"] == "snapshot"
    assert all(_valid_envelope(e) for e in envs)
    assert any(e["type"] == "log.appended" and "RUN DIFF" in e["data"]["html"] for e in envs)


def test_regression_raises_critical_alert_on_owning_station():
    envs = bridge.diff_to_envelopes(_diff("base", "cand_schema"))
    alerts = [e["data"] for e in envs if e["type"] == "alert.raised"]
    crit = [a for a in alerts if a["kind"] == "critical"]
    assert crit, "schema regression should raise a critical alert"
    # attributed to the station that owns the FinalReport contract
    assert any(a["target"].get("id") == "synthesizer" for a in crit)


def test_warning_diff_raises_amber_not_critical():
    envs = bridge.diff_to_envelopes(_diff("base", "cand_cost"))
    kinds = {e["data"]["kind"] for e in envs if e["type"] == "alert.raised"}
    assert "warning" in kinds
    assert "critical" not in kinds


def test_clean_diff_has_no_alerts():
    envs = bridge.diff_to_envelopes(_diff("base", "base"))
    assert not [e for e in envs if e["type"] == "alert.raised"]
    assert any("no differences detected" in e["data"].get("html", "")
               for e in envs if e["type"] == "log.appended")


def test_load_or_compute_diff_from_run_dirs():
    diff = bridge.load_or_compute_diff(None, str(RUNS / "base"), str(RUNS / "cand_schema"))
    assert diff["verdict"] == "regression"


def test_load_diff_from_file(tmp_path):
    import json
    f = tmp_path / "d.json"
    f.write_text(json.dumps(_diff("base", "cand_cost")))
    diff = bridge.load_or_compute_diff(str(f), None, None)
    assert diff["verdict"] == "warning"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
