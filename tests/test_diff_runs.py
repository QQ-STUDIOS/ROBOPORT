"""Phase-1 acceptance gates for scripts/diff_runs.py (see docs/ROADMAP.md)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import diff_runs  # noqa: E402

RUNS = REPO / "tests" / "fixtures" / "runs"


def _diff(base: str, cand: str) -> dict:
    return diff_runs.diff_runs(diff_runs.Run(RUNS / base), diff_runs.Run(RUNS / cand))


def test_self_compare_is_pass():
    """Gate 1: comparing a run to itself returns verdict: pass."""
    env = _diff("base", "base")
    assert env["verdict"] == "pass"
    assert env["agent_diffs"] == []


def test_equivalent_run_is_pass():
    env = _diff("base", "cand_pass")
    assert env["verdict"] == "pass"


def test_schema_regression():
    """Gate 2: removing a required field from candidate final_output -> regression."""
    env = _diff("base", "cand_schema")
    assert env["verdict"] == "regression"
    assert env["summary"]["schema_regressions"] == 1
    kinds = {s["kind"] for d in env["agent_diffs"] for s in d["signals"]}
    assert "schema_invalid" in kinds
    # attributed to the agent that owns the FinalReport contract
    assert any(d["agent"] == "synthesizer" for d in env["agent_diffs"])


def test_new_blocker_failure_is_regression():
    """Gate 3: a new blocker failure in grading.json -> regression."""
    env = _diff("base", "cand_blocker")
    assert env["verdict"] == "regression"
    assert env["summary"]["new_blocker_failures"] >= 1
    assert any(s["kind"] == "blocker_failed"
               for d in env["agent_diffs"] for s in d["signals"])


def test_added_cost_without_quality_loss_is_warning():
    """Gate 4: extra llm/tool calls without blocker loss -> warning, not regression."""
    env = _diff("base", "cand_cost")
    assert env["verdict"] == "warning"
    assert env["summary"]["cost_delta_llm_calls"] == 2
    assert env["summary"]["new_blocker_failures"] == 0


def test_output_is_deterministic():
    """Gate 5: same inputs -> byte-stable JSON."""
    a = json.dumps(_diff("base", "cand_schema"), indent=2, sort_keys=True)
    b = json.dumps(_diff("base", "cand_schema"), indent=2, sort_keys=True)
    assert a == b


def test_different_goal_is_inconclusive(tmp_path):
    """Comparability gate: different goal/input -> inconclusive."""
    other = tmp_path / "other"
    other.mkdir()
    plan = json.loads((RUNS / "base" / "plan.json").read_text())
    plan["goal"] = "a completely different task"
    (other / "plan.json").write_text(json.dumps(plan))
    (other / "run.log").write_text((RUNS / "base" / "run.log").read_text())
    (other / "final_output.json").write_text((RUNS / "base" / "final_output.json").read_text())
    env = diff_runs.diff_runs(diff_runs.Run(RUNS / "base"), diff_runs.Run(other))
    assert env["verdict"] == "inconclusive"


def test_exit_codes(tmp_path):
    """Exit-code contract: regression -> 1 by default; --fail-on warning escalates."""
    assert diff_runs.main(["--baseline", str(RUNS / "base"),
                           "--candidate", str(RUNS / "base"), "--quiet"]) == 0
    assert diff_runs.main(["--baseline", str(RUNS / "base"),
                           "--candidate", str(RUNS / "cand_schema"), "--quiet"]) == 1
    # warning passes by default...
    assert diff_runs.main(["--baseline", str(RUNS / "base"),
                           "--candidate", str(RUNS / "cand_cost"), "--quiet"]) == 0
    # ...but fails when the threshold is lowered
    assert diff_runs.main(["--baseline", str(RUNS / "base"),
                           "--candidate", str(RUNS / "cand_cost"),
                           "--fail-on", "warning", "--quiet"]) == 1


def _write_min_run(d, *, duration_ms, config_fp, llm_calls=1):
    """Minimal run dir: one synthesizer step with the given latency/fingerprint."""
    d.mkdir(parents=True, exist_ok=True)
    plan = {"goal": "g", "steps": [{"id": "synth", "owner": "synthesizer", "wave": 0,
                                    "output_type": "object", "success_criteria": ["ok"]}]}
    (d / "plan.json").write_text(json.dumps(plan))
    sd = {"event": "step_done", "step_id": "synth", "status": "ok",
          "criteria_results": [{"criterion": "ok", "passed": True}],
          "tool_calls": 0, "llm_calls": llm_calls,
          "duration_ms": duration_ms, "config_fp": config_fp}
    (d / "run.log").write_text(json.dumps(sd) + "\n")


def test_material_latency_increase_is_warning(tmp_path):
    base = tmp_path / "b"; cand = tmp_path / "c"
    _write_min_run(base, duration_ms=1000, config_fp="fp1")
    _write_min_run(cand, duration_ms=2000, config_fp="fp1")
    env = diff_runs.diff_runs(diff_runs.Run(base), diff_runs.Run(cand))
    assert env["verdict"] == "warning"
    assert env["summary"]["latency_delta_ms"] == 1000
    assert any(s["kind"] == "latency_increase"
               for d in env["agent_diffs"] for s in d["signals"])


def test_small_latency_jitter_is_ignored(tmp_path):
    base = tmp_path / "b"; cand = tmp_path / "c"
    _write_min_run(base, duration_ms=1000, config_fp="fp1")
    _write_min_run(cand, duration_ms=1100, config_fp="fp1")  # +100ms, below threshold
    env = diff_runs.diff_runs(diff_runs.Run(base), diff_runs.Run(cand))
    assert env["verdict"] == "pass"


def test_config_drift_is_info_not_regression(tmp_path):
    base = tmp_path / "b"; cand = tmp_path / "c"
    _write_min_run(base, duration_ms=1000, config_fp="fp_ollama")
    _write_min_run(cand, duration_ms=1000, config_fp="fp_anthropic")  # different model/route
    env = diff_runs.diff_runs(diff_runs.Run(base), diff_runs.Run(cand))
    assert env["verdict"] == "pass"  # config drift alone doesn't fail the build
    assert any(s["kind"] == "config_changed"
               for d in env["agent_diffs"] for s in d["signals"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
