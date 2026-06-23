"""Phase 3 — prove the error stack fires, offline, via the FaultProvider.

Each test injects a fault on the Provider seam and asserts the executor's
documented behavior: retry at the call layer, repair on schema-invalid output,
fail loudly on exhaustion, and never pass on a quiet-200 empty result.
"""
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

REGISTRY = {"agents": {"tester": {"path": "agents/core/planner.md",
                                  "model_hint": "any", "deterministic": False}}}

VALID = json.dumps({
    "status": "ok",
    "output": {"generated_at": "t", "summary": {"total_jobs": 3, "verdicts": {"apply": 2}},
               "ranked_matches": []},
    "criteria_results": [{"criterion": "ok", "passed": True}],
    "error": None,
})
# missing required `summary` -> fails FinalReport schema
INVALID = json.dumps({
    "status": "ok",
    "output": {"generated_at": "t", "ranked_matches": []},
    "criteria_results": [], "error": None,
})


TOOL_CALL = [{"id": "c0", "name": "x", "arguments": {}}]


def _run(monkeypatch, script, *, output_type="FinalReport", config=None):
    fp = FaultProvider(script)
    monkeypatch.setattr(executor, "provider", lambda: fp)
    monkeypatch.setattr(executor, "load_agent_spec", lambda *_: "spec")
    monkeypatch.setattr(executor, "_agent_tools_for", lambda *_: [])
    if config is not None:
        monkeypatch.setattr(executor, "_agent_config", lambda: config)
    step = {"id": "s1", "owner": "tester", "wave": 0, "input": {},
            "output_type": output_type, "success_criteria": ["produce output"]}
    return executor.call_executor(step, {}, REGISTRY), fp


def _failed_criteria(result):
    return [c for c in result["criteria_results"] if not c["passed"]]


def test_transient_then_success_retries(monkeypatch):
    """Layer 1: a transient 5xx is retried, then the step succeeds."""
    result, _ = _run(monkeypatch, [{"transient": True}, {"content": VALID}])
    assert result["status"] == "ok"
    assert result["retries"] == 1
    assert not _failed_criteria(result)


def test_persistent_transient_fails_loudly(monkeypatch):
    """Layer 1 exhausted: persistent 5xx fails with the call-level layer named."""
    result, _ = _run(monkeypatch, [{"transient": True}])  # repeats
    assert result["status"] == "failed"
    assert result["layer"] == "provider_5xx"
    assert "transient" in result["error"]


def test_fatal_provider_error_fails(monkeypatch):
    """A non-retryable provider error fails immediately (not retried)."""
    result, fp = _run(monkeypatch, [{"fatal": True}])
    assert result["status"] == "failed"
    assert result["layer"] == "provider_error"
    assert fp.calls == 1  # no retries on a fatal error


def test_schema_invalid_is_repaired(monkeypatch):
    """Layer 1: schema-invalid output triggers one repair pass that fixes it."""
    result, _ = _run(monkeypatch, [{"content": INVALID}, {"content": VALID}])
    assert result["status"] == "ok"
    assert result["repaired"] is True
    assert not _failed_criteria(result)


def test_schema_invalid_persists_is_flagged(monkeypatch):
    """Repair attempted but output still invalid -> recorded, not silently passed."""
    result, _ = _run(monkeypatch, [{"content": INVALID}, {"content": INVALID}])
    assert result["repaired"] is True
    assert any("schema" in c["criterion"] for c in _failed_criteria(result))


def test_quiet_200_empty_list_is_flagged(monkeypatch):
    """The forbidden quiet-200: an empty search result must fail loudly."""
    empty = json.dumps({"status": "ok", "output": [], "criteria_results": [], "error": None})
    result, _ = _run(monkeypatch, [{"content": empty}], output_type="list[Job]")
    assert any("quiet-200" in c["criterion"] for c in _failed_criteria(result))


def test_llm_budget_exceeded_aborts(monkeypatch):
    """Layer 2: too many LLM calls aborts loudly with layer=budget_exceeded."""
    cfg = {"budgets": {"per_agent": {"max_llm_calls": 2, "max_tool_calls": 50}}}
    result, _ = _run(monkeypatch, [{"tool_calls": TOOL_CALL}], config=cfg)  # loops on tool calls
    assert result["status"] == "failed"
    assert result["layer"] == "budget_exceeded"
    assert "llm calls" in result["error"]


def test_tool_budget_exceeded_aborts(monkeypatch):
    """Layer 2: too many tool calls aborts loudly with layer=budget_exceeded."""
    cfg = {"budgets": {"per_agent": {"max_llm_calls": 10, "max_tool_calls": 1}}}
    result, _ = _run(monkeypatch, [{"tool_calls": TOOL_CALL}], config=cfg)
    assert result["status"] == "failed"
    assert result["layer"] == "budget_exceeded"
    assert "tool calls" in result["error"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
