"""Prove the offline fault-injection benchmark: it drives the real executor via
the FaultProvider, grades the error-stack outcome, and writes run artifacts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import fault_bench  # noqa: E402


# --- grade() -----------------------------------------------------------------

def test_grade_passes_on_match():
    result = {"status": "ok", "retries": 1, "criteria_results": []}
    ok, mism = fault_bench.grade(result, calls=2, expect={"status": "ok", "retries": 1})
    assert ok and mism == []


def test_grade_flags_status_mismatch():
    ok, mism = fault_bench.grade({"status": "ok"}, 1, {"status": "failed"})
    assert not ok and any("status" in m for m in mism)


def test_grade_error_and_criterion_substrings():
    result = {"status": "failed", "error": "too many llm calls",
              "criteria_results": [{"criterion": "schema:FinalReport", "passed": False}]}
    ok, _ = fault_bench.grade(result, 1, {"error_contains": "llm calls",
                                          "failed_criterion_contains": "schema"})
    assert ok
    bad, mism = fault_bench.grade(result, 1, {"failed_criterion_contains": "quiet-200"})
    assert not bad and any("quiet-200" in m for m in mism)


def test_grade_layer_not():
    ok, _ = fault_bench.grade({"layer": "budget_exceeded"}, 1, {"layer_not": "unsafe_action"})
    assert ok
    bad, _ = fault_bench.grade({"layer": "unsafe_action"}, 1, {"layer_not": "unsafe_action"})
    assert not bad


# --- run_suite over the real scenario set ------------------------------------

def test_suite_all_scenarios_pass(tmp_path):
    scenarios = json.loads((REPO / "evals" / "fault_injection.json").read_text())["scenarios"]
    summary = fault_bench.run_suite(scenarios, tmp_path)
    assert summary["ok"], [r for r in summary["scenarios"] if not r["passed"]]
    assert summary["passed"] == summary["total"] == len(scenarios)


def test_suite_writes_diffable_artifacts(tmp_path):
    scenarios = json.loads((REPO / "evals" / "fault_injection.json").read_text())["scenarios"]
    fault_bench.run_suite(scenarios, tmp_path)
    run_dir = tmp_path / "eval_unsafe_action_escalates" / "run_1"
    assert (run_dir / "final_output.json").exists()
    assert (run_dir / "fault_verdict.json").exists()
    step_done = json.loads((run_dir / "run.log").read_text().splitlines()[0])
    assert step_done["event"] == "step_done"
    assert step_done["layer"] == "unsafe_action"
    assert isinstance(step_done["config_fp"], str) and len(step_done["config_fp"]) >= 8


def test_suite_actually_checks_expectations(tmp_path):
    """A scenario whose declared expectation is wrong must FAIL — proving the
    runner grades, not rubber-stamps."""
    broken = [{
        "id": "wrong_expectation", "layer": "L1-call",
        "step": {"owner": "tester", "output_type": "FinalReport"},
        "fault_script": [{"fatal": True}],
        "expect": {"status": "ok"},  # a fatal error does NOT produce status ok
    }]
    summary = fault_bench.run_suite(broken, tmp_path)
    assert summary["ok"] is False
    assert summary["scenarios"][0]["passed"] is False


def test_executor_seams_restored_after_suite(tmp_path):
    """The runner swaps executor seams per scenario and must restore them."""
    from roboport_runtime import executor
    before = {s: getattr(executor, s) for s in fault_bench._SEAMS}
    fault_bench.run_suite([{
        "id": "x", "step": {"owner": "tester", "output_type": "FinalReport"},
        "fault_script": [{"fatal": True}], "expect": {"status": "failed"},
    }], tmp_path)
    after = {s: getattr(executor, s) for s in fault_bench._SEAMS}
    assert before == after


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
