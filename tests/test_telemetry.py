"""Phase 4 — prove routing telemetry flows from the Provider seam to the step
result and rolls up per agent, offline via the FaultProvider."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))
from roboport_runtime import executor  # noqa: E402
from fault_provider import FaultProvider  # noqa: E402
from benchmark import routing_summary  # noqa: E402

REGISTRY = {"agents": {"tester": {"path": "agents/core/planner.md",
                                  "model_hint": "any", "deterministic": False}}}

VALID = json.dumps({
    "status": "ok",
    "output": {"generated_at": "t", "summary": {"total_jobs": 3, "verdicts": {"apply": 2}},
               "ranked_matches": []},
    "criteria_results": [{"criterion": "ok", "passed": True}],
    "error": None,
})
INVALID = json.dumps({
    "status": "ok",
    "output": {"generated_at": "t", "ranked_matches": []},
    "criteria_results": [], "error": None,
})


def _usage(model="qwen3:14b", provider="ollama", pt=100, ct=20, cost=0.0, lat=50):
    return {"provider": provider, "model": model, "prompt_tokens": pt,
            "completion_tokens": ct, "cost_usd": cost, "latency_ms": lat}


def _run(monkeypatch, script):
    fp = FaultProvider(script)
    monkeypatch.setattr(executor, "provider", lambda: fp)
    monkeypatch.setattr(executor, "load_agent_spec", lambda *_: "spec")
    monkeypatch.setattr(executor, "_agent_tools_for", lambda *_: [])
    step = {"id": "s1", "owner": "tester", "wave": 0, "input": {},
            "output_type": "FinalReport", "success_criteria": ["produce output"]}
    return executor.call_executor(step, {}, REGISTRY)


def test_step_result_carries_usage(monkeypatch):
    result = _run(monkeypatch, [{"content": VALID, "usage": _usage()}])
    assert result["provider"] == "ollama"
    assert result["model"] == "qwen3:14b"
    assert result["prompt_tokens"] == 100
    assert result["completion_tokens"] == 20
    assert result["cost_usd"] == 0.0
    assert result["latency_ms"] == 50


def test_repair_pass_accumulates_telemetry(monkeypatch):
    """The schema-repair second call's tokens/latency add to the step total."""
    result = _run(monkeypatch, [
        {"content": INVALID, "usage": _usage(pt=100, ct=20, lat=50)},
        {"content": VALID, "usage": _usage(pt=30, ct=10, lat=25)},
    ])
    assert result["repaired"] is True
    assert result["prompt_tokens"] == 130
    assert result["completion_tokens"] == 30
    assert result["latency_ms"] == 75


def test_anthropic_priced_cost_flows_through(monkeypatch):
    result = _run(monkeypatch, [{"content": VALID,
                                 "usage": _usage(provider="anthropic",
                                                 model="claude-opus-4-8",
                                                 pt=1000, ct=500, cost=0.0175, lat=200)}])
    assert result["cost_usd"] == 0.0175
    assert result["provider"] == "anthropic"


def test_unknown_cost_makes_step_cost_none(monkeypatch):
    """If any call's cost is unknown, the step cost is None, not a partial sum."""
    result = _run(monkeypatch, [{"content": VALID, "usage": _usage(cost=None)}])
    assert result["cost_usd"] is None
    assert result["prompt_tokens"] == 100  # tokens still accumulate


def test_missing_usage_is_tolerated(monkeypatch):
    """The fault harness can omit usage entirely — telemetry stays zeroed."""
    result = _run(monkeypatch, [{"content": VALID}])
    assert result["status"] == "ok"
    assert result["prompt_tokens"] == 0
    assert result["cost_usd"] == 0.0


# --- routing_summary rollup ---------------------------------------------------

def test_routing_summary_rolls_up_by_agent():
    steps = [
        {"agent": "job_scout", "llm_calls": 1, "tool_calls": 2, "prompt_tokens": 100,
         "completion_tokens": 20, "cost_usd": 0.01, "latency_ms": 50,
         "provider": "ollama", "model": "qwen3:14b"},
        {"agent": "job_scout", "llm_calls": 1, "tool_calls": 0, "prompt_tokens": 50,
         "completion_tokens": 10, "cost_usd": 0.005, "latency_ms": 30,
         "provider": "ollama", "model": "qwen3:14b"},
    ]
    summ = routing_summary(steps)
    js = summ["by_agent"][0]
    assert js["agent"] == "job_scout"
    assert js["steps"] == 2
    assert js["llm_calls"] == 2 and js["tool_calls"] == 2
    assert js["prompt_tokens"] == 150 and js["completion_tokens"] == 30
    assert js["latency_ms"] == 80
    assert js["cost_usd"] == 0.015
    assert js["providers"] == ["ollama"] and js["models"] == ["qwen3:14b"]
    assert summ["totals"]["cost_usd"] == 0.015


def test_routing_summary_unknown_cost_poisons_total():
    steps = [
        {"agent": "a", "llm_calls": 1, "cost_usd": 0.01, "provider": "ollama"},
        {"agent": "b", "llm_calls": 0, "cost_usd": None, "provider": None},
    ]
    summ = routing_summary(steps)
    assert summ["totals"]["cost_usd"] is None  # any unknown -> total unknown
    by_agent = {r["agent"]: r for r in summ["by_agent"]}
    assert by_agent["b"]["cost_usd"] is None
    assert by_agent["a"]["cost_usd"] == 0.01


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
