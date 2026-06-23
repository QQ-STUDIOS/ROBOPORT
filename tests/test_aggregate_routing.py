"""Phase 4 task 3 — prove aggregate's routing report: cost/latency per passing
run, by-provider/model with blocker pass rate, and per-agent regression flags."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import aggregate  # noqa: E402


def _step(provider="ollama", model="qwen3:14b", cost=0.0, latency=100,
          pt=80, ct=20, llm=1, tool=2):
    return {"event": "step_done", "provider": provider, "model": model,
            "cost_usd": cost, "latency_ms": latency, "prompt_tokens": pt,
            "completion_tokens": ct, "llm_calls": llm, "tool_calls": tool}


def test_rollup_passing_runs_and_blocker_rate():
    runs = [
        {"eval_id": "e1", "run": "run_1", "completed": True,
         "grading": {"blocker_failed": False},
         "steps": [_step(latency=100, cost=0.01), _step(latency=50, cost=0.005)]},
        {"eval_id": "e1", "run": "run_2", "completed": True,
         "grading": {"blocker_failed": True},          # not passing
         "steps": [_step(latency=300, cost=0.02)]},
    ]
    out = aggregate.routing_rollup(runs)

    # Only run_1 passes -> its latency 150 is the passing total.
    assert out["passing_runs"]["n"] == 1
    assert out["passing_runs"]["latency_ms"]["total"] == 150
    assert out["passing_runs"]["cost_usd"]["total"] == 0.015

    # One provider/model across 2 runs, 2 graded, 1 blocker fail -> 0.5.
    pm = out["by_provider_model"][0]
    assert (pm["provider"], pm["model"]) == ("ollama", "qwen3:14b")
    assert pm["runs"] == 2 and pm["steps"] == 3
    assert pm["blocker_pass_rate"] == 0.5

    by_run = {r["run"]: r for r in out["per_run"]}
    assert by_run["run_1"]["passing"] is True
    assert by_run["run_2"]["passing"] is False


def test_rollup_unknown_cost_poisons_scope():
    runs = [{"eval_id": "e1", "run": "run_1", "completed": True,
             "grading": {"blocker_failed": False},
             "steps": [_step(cost=0.01), _step(cost=None)]}]  # unknown cost
    out = aggregate.routing_rollup(runs)
    assert out["per_run"][0]["cost_usd"] is None
    assert out["passing_runs"]["cost_usd"]["total"] is None
    assert out["by_provider_model"][0]["cost_usd"] is None
    # latency is unaffected by unknown cost.
    assert out["passing_runs"]["latency_ms"]["total"] == 200


def test_incomplete_run_is_not_passing():
    runs = [{"eval_id": "e1", "run": "run_1", "completed": False,
             "grading": None, "steps": [_step()]}]
    out = aggregate.routing_rollup(runs)
    assert out["passing_runs"]["n"] == 0
    assert out["per_run"][0]["passing"] is False


# --- routing_deltas -----------------------------------------------------------

def _summary(by_agent):
    return {"routing": {"by_agent": by_agent}}


def test_routing_deltas_flags_latency_regression():
    base = _summary([{"agent": "job_scout", "steps": 2, "latency_ms": 200, "cost_usd": 0.0}])
    cand = _summary([{"agent": "job_scout", "steps": 2, "latency_ms": 600, "cost_usd": 0.0}])
    d = aggregate.routing_deltas(base, cand)[0]
    assert d["baseline_latency_ms_mean"] == 100.0
    assert d["candidate_latency_ms_mean"] == 300.0
    assert d["latency_delta_pct"] == 2.0  # +200%
    assert "latency_regression" in d["flags"]


def test_routing_deltas_ignores_tiny_absolute_latency():
    # +50% but only +10ms per step -> below the absolute floor, not flagged.
    base = _summary([{"agent": "a", "steps": 1, "latency_ms": 20, "cost_usd": 0.0}])
    cand = _summary([{"agent": "a", "steps": 1, "latency_ms": 30, "cost_usd": 0.0}])
    d = aggregate.routing_deltas(base, cand)[0]
    assert d["flags"] == []


def test_routing_deltas_flags_cost_regression():
    base = _summary([{"agent": "syn", "steps": 1, "latency_ms": 10, "cost_usd": 0.01}])
    cand = _summary([{"agent": "syn", "steps": 1, "latency_ms": 10, "cost_usd": 0.05}])
    d = aggregate.routing_deltas(base, cand)[0]
    assert "cost_regression" in d["flags"]


def test_routing_deltas_unknown_cost_not_flagged():
    base = _summary([{"agent": "a", "steps": 1, "latency_ms": 10, "cost_usd": None}])
    cand = _summary([{"agent": "a", "steps": 1, "latency_ms": 10, "cost_usd": None}])
    d = aggregate.routing_deltas(base, cand)[0]
    assert d["cost_delta_pct"] is None and d["flags"] == []


# --- filesystem integration ---------------------------------------------------

def test_report_benchmark_includes_routing(tmp_path):
    run_dir = tmp_path / "eval_e1" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "final_output.json").write_text("{}")
    (run_dir / "grading.json").write_text(json.dumps(
        {"pass_rate": 1.0, "blocker_failed": False, "results": [], "meta_critique": []}))
    (run_dir / "run.log").write_text(
        json.dumps({"event": "plan_emitted"}) + "\n"
        + json.dumps(_step(latency=120, cost=0.0)) + "\n")

    report = aggregate.report_benchmark(tmp_path)
    assert "routing" in report
    assert report["routing"]["passing_runs"]["n"] == 1
    assert report["routing"]["passing_runs"]["latency_ms"]["total"] == 120
    assert report["routing"]["by_provider_model"][0]["provider"] == "ollama"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
